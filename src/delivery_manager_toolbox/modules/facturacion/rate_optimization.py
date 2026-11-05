"""Selección y consolidación de tarifas para las horas de gestión."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
import math

from delivery_manager_toolbox.modules.facturacion.configuration import BillingConfiguration
from delivery_manager_toolbox.modules.facturacion.models import (
    AccountBillingSummary,
    AllocationStatus,
    ManagementAllocationResult,
    ManagementAssignment,
    ManagerBillingSummary,
    RateBillingSummary,
    RateIncidentCode,
    RateOptimizationIncident,
    RateOptimizationResult,
    RateSource,
    SheetAnalysis,
)
from delivery_manager_toolbox.modules.facturacion.validation import normalize_identifier


_FLOAT_TOLERANCE = 1e-9


def optimize_management_rates(
    analysis: SheetAnalysis,
    allocation: ManagementAllocationResult | None,
    configuration: BillingConfiguration,
) -> RateOptimizationResult | None:
    """Aplica una tarifa única por cuenta sin modificar ninguna hora asignada."""
    if allocation is None:
        return None

    configured_rates = _configured_rates(configuration)
    detected_rates = {
        normalize_identifier(item.account_code): tuple(sorted(set(item.rates)))
        for item in analysis.accounts
    }
    incidents: list[RateOptimizationIncident] = []
    rated: list[ManagementAssignment] = []
    missing_rate_accounts: set[str] = set()

    for assignment in allocation.assignments:
        account_key = normalize_identifier(assignment.account_code)
        if account_key in configured_rates:
            rate = configured_rates[account_key]
            source = RateSource.CONFIGURED_BLOCK
        else:
            candidates = detected_rates.get(account_key, ())
            rate = max(candidates) if candidates else None
            source = (
                RateSource.OPTIMIZED_RATE
                if len(candidates) > 1
                else RateSource.ACCOUNT_RATE if candidates
                else None
            )
        if rate is None:
            status = RateIncidentCode.NO_RATE_FOUND.value
            amount = None
            if assignment.account_code not in missing_rate_accounts:
                incidents.append(RateOptimizationIncident(
                    code=RateIncidentCode.NO_RATE_FOUND,
                    account_code=assignment.account_code,
                    message=f"No se encontró una tarifa válida para {assignment.account_code}.",
                ))
                missing_rate_accounts.add(assignment.account_code)
        else:
            status = "OK"
            amount = assignment.management_hours * rate
        rated.append(replace(
            assignment,
            management_rate=rate,
            rate_source=source,
            amount=amount,
            rate_status=status,
        ))

    consolidated = consolidate_rated_assignments(rated)
    incidents.extend(_multiple_rate_incidents(consolidated))
    incidents.extend(_tolerance_incidents(allocation))
    _validate_optimized_hours(allocation.assignments, consolidated)
    manager_summaries = _build_manager_billing_summaries(consolidated)
    account_summaries = _build_account_billing_summaries(consolidated)
    billing_summary = _build_rate_billing_summary(consolidated, account_summaries, incidents)
    return RateOptimizationResult(
        sheet_name=analysis.sheet_name,
        assignments=consolidated,
        manager_summaries=manager_summaries,
        account_summaries=account_summaries,
        billing_summary=billing_summary,
        incidents=tuple(incidents),
        consolidated_record_count=len(rated) - len(consolidated),
    )


def consolidate_rated_assignments(
    assignments: Iterable[ManagementAssignment],
) -> tuple[ManagementAssignment, ...]:
    """Consolida gestor, cuenta y tarifa preservando horas e importes."""
    merged: dict[tuple[str, str, str, float | None], ManagementAssignment] = {}
    for item in assignments:
        key = (item.sheet_name, item.employee_id, item.account_code, item.management_rate)
        if key not in merged:
            merged[key] = item
            continue
        previous = merged[key]
        merged[key] = replace(
            previous,
            management_hours=previous.management_hours + item.management_hours,
            amount=(
                (previous.amount or 0.0) + (item.amount or 0.0)
                if previous.amount is not None and item.amount is not None
                else None
            ),
        )
    return tuple(sorted(
        merged.values(),
        key=lambda item: (
            normalize_identifier(item.manager_name),
            normalize_identifier(item.account_code),
            item.management_rate if item.management_rate is not None else math.inf,
        ),
    ))


def _configured_rates(configuration: BillingConfiguration) -> dict[str, float]:
    return {
        normalize_identifier(account): block.management_rate
        for block in configuration.management_rate_blocks
        for account in block.accounts
    }


def _multiple_rate_incidents(
    assignments: Iterable[ManagementAssignment],
) -> tuple[RateOptimizationIncident, ...]:
    rates: dict[tuple[str, str], set[float | None]] = defaultdict(set)
    names: dict[tuple[str, str], str] = {}
    for item in assignments:
        key = (item.employee_id, item.account_code)
        rates[key].add(item.management_rate)
        names[key] = item.manager_name
    return tuple(
        RateOptimizationIncident(
            code=RateIncidentCode.MULTIPLE_RATE_EXCEPTION,
            account_code=account_code,
            manager_name=names[(employee_id, account_code)],
            message=f"{names[(employee_id, account_code)]} conserva varias tarifas para {account_code}.",
        )
        for (employee_id, account_code), values in rates.items()
        if len(values) > 1
    )


def _tolerance_incidents(
    allocation: ManagementAllocationResult,
) -> tuple[RateOptimizationIncident, ...]:
    return tuple(
        RateOptimizationIncident(
            code=RateIncidentCode.OUTSIDE_TOLERANCE_AFTER_OPTIMIZATION,
            manager_name=item.manager_name,
            message=f"{item.manager_name} permanece fuera de la tolerancia ±10 %.",
        )
        for item in allocation.manager_summaries
        if item.status == AllocationStatus.OUTSIDE_TOLERANCE
    )


def _validate_optimized_hours(
    original: Iterable[ManagementAssignment],
    optimized: Iterable[ManagementAssignment],
) -> None:
    original_records = tuple(original)
    optimized_records = tuple(optimized)
    original_by_account = _hours_by_account(original_records)
    optimized_by_account = _hours_by_account(optimized_records)
    if original_by_account.keys() != optimized_by_account.keys():
        raise ValueError("La optimización de tarifas cambió las cuentas del reparto.")
    for account_code, hours in original_by_account.items():
        if not math.isclose(hours, optimized_by_account[account_code], abs_tol=_FLOAT_TOLERANCE):
            raise ValueError(f"La optimización de tarifas cambió las horas de {account_code}.")
    if not math.isclose(
        sum(item.management_hours for item in original_records),
        sum(item.management_hours for item in optimized_records),
        abs_tol=_FLOAT_TOLERANCE,
    ):
        raise ValueError("La optimización de tarifas cambió las horas totales del mes.")
    for item in optimized_records:
        units = round(item.management_hours * 2)
        if not math.isclose(item.management_hours * 2, units, abs_tol=_FLOAT_TOLERANCE):
            raise ValueError("La optimización generó horas fuera de escalones de 0,5.")


def _build_manager_billing_summaries(
    assignments: Iterable[ManagementAssignment],
) -> tuple[ManagerBillingSummary, ...]:
    records = tuple(assignments)
    grouped: dict[tuple[str, str], list[ManagementAssignment]] = defaultdict(list)
    for item in records:
        grouped[(item.employee_id, item.manager_name)].append(item)
    summaries: list[ManagerBillingSummary] = []
    for (employee_id, manager_name), items in grouped.items():
        hours = sum(item.management_hours for item in items)
        amount = sum(item.amount or 0.0 for item in items)
        rated_hours = sum(item.management_hours for item in items if item.management_rate is not None)
        summaries.append(ManagerBillingSummary(
            employee_id=employee_id,
            manager_name=manager_name,
            management_hours=hours,
            management_amount=amount,
            average_rate=amount / rated_hours if rated_hours else None,
            accounts_assigned=len({item.account_code for item in items}),
        ))
    return tuple(sorted(summaries, key=lambda item: normalize_identifier(item.manager_name)))


def _build_account_billing_summaries(
    assignments: Iterable[ManagementAssignment],
) -> tuple[AccountBillingSummary, ...]:
    grouped: dict[str, list[ManagementAssignment]] = defaultdict(list)
    for item in assignments:
        grouped[item.account_code].append(item)
    summaries: list[AccountBillingSummary] = []
    for account_code, items in grouped.items():
        hours = sum(item.management_hours for item in items)
        amount = sum(item.amount or 0.0 for item in items)
        rates = {item.management_rate for item in items}
        sources = {item.rate_source for item in items}
        summaries.append(AccountBillingSummary(
            account_code=account_code,
            service_name=items[0].service_name,
            management_hours=hours,
            management_rate=next(iter(rates)) if len(rates) == 1 else None,
            management_amount=amount,
            managers_assigned=len({item.employee_id for item in items}),
            rate_source=next(iter(sources)) if len(sources) == 1 else None,
        ))
    return tuple(sorted(summaries, key=lambda item: normalize_identifier(item.account_code)))


def _build_rate_billing_summary(
    assignments: Iterable[ManagementAssignment],
    accounts: Iterable[AccountBillingSummary],
    incidents: Iterable[RateOptimizationIncident],
) -> RateBillingSummary:
    records = tuple(assignments)
    account_records = tuple(accounts)
    incident_records = tuple(incidents)
    total_hours = sum(item.management_hours for item in records)
    total_amount = sum(item.amount or 0.0 for item in records)
    rated_hours = sum(item.management_hours for item in records if item.management_rate is not None)
    return RateBillingSummary(
        total_management_hours=total_hours,
        total_management_amount=total_amount,
        weighted_average_rate=total_amount / rated_hours if rated_hours else None,
        configured_block_accounts=sum(
            item.rate_source == RateSource.CONFIGURED_BLOCK for item in account_records
        ),
        automatically_optimized_accounts=sum(
            item.rate_source in {RateSource.ACCOUNT_RATE, RateSource.OPTIMIZED_RATE}
            for item in account_records
        ),
        multiple_rate_incidents=sum(
            item.code == RateIncidentCode.MULTIPLE_RATE_EXCEPTION for item in incident_records
        ),
    )


def _hours_by_account(assignments: Iterable[ManagementAssignment]) -> dict[str, float]:
    result: dict[str, float] = defaultdict(float)
    for item in assignments:
        result[item.account_code] += item.management_hours
    return result


__all__ = ["consolidate_rated_assignments", "optimize_management_rates"]
