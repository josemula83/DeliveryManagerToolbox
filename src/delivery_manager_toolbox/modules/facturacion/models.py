"""Contratos de datos del módulo de Facturación."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class WorksheetInfo:
    name: str
    approximate_records: int
    columns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    """Mapeo legado reservado para posibles formatos planos futuros."""

    person: str | None = None
    account: str | None = None
    hours: str | None = None
    rate: str | None = None


@dataclass(frozen=True, slots=True)
class WorkbookAnalysis:
    """Metadatos globales del fichero; no contiene datos calculables de varios meses."""

    filename: str
    worksheets: tuple[WorksheetInfo, ...]
    suggested_sheet: str | None = None
    suggested_mapping: ColumnMapping = ColumnMapping()

    @property
    def sheet_count(self) -> int:
        return len(self.worksheets)

    @property
    def sheet_names(self) -> tuple[str, ...]:
        return tuple(worksheet.name for worksheet in self.worksheets)

    @property
    def approximate_records(self) -> int:
        return sum(worksheet.approximate_records for worksheet in self.worksheets)

    def worksheet(self, name: str) -> WorksheetInfo:
        for worksheet in self.worksheets:
            if worksheet.name == name:
                return worksheet
        raise KeyError(f"No existe la hoja: {name}")


# Alias temporal para consumidores anteriores a la separación explícita workbook/hoja.
WorkbookInfo = WorkbookAnalysis


@dataclass(frozen=True, slots=True)
class Person:
    employee_id: str
    name: str


class CapacitySource(StrEnum):
    PERSON_TOTAL_HOURS = "PERSON_TOTAL_HOURS"
    DAILY_HOURS = "DAILY_HOURS"


@dataclass(frozen=True, slots=True)
class PersonMonthlyWorkCapacity:
    employee_id: str
    person_name: str
    monthly_workable_hours: float
    capacity_source: CapacitySource


@dataclass(frozen=True, slots=True)
class DailyHours:
    day: object
    hours: float


@dataclass(frozen=True, slots=True)
class ParsedAccountDescription:
    account_code: str | None
    service_name: str
    rate: float | None
    account_type: str | None


@dataclass(frozen=True, slots=True)
class AccountAssignment:
    person: Person
    account_code: str | None
    service_name: str
    rate: float | None
    original_description: str
    account_type: str | None
    daily_hours: tuple[DailyHours, ...] = ()

    @property
    def total_hours(self) -> float:
        return sum(item.hours for item in self.daily_hours)


@dataclass(frozen=True, slots=True)
class PersonSummary:
    employee_id: str
    name: str
    account_count: int


@dataclass(frozen=True, slots=True)
class AccountCatalogItem:
    account_code: str
    service_name: str
    original_description: str
    rates: tuple[float, ...]

    @property
    def display_name(self) -> str:
        return f"{self.account_code} — {self.service_name}" if self.service_name else self.account_code


@dataclass(frozen=True, slots=True)
class NormalizedHourRecord:
    employee_id: str
    person: str
    account: str | None
    service: str
    rate: float | None
    day: object
    hours: float


@dataclass(frozen=True, slots=True)
class SheetAnalysis:
    """Datos normalizados de una sola hoja/período."""

    sheet_name: str
    people: tuple[Person, ...]
    assignments: tuple[AccountAssignment, ...]
    raw_columns: tuple[str, ...]
    raw_rows: tuple[tuple[object, ...], ...]
    scanned_rows: int
    person_monthly_capacities: tuple[PersonMonthlyWorkCapacity, ...] = ()

    @property
    def person_summaries(self) -> tuple[PersonSummary, ...]:
        counts = {person.employee_id: 0 for person in self.people}
        for assignment in self.assignments:
            counts[assignment.person.employee_id] = counts.get(assignment.person.employee_id, 0) + 1
        return tuple(PersonSummary(person.employee_id, person.name, counts[person.employee_id]) for person in self.people)

    @property
    def accounts(self) -> tuple[AccountCatalogItem, ...]:
        catalog: dict[str, dict[str, object]] = {}
        for assignment in self.assignments:
            if assignment.account_code is None:
                continue
            item = catalog.setdefault(
                assignment.account_code,
                {
                    "service_name": assignment.service_name,
                    "original_description": assignment.original_description,
                    "rates": [],
                },
            )
            rates = item["rates"]
            if assignment.rate is not None and assignment.rate not in rates:
                rates.append(assignment.rate)
        return tuple(
            AccountCatalogItem(code, str(item["service_name"]), str(item["original_description"]), tuple(item["rates"]))
            for code, item in catalog.items()
        )

    @property
    def rates(self) -> tuple[float, ...]:
        result: list[float] = []
        for assignment in self.assignments:
            if assignment.rate is not None and assignment.rate not in result:
                result.append(assignment.rate)
        return tuple(result)

    @property
    def normalized_hours(self) -> tuple[NormalizedHourRecord, ...]:
        return tuple(
            NormalizedHourRecord(
                assignment.person.employee_id,
                assignment.person.name,
                assignment.account_code,
                assignment.service_name,
                assignment.rate,
                daily.day,
                daily.hours,
            )
            for assignment in self.assignments
            for daily in assignment.daily_hours
        )


# Alias temporal para compatibilidad con el nombre anterior.
HierarchicalWorksheetAnalysis = SheetAnalysis


@dataclass(frozen=True, slots=True)
class WorksheetPreview:
    """Contrato legado para el lector plano avanzado/manual."""

    sheet_name: str
    columns: tuple[str, ...]
    sample_rows: tuple[tuple[object, ...], ...]
    people: tuple[str, ...]
    accounts: tuple[str, ...]
    rates: tuple[object, ...]
    hours: tuple[object, ...]
    scanned_records: int


class CalculationType(StrEnum):
    NO_MANAGEMENT = "NO_MANAGEMENT"
    FIXED = "FIXED"
    PERCENTAGE = "PERCENTAGE"


@dataclass(frozen=True, slots=True)
class AssignmentAuditRecord:
    employee_id: str
    person_name: str
    account_code: str
    service_name: str
    hours: float


@dataclass(frozen=True, slots=True)
class CleanModelAuditSummary:
    management_people_detected: int
    assignments_removed: int
    hours_removed: float
    exceptions_preserved: int
    hours_preserved_by_exception: float


@dataclass(frozen=True, slots=True)
class CleanBillingModel:
    assignments: tuple[AccountAssignment, ...]
    removed_assignments: tuple[AssignmentAuditRecord, ...]
    preserved_exceptions: tuple[AssignmentAuditRecord, ...]
    audit: CleanModelAuditSummary


@dataclass(frozen=True, slots=True)
class AccountBaseHours:
    account_code: str
    service_name: str
    base_hours: float


@dataclass(frozen=True, slots=True)
class AccountManagementCalculation:
    account_code: str
    service_name: str
    base_hours: float
    raw_management_hours: float
    management_hours: float
    calculation_type: CalculationType
    percentage_applied: float


@dataclass(frozen=True, slots=True)
class BillingCalculationSummary:
    total_accounts: int
    total_base_hours: float
    total_management_hours: float
    accounts_without_management: int
    accounts_with_fixed_management: int
    accounts_percentage_based: int
    management_people_detected: int
    assignments_removed: int
    hours_removed: float
    exceptions_preserved: int
    hours_preserved_by_exception: float


class AllocationStatus(StrEnum):
    OK = "OK"
    OUTSIDE_TOLERANCE = "OUTSIDE_TOLERANCE"
    CAPACITY_OK = "CAPACITY_OK"
    WITHIN_TARGET = "WITHIN_TARGET"
    ABOVE_TARGET_DUE_TO_OVERFLOW = "ABOVE_TARGET_DUE_TO_OVERFLOW"
    BELOW_TARGET = "BELOW_TARGET"
    CAPACITY_DATA_MISSING = "CAPACITY_DATA_MISSING"


class RateSource(StrEnum):
    CONFIGURED_BLOCK = "CONFIGURED_BLOCK"
    ACCOUNT_RATE = "ACCOUNT_RATE"
    OPTIMIZED_RATE = "OPTIMIZED_RATE"


class RateIncidentCode(StrEnum):
    NO_RATE_FOUND = "NO_RATE_FOUND"
    MULTIPLE_RATE_EXCEPTION = "MULTIPLE_RATE_EXCEPTION"
    RATE_CONSTRAINT = "RATE_CONSTRAINT"
    OUTSIDE_TOLERANCE_AFTER_OPTIMIZATION = "OUTSIDE_TOLERANCE_AFTER_OPTIMIZATION"


@dataclass(frozen=True, slots=True)
class ManagerTarget:
    employee_id: str
    manager_name: str
    capacity_percentage: float
    base_target_hours: float
    monthly_workable_hours: float | None = None
    capacity_source: CapacitySource | None = None

    @property
    def preferred_min_hours(self) -> float:
        return self.base_target_hours * 0.90

    @property
    def preferred_max_hours(self) -> float:
        return self.base_target_hours * 1.10


@dataclass(frozen=True, slots=True)
class ManagementAssignment:
    sheet_name: str
    employee_id: str
    manager_name: str
    account_code: str
    service_name: str
    management_hours: float
    management_rate: float | None = None
    rate_source: RateSource | None = None
    amount: float | None = None
    rate_status: str = "PENDING"


@dataclass(frozen=True, slots=True)
class ManagerAllocationSummary:
    employee_id: str
    manager_name: str
    capacity_percentage: float
    base_target_hours: float
    assigned_hours: float
    difference_vs_base_target: float
    difference_percentage: float
    accounts_assigned: int
    status: AllocationStatus
    monthly_workable_hours: float | None = None
    capacity_source: CapacitySource | None = None
    preferred_min_hours: float = 0.0
    preferred_max_hours: float = 0.0
    overflow_hours_assigned: float = 0.0

@dataclass(frozen=True, slots=True)
class ManagementAllocationResult:
    sheet_name: str
    assignments: tuple[ManagementAssignment, ...]
    manager_summaries: tuple[ManagerAllocationSummary, ...]
    total_management_hours: float
    total_assigned_hours: float
    allocation_difference: float
    allocation_status: AllocationStatus
    split_account_count: int
    incidents: tuple[str, ...] = ()
    total_base_target_hours: float = 0.0
    overflow_hours: float = 0.0


@dataclass(frozen=True, slots=True)
class RateOptimizationIncident:
    code: RateIncidentCode
    message: str
    account_code: str | None = None
    manager_name: str | None = None


@dataclass(frozen=True, slots=True)
class ManagerBillingSummary:
    employee_id: str
    manager_name: str
    management_hours: float
    management_amount: float
    average_rate: float | None
    accounts_assigned: int


@dataclass(frozen=True, slots=True)
class AccountBillingSummary:
    account_code: str
    service_name: str
    management_hours: float
    management_rate: float | None
    management_amount: float
    managers_assigned: int
    rate_source: RateSource | None


@dataclass(frozen=True, slots=True)
class RateBillingSummary:
    total_management_hours: float
    total_management_amount: float
    weighted_average_rate: float | None
    configured_block_accounts: int
    automatically_optimized_accounts: int
    multiple_rate_incidents: int


@dataclass(frozen=True, slots=True)
class RateOptimizationResult:
    sheet_name: str
    assignments: tuple[ManagementAssignment, ...]
    manager_summaries: tuple[ManagerBillingSummary, ...]
    account_summaries: tuple[AccountBillingSummary, ...]
    billing_summary: RateBillingSummary
    incidents: tuple[RateOptimizationIncident, ...]
    consolidated_record_count: int


@dataclass(frozen=True, slots=True)
class SheetCalculationResult:
    """Resultado de la primera fase asociado inequívocamente a un período."""

    sheet_name: str
    summary: BillingCalculationSummary
    account_calculations: tuple[AccountManagementCalculation, ...]
    clean_model: CleanBillingModel
    allocation_result: ManagementAllocationResult | None = None
    rate_optimization_result: RateOptimizationResult | None = None
    incidents: tuple[str, ...] = ()

    @property
    def results(self) -> tuple[AccountManagementCalculation, ...]:
        """Alias de compatibilidad para la interfaz y exportador existentes."""
        return self.account_calculations

    @property
    def audit(self) -> CleanModelAuditSummary:
        return self.clean_model.audit

    @property
    def billing_summary(self) -> RateBillingSummary | None:
        return (
            self.rate_optimization_result.billing_summary
            if self.rate_optimization_result is not None
            else None
        )


@dataclass(frozen=True, slots=True)
class SkippedSheet:
    sheet_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class WorkbookCalculationResult:
    """Resultados independientes de las hojas procesables de un libro."""

    results_by_sheet: dict[str, SheetCalculationResult]
    skipped_sheets: tuple[SkippedSheet, ...] = ()

    @property
    def detected_sheet_count(self) -> int:
        return len(self.results_by_sheet) + len(self.skipped_sheets)

    @property
    def calculated_sheet_count(self) -> int:
        return len(self.results_by_sheet)


BillingResult = SheetCalculationResult
