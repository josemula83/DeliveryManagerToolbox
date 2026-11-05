"""Optimización de tarifas portada de Facturación BBVA V1.3."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
import math

from delivery_manager_toolbox.modules.facturacion.configuration import BillingConfiguration
from delivery_manager_toolbox.modules.facturacion.models import (
    AccountBillingSummary,
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
_SCORE_EPSILON = (1e-7, 1e-7, 1e-9, 1e-9, 1e-9)


@dataclass(slots=True)
class _RateBlock:
    assignment: ManagementAssignment
    original_rate: float
    base_rate: float
    base_source: RateSource
    multiplier: float

    @property
    def billable_hours(self) -> float:
        return (
            self.assignment.management_hours / self.multiplier
            if self.base_rate
            else self.assignment.management_hours
        )

    @property
    def billing_rate(self) -> float | None:
        return round(self.base_rate * self.multiplier, 2) if self.base_rate else None


@dataclass(slots=True)
class _ManagerState:
    employee_id: str
    name: str
    target: float
    desired_billable_hours: float
    blocks: list[_RateBlock]

    @property
    def informed(self) -> float:
        return sum(block.billable_hours for block in self.blocks)


def optimize_management_rates(
    analysis: SheetAnalysis,
    allocation: ManagementAllocationResult | None,
    configuration: BillingConfiguration,
) -> RateOptimizationResult | None:
    """Ejecuta literalmente la optimización por multiplicadores x2,5/x2/x1."""
    if allocation is None:
        return None

    consolidated = consolidate_rated_assignments(allocation.assignments)
    configured_rates = _configured_rates(configuration)
    automatic_rates = _legacy_account_rates(analysis, allocation)
    by_manager: dict[str, list[_RateBlock]] = defaultdict(list)
    incidents: list[RateOptimizationIncident] = []
    missing_accounts: set[str] = set()

    for assignment in consolidated:
        account_key = normalize_identifier(assignment.account_code)
        if account_key in configured_rates:
            base_rate = configured_rates[account_key]
            base_source = RateSource.CONFIGURED_BLOCK
        else:
            base_rate = automatic_rates.get(account_key, 0.0)
            base_source = RateSource.AUTOMATIC
        if not base_rate and assignment.account_code not in missing_accounts:
            incidents.append(RateOptimizationIncident(
                code=RateIncidentCode.NO_RATE_FOUND,
                account_code=assignment.account_code,
                message=f"No se encontró una tarifa base válida para {assignment.account_code}.",
            ))
            missing_accounts.add(assignment.account_code)
        by_manager[normalize_identifier(assignment.employee_id)].append(_RateBlock(
            assignment=assignment,
            original_rate=automatic_rates.get(account_key, 0.0),
            base_rate=base_rate,
            base_source=base_source,
            multiplier=2.5 if base_rate else 1.0,
        ))

    total_target = sum(item.base_target_hours for item in allocation.manager_summaries)
    total_management = sum(item.management_hours for item in consolidated)
    minimum_billable = sum(
        item.management_hours / 2.5
        if _base_rate_for(item.account_code, configured_rates, automatic_rates)
        else item.management_hours
        for item in consolidated
    )
    achievable = max(minimum_billable, min(total_target, total_management))
    common_ratio = achievable / total_target if total_target > _FLOAT_TOLERANCE else 1.0
    states = [
        _ManagerState(
            employee_id=summary.employee_id,
            name=summary.manager_name,
            target=summary.base_target_hours,
            desired_billable_hours=summary.base_target_hours * common_ratio,
            blocks=by_manager.get(normalize_identifier(summary.employee_id), []),
        )
        for summary in allocation.manager_summaries
    ]

    _optimize_globally(states)
    _rebalance_lower_rates(states)
    optimized = tuple(
        _final_assignment(block)
        for state in states
        for block in state.blocks
    )
    incidents.extend(_multiple_rate_incidents(optimized))
    _validate_optimized_hours(allocation.assignments, optimized)
    manager_summaries = _build_manager_billing_summaries(optimized)
    account_summaries = _build_account_billing_summaries(optimized)
    billing_summary = _build_rate_billing_summary(optimized, incidents)
    return RateOptimizationResult(
        sheet_name=analysis.sheet_name,
        assignments=optimized,
        manager_summaries=manager_summaries,
        account_summaries=account_summaries,
        billing_summary=billing_summary,
        incidents=tuple(incidents),
        consolidated_record_count=len(allocation.assignments) - len(consolidated),
    )


def consolidate_rated_assignments(
    assignments: Iterable[ManagementAssignment],
) -> tuple[ManagementAssignment, ...]:
    """Consolida cada gestor/cuenta antes de elegir una única tarifa legacy."""
    merged: dict[tuple[str, str, str], ManagementAssignment] = {}
    for item in assignments:
        key = (item.sheet_name, item.employee_id, item.account_code)
        if key not in merged:
            merged[key] = replace(
                item,
                management_rate=None,
                rate_source=None,
                amount=None,
                rate_status="PENDING",
                original_rate=None,
                reference_rate=None,
                reference_rate_source=None,
                rate_multiplier=None,
                billable_hours=None,
                rate_reason="",
            )
            continue
        previous = merged[key]
        merged[key] = replace(
            previous,
            management_hours=previous.management_hours + item.management_hours,
        )
    return tuple(merged.values())


def _configured_rates(configuration: BillingConfiguration) -> dict[str, float]:
    return {
        normalize_identifier(account): block.management_rate
        for block in configuration.management_rate_blocks
        for account in block.accounts
    }


def _legacy_account_rates(
    analysis: SheetAnalysis,
    allocation: ManagementAllocationResult,
) -> dict[str, float]:
    """Replica la primera tarifa de cuenta que alimenta ``calcMap`` en el HTML."""
    manager_ids = {
        normalize_identifier(item.employee_id)
        for item in allocation.manager_summaries
    }
    rates: dict[str, float] = {}
    fallback: dict[str, float] = {}
    for item in analysis.assignments:
        if item.account_code is None:
            continue
        key = normalize_identifier(item.account_code)
        fallback.setdefault(key, item.rate or 0.0)
        if normalize_identifier(item.person.employee_id) not in manager_ids:
            rates.setdefault(key, item.rate or 0.0)
    for key, rate in fallback.items():
        rates.setdefault(key, rate)
    return rates


def _base_rate_for(
    account_code: str,
    configured_rates: dict[str, float],
    automatic_rates: dict[str, float],
) -> float:
    key = normalize_identifier(account_code)
    return configured_rates.get(key, automatic_rates.get(key, 0.0))


def _optimize_globally(states: list[_ManagerState]) -> None:
    guard = 0
    while guard < 10_000:
        guard += 1
        base_score = _solution_score(states)
        best: tuple[_RateBlock, float, tuple[float, ...], float, float] | None = None
        for state in states:
            for block in state.blocks:
                if not block.base_rate:
                    continue
                options = (2.0, 1.0) if block.multiplier == 2.5 else (1.0,) if block.multiplier == 2.0 else ()
                for multiplier in options:
                    old = block.multiplier
                    block.multiplier = multiplier
                    score = _solution_score(states)
                    burden = _tariff_burden(state)
                    tie_better = (
                        best is not None
                        and not _better(score, best[2])
                        and not _better(best[2], score)
                        and (
                            burden[0] < best[3] - _FLOAT_TOLERANCE
                            or (
                                math.isclose(burden[0], best[3], abs_tol=_FLOAT_TOLERANCE)
                                and burden[1] < best[4] - _FLOAT_TOLERANCE
                            )
                        )
                    )
                    if _better(score, base_score) and (
                        best is None or _better(score, best[2]) or tie_better
                    ):
                        best = (block, multiplier, score, burden[0], burden[1])
                    block.multiplier = old
        if best is None:
            break
        best[0].multiplier = best[1]


def _rebalance_lower_rates(states: list[_ManagerState]) -> None:
    guard = 0
    while guard < 2_000:
        guard += 1
        base_score = _solution_score(states)
        burdens = [(state, *_tariff_burden(state)) for state in states]
        most = sorted(burdens, key=lambda item: (-item[1], -item[2]))[0] if burdens else None
        least = sorted(burdens, key=lambda item: (item[1], item[2]))[0] if burdens else None
        if (
            most is None
            or least is None
            or most[0] is least[0]
            or most[1] - least[1] < 0.05
        ):
            break
        improved = False
        for high in most[0].blocks:
            if not high.base_rate or high.multiplier == 2.5:
                continue
            old_high = high.multiplier
            high.multiplier = 2.0 if high.multiplier == 1.0 else 2.5
            for low in least[0].blocks:
                if not low.base_rate or low.multiplier == 1.0:
                    continue
                old_low = low.multiplier
                low.multiplier = 2.0 if low.multiplier == 2.5 else 1.0
                score = _solution_score(states)
                if (
                    score[0] <= base_score[0] + 0.01
                    and score[1] <= base_score[1] + 0.002
                    and score[2] < base_score[2] - 1e-6
                ):
                    improved = True
                    break
                low.multiplier = old_low
            if improved:
                break
            high.multiplier = old_high
        if not improved:
            break


def _solution_score(states: list[_ManagerState]) -> tuple[float, ...]:
    deviations = [abs(_relative_deviation(state)) for state in states]
    burdens = [_tariff_burden(state) for state in states]
    x1_values = [item[0] for item in burdens]
    x2_values = [item[1] for item in burdens]
    return (
        max((0.0, *deviations)),
        sum(value * value for value in deviations),
        max((0.0, *x1_values)) - min(x1_values, default=0.0),
        max((0.0, *x2_values)) - min(x2_values, default=0.0),
        sum(item[2] for item in burdens),
    )


def _relative_deviation(state: _ManagerState) -> float:
    if state.desired_billable_hours <= _FLOAT_TOLERANCE:
        return 0.0
    return (state.informed - state.desired_billable_hours) / state.desired_billable_hours


def _tariff_burden(state: _ManagerState) -> tuple[float, float, float]:
    flexible = [block for block in state.blocks if block.base_rate]
    total = sum(block.assignment.management_hours for block in flexible) or 1.0
    x1 = sum(
        block.assignment.management_hours for block in flexible if block.multiplier == 1.0
    ) / total
    x2 = sum(
        block.assignment.management_hours for block in flexible if block.multiplier == 2.0
    ) / total
    penalty = sum(
        block.assignment.management_hours * (2.5 - block.multiplier)
        for block in flexible
    )
    return x1, x2, penalty


def _better(candidate: tuple[float, ...], reference: tuple[float, ...]) -> bool:
    for index, value in enumerate(candidate):
        if value < reference[index] - _SCORE_EPSILON[index]:
            return True
        if value > reference[index] + _SCORE_EPSILON[index]:
            return False
    return False


def _final_assignment(block: _RateBlock) -> ManagementAssignment:
    rate = block.billing_rate
    billable_hours = block.billable_hours
    optimized = block.base_rate and block.multiplier < 2.5
    source = RateSource.LEGACY_OPTIMIZATION if optimized else block.base_source if block.base_rate else None
    if not block.base_rate:
        reason = "Sin tarifa base válida; el HTML legacy mantiene las horas y genera importe cero."
        status = RateIncidentCode.NO_RATE_FOUND.value
        amount = 0.0
    else:
        origin = "bloque configurado" if block.base_source == RateSource.CONFIGURED_BLOCK else "tarifa original de la cuenta"
        reason = f"Tarifa base de {origin}; multiplicador legacy x{block.multiplier:g}."
        status = "OK"
        amount = billable_hours * rate
    return replace(
        block.assignment,
        management_rate=rate,
        rate_source=source,
        amount=amount,
        rate_status=status,
        original_rate=block.original_rate or None,
        reference_rate=block.base_rate or None,
        reference_rate_source=block.base_source if block.base_rate else None,
        rate_multiplier=block.multiplier if block.base_rate else None,
        billable_hours=billable_hours,
        rate_reason=reason,
    )


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


def _validate_optimized_hours(
    original: Iterable[ManagementAssignment],
    optimized: Iterable[ManagementAssignment],
) -> None:
    original_records = tuple(original)
    optimized_records = tuple(optimized)
    for grouping, label in (
        (_hours_by_account, "cuentas"),
        (_hours_by_manager_account, "reparto gestor/cuenta"),
    ):
        before = grouping(original_records)
        after = grouping(optimized_records)
        if before.keys() != after.keys() or any(
            not math.isclose(value, after[key], abs_tol=_FLOAT_TOLERANCE)
            for key, value in before.items()
        ):
            raise ValueError(f"La optimización de tarifas cambió las horas de {label}.")
    if not math.isclose(
        sum(item.management_hours for item in original_records),
        sum(item.management_hours for item in optimized_records),
        abs_tol=_FLOAT_TOLERANCE,
    ):
        raise ValueError("La optimización de tarifas cambió las horas totales del mes.")
    for item in optimized_records:
        units = round(item.management_hours * 2)
        if not math.isclose(item.management_hours * 2, units, abs_tol=_FLOAT_TOLERANCE):
            raise ValueError("La optimización generó horas de gestión fuera de escalones de 0,5.")


def _build_manager_billing_summaries(
    assignments: Iterable[ManagementAssignment],
) -> tuple[ManagerBillingSummary, ...]:
    grouped: dict[tuple[str, str], list[ManagementAssignment]] = defaultdict(list)
    for item in assignments:
        grouped[(item.employee_id, item.manager_name)].append(item)
    summaries: list[ManagerBillingSummary] = []
    for (employee_id, manager_name), items in grouped.items():
        billed = sum(item.billable_hours or 0.0 for item in items if item.management_rate is not None)
        amount = sum(item.amount or 0.0 for item in items)
        summaries.append(ManagerBillingSummary(
            employee_id=employee_id,
            manager_name=manager_name,
            management_hours=sum(item.management_hours for item in items),
            billable_hours=sum(item.billable_hours or 0.0 for item in items),
            management_amount=amount,
            average_rate=amount / billed if billed else None,
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
        rates = {item.management_rate for item in items}
        sources = {item.rate_source for item in items}
        summaries.append(AccountBillingSummary(
            account_code=account_code,
            service_name=items[0].service_name,
            management_hours=sum(item.management_hours for item in items),
            billable_hours=sum(item.billable_hours or 0.0 for item in items),
            management_rate=next(iter(rates)) if len(rates) == 1 else None,
            management_amount=sum(item.amount or 0.0 for item in items),
            managers_assigned=len({item.employee_id for item in items}),
            rate_source=next(iter(sources)) if len(sources) == 1 else None,
        ))
    return tuple(sorted(summaries, key=lambda item: normalize_identifier(item.account_code)))


def _build_rate_billing_summary(
    assignments: Iterable[ManagementAssignment],
    incidents: Iterable[RateOptimizationIncident],
) -> RateBillingSummary:
    records = tuple(assignments)
    incident_records = tuple(incidents)
    total_billable = sum(item.billable_hours or 0.0 for item in records)
    total_amount = sum(item.amount or 0.0 for item in records)
    rated_billable = sum(
        item.billable_hours or 0.0 for item in records if item.management_rate is not None
    )
    return RateBillingSummary(
        total_management_hours=sum(item.management_hours for item in records),
        total_billable_hours=total_billable,
        total_management_amount=total_amount,
        weighted_average_rate=total_amount / rated_billable if rated_billable else None,
        configured_block_accounts=len({
            item.account_code for item in records
            if item.reference_rate_source == RateSource.CONFIGURED_BLOCK
        }),
        optimized_accounts=len({
            item.account_code for item in records
            if item.rate_source == RateSource.LEGACY_OPTIMIZATION
        }),
        multiple_rate_incidents=sum(
            item.code == RateIncidentCode.MULTIPLE_RATE_EXCEPTION for item in incident_records
        ),
    )


def _hours_by_account(assignments: Iterable[ManagementAssignment]) -> dict[str, float]:
    result: dict[str, float] = defaultdict(float)
    for item in assignments:
        result[item.account_code] += item.management_hours
    return result


def _hours_by_manager_account(
    assignments: Iterable[ManagementAssignment],
) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = defaultdict(float)
    for item in assignments:
        result[(item.employee_id, item.account_code)] += item.management_hours
    return result


__all__ = ["consolidate_rated_assignments", "optimize_management_rates"]
