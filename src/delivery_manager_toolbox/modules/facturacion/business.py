"""Primera fase del motor de cálculo de Facturación."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
import math

from delivery_manager_toolbox.modules.facturacion.allocation import calculate_management_allocation
from delivery_manager_toolbox.modules.facturacion.configuration import (
    BillingConfiguration,
    resolve_management_people_in_sheet,
)
from delivery_manager_toolbox.modules.facturacion.models import (
    AccountAssignment,
    AccountBaseHours,
    AccountCatalogItem,
    AccountManagementCalculation,
    AssignmentAuditRecord,
    AllocationStatus,
    BillingCalculationSummary,
    CalculationType,
    CleanBillingModel,
    CleanModelAuditSummary,
    SheetAnalysis,
    SheetCalculationResult,
    SkippedSheet,
    WorkbookCalculationResult,
)
from delivery_manager_toolbox.modules.facturacion.validation import normalize_identifier
from delivery_manager_toolbox.modules.facturacion.rate_optimization import optimize_management_rates
from delivery_manager_toolbox.modules.facturacion.work_calendar import floor_to_half_hour


MANAGEMENT_PERCENTAGE = 5.0
_HALF_HOUR = 0.5


def build_clean_billing_model(
    analysis: SheetAnalysis,
    configuration: BillingConfiguration,
) -> CleanBillingModel:
    """Crea una copia lógica sin las asignaciones originales de gestores."""
    resolutions = resolve_management_people_in_sheet(configuration, analysis)
    management_ids = {
        normalize_identifier(item.matched_employee_id)
        for item in resolutions
        if item.matched_employee_id is not None
    }
    exact_exceptions = {
        _person_account_key(exception.employee_id, exception.account_code)
        for exception in configuration.management_person_account_exceptions
    }
    fallback_exceptions = {
        (normalize_identifier(exception.person_name), normalize_identifier(exception.account_code))
        for exception in configuration.management_person_account_exceptions
    }

    retained: list[AccountAssignment] = []
    removed: list[AssignmentAuditRecord] = []
    preserved: dict[tuple[str, str], AssignmentAuditRecord] = {}
    for assignment in analysis.assignments:
        employee_key = normalize_identifier(assignment.person.employee_id)
        name_key = normalize_identifier(assignment.person.name)
        is_management_person = employee_key in management_ids
        if not is_management_person:
            retained.append(assignment)
            continue

        account_code = assignment.account_code or ""
        exact_key = _person_account_key(assignment.person.employee_id, account_code)
        fallback_key = (name_key, _normalize_optional(account_code)) if account_code else None
        is_exception = exact_key in exact_exceptions or fallback_key in fallback_exceptions
        if is_exception:
            retained.append(assignment)
            record = _audit_record(assignment)
            if exact_key in preserved:
                record = replace(record, hours=preserved[exact_key].hours + record.hours)
            preserved[exact_key] = record
        else:
            removed.append(_audit_record(assignment))

    audit = CleanModelAuditSummary(
        management_people_detected=len(management_ids),
        assignments_removed=len(removed),
        hours_removed=sum(record.hours for record in removed),
        exceptions_preserved=len(preserved),
        hours_preserved_by_exception=sum(record.hours for record in preserved.values()),
    )
    return CleanBillingModel(tuple(retained), tuple(removed), tuple(preserved.values()), audit)


def calculate_account_base_hours(
    clean_model: CleanBillingModel,
    account_catalog: Iterable[AccountCatalogItem] = (),
) -> tuple[AccountBaseHours, ...]:
    """Agrega horas numéricas por account_code, sin considerar la tarifa."""
    totals: dict[str, float] = {}
    services: dict[str, str] = {}
    display_codes: dict[str, str] = {}

    for item in account_catalog:
        key = normalize_identifier(item.account_code)
        totals.setdefault(key, 0.0)
        services.setdefault(key, item.service_name)
        display_codes.setdefault(key, item.account_code)

    for assignment in clean_model.assignments:
        if assignment.account_code is None:
            continue
        key = normalize_identifier(assignment.account_code)
        totals[key] = totals.get(key, 0.0) + _valid_assignment_hours(assignment)
        services.setdefault(key, assignment.service_name)
        display_codes.setdefault(key, assignment.account_code)

    return tuple(
        AccountBaseHours(display_codes[key], services.get(key, ""), hours)
        for key, hours in totals.items()
    )


def calculate_management_hours(
    base: AccountBaseHours,
    configuration: BillingConfiguration,
) -> AccountManagementCalculation:
    """Aplica las prioridades sin gestión, fija y porcentaje, en ese orden."""
    account_key = normalize_identifier(base.account_code)
    no_management = {
        normalize_identifier(account)
        for account in configuration.no_management_accounts
    }
    fixed_management = {
        normalize_identifier(record.account): record.fixed_hours
        for record in configuration.fixed_management_accounts
    }

    if account_key in no_management:
        return AccountManagementCalculation(
            base.account_code,
            base.service_name,
            base.base_hours,
            0.0,
            0.0,
            CalculationType.NO_MANAGEMENT,
            0.0,
        )
    if account_key in fixed_management:
        return AccountManagementCalculation(
            base.account_code,
            base.service_name,
            base.base_hours,
            0.0,
            fixed_management[account_key],
            CalculationType.FIXED,
            0.0,
        )

    raw_hours = base.base_hours * MANAGEMENT_PERCENTAGE / 100
    management_hours = _round_percentage_management_hours(base.base_hours, raw_hours)
    return AccountManagementCalculation(
        base.account_code,
        base.service_name,
        base.base_hours,
        raw_hours,
        management_hours,
        CalculationType.PERCENTAGE,
        MANAGEMENT_PERCENTAGE,
    )


def calculate_all_accounts(
    base_hours: Iterable[AccountBaseHours],
    configuration: BillingConfiguration,
) -> tuple[AccountManagementCalculation, ...]:
    return tuple(calculate_management_hours(base, configuration) for base in base_hours)


def build_calculation_summary(
    calculations: Iterable[AccountManagementCalculation],
    audit: CleanModelAuditSummary,
) -> BillingCalculationSummary:
    records = tuple(calculations)
    return BillingCalculationSummary(
        total_accounts=len(records),
        total_base_hours=sum(record.base_hours for record in records),
        total_management_hours=sum(record.management_hours for record in records),
        accounts_without_management=sum(
            record.calculation_type == CalculationType.NO_MANAGEMENT for record in records
        ),
        accounts_with_fixed_management=sum(
            record.calculation_type == CalculationType.FIXED for record in records
        ),
        accounts_percentage_based=sum(
            record.calculation_type == CalculationType.PERCENTAGE for record in records
        ),
        management_people_detected=audit.management_people_detected,
        assignments_removed=audit.assignments_removed,
        hours_removed=audit.hours_removed,
        exceptions_preserved=audit.exceptions_preserved,
        hours_preserved_by_exception=audit.hours_preserved_by_exception,
    )


def calculate_sheet_management(
    analysis: SheetAnalysis,
    configuration: BillingConfiguration,
) -> SheetCalculationResult:
    """Calcula un único período; rechaza metadatos o agregados de workbook."""
    if not isinstance(analysis, SheetAnalysis):
        raise TypeError("El cálculo requiere un SheetAnalysis correspondiente a una única hoja.")
    clean_model = build_clean_billing_model(analysis, configuration)
    base_hours = calculate_account_base_hours(clean_model, analysis.accounts)
    calculations = calculate_all_accounts(base_hours, configuration)
    summary = build_calculation_summary(calculations, clean_model.audit)
    allocation_result = calculate_management_allocation(analysis, calculations, configuration)
    incidents = (
        ("No se pudo repartir la gestión: configura al menos una Persona en Gestión con capacidad positiva.",)
        if summary.total_management_hours > 0 and allocation_result is None
        else ()
    )
    return SheetCalculationResult(
        sheet_name=analysis.sheet_name,
        summary=summary,
        account_calculations=calculations,
        clean_model=clean_model,
        allocation_result=allocation_result,
        incidents=incidents,
    )


def calculate_sheet_rate_optimization(
    result: SheetCalculationResult,
    analysis: SheetAnalysis,
    configuration: BillingConfiguration,
) -> SheetCalculationResult:
    """Ejecuta tarifas como fase explícita posterior al cálculo y reparto."""
    if result.sheet_name != analysis.sheet_name:
        raise ValueError("El resultado y el análisis deben pertenecer a la misma hoja.")
    if (
        result.allocation_result is not None
        and result.allocation_result.allocation_status in {
            AllocationStatus.CAPACITY_DATA_MISSING,
            AllocationStatus.MANAGER_NOT_FOUND_IN_SHEET,
            AllocationStatus.NO_REFERENCE_MANAGER_CAPACITY,
        }
    ):
        return result
    return replace(
        result,
        rate_optimization_result=optimize_management_rates(
            analysis,
            result.allocation_result,
            configuration,
        ),
    )


def calculate_workbook_management(
    analyses: Iterable[SheetAnalysis],
    configuration: BillingConfiguration,
    skipped_sheets: Iterable[SkippedSheet] = (),
) -> WorkbookCalculationResult:
    """Coordina cálculos independientes sin combinar datos entre hojas."""
    results_by_sheet: dict[str, SheetCalculationResult] = {}
    skipped = list(skipped_sheets)
    for analysis in analyses:
        incompatibility = _sheet_incompatibility_reason(analysis)
        if incompatibility is not None:
            skipped.append(SkippedSheet(analysis.sheet_name, incompatibility))
            continue
        result = calculate_sheet_management(
            analysis,
            configuration,
        )
        results_by_sheet[analysis.sheet_name] = calculate_sheet_rate_optimization(
            result,
            analysis,
            configuration,
        )
    return WorkbookCalculationResult(results_by_sheet, tuple(skipped))


def calculate_billing(
    analysis: SheetAnalysis,
    configuration: BillingConfiguration,
) -> SheetCalculationResult:
    """Alias compatible; el cálculo continúa siendo estrictamente por hoja."""
    return calculate_sheet_management(analysis, configuration)


def _valid_assignment_hours(assignment: AccountAssignment) -> float:
    return sum(
        item.hours
        for item in assignment.daily_hours
        if isinstance(item.hours, (int, float))
        and not isinstance(item.hours, bool)
        and math.isfinite(item.hours)
    )


def _sheet_incompatibility_reason(analysis: SheetAnalysis) -> str | None:
    if not analysis.people:
        return "No se detectaron filas de persona."
    if not any(assignment.account_code for assignment in analysis.assignments):
        return "No se detectaron asignaciones con código de cuenta."
    return None


def _round_percentage_management_hours(base_hours: float, raw_hours: float) -> float:
    if base_hours <= 0:
        return 0.0
    if raw_hours < _HALF_HOUR:
        return _HALF_HOUR
    return floor_to_half_hour(raw_hours)


def _audit_record(assignment: AccountAssignment) -> AssignmentAuditRecord:
    return AssignmentAuditRecord(
        assignment.person.employee_id,
        assignment.person.name,
        assignment.account_code or "Sin código EXT",
        assignment.service_name,
        _valid_assignment_hours(assignment),
    )


def _person_account_key(employee_id: object, account_code: object) -> tuple[str, str]:
    return normalize_identifier(employee_id), _normalize_optional(account_code)


def _normalize_optional(value: object) -> str:
    return normalize_identifier(value) if value is not None and str(value).strip() else ""


__all__ = [
    "SheetCalculationResult",
    "build_clean_billing_model",
    "build_calculation_summary",
    "calculate_account_base_hours",
    "calculate_all_accounts",
    "calculate_billing",
    "calculate_sheet_management",
    "calculate_sheet_rate_optimization",
    "calculate_workbook_management",
    "calculate_management_hours",
]
