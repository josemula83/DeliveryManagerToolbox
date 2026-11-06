"""Reparto determinista de horas de gestión entre Personas en Gestión."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
import math
import re

from delivery_manager_toolbox.modules.facturacion.configuration import (
    BillingConfiguration,
    resolve_management_people_in_sheet,
)
from delivery_manager_toolbox.modules.facturacion.models import (
    AccountManagementCalculation,
    AllocationStatus,
    CapacitySource,
    ManagementAllocationResult,
    ManagementAssignment,
    ManagementPersonResolution,
    ManagerAllocationSummary,
    ManagerTarget,
    SheetAnalysis,
)
from delivery_manager_toolbox.modules.facturacion.validation import normalize_identifier
from delivery_manager_toolbox.modules.facturacion.work_calendar import (
    calculate_manager_base_target,
    floor_to_half_hour,
)


ALLOCATION_STEP = 0.5
_FLOAT_TOLERANCE = 1e-9


def calculate_manager_targets(
    _total_management_hours: float,
    analysis: SheetAnalysis,
    configuration: BillingConfiguration,
) -> tuple[ManagerTarget, ...]:
    """Calcula el objetivo base mensual de cada Persona en Gestión."""
    configured = sorted(
        configuration.management_people,
        key=lambda item: normalize_identifier(item.name),
    )
    resolutions = {
        normalize_identifier(item.configured_manager_name): item
        for item in resolve_management_people_in_sheet(configuration, analysis)
    }
    resolved = tuple(
        (item, resolutions[normalize_identifier(item.name)])
        for item in configured
    )
    real_capacities = tuple(
        resolution.monthly_workable_hours
        for _, resolution in resolved
        if _has_valid_calendar_capacity(resolution)
    )
    peer_average = (
        floor_to_half_hour(sum(real_capacities) / len(real_capacities))
        if real_capacities
        else None
    )

    targets: list[ManagerTarget] = []
    for item, resolution in resolved:
        employee_id = (
            resolution.matched_employee_id
            or resolution.configured_employee_id
            or item.name
        )
        if _has_valid_calendar_capacity(resolution):
            monthly_workable_hours = resolution.monthly_workable_hours
            capacity_source = CapacitySource.THEORETICAL_CALENDAR
        elif resolution.matched_employee_id is None and peer_average is not None:
            monthly_workable_hours = peer_average
            capacity_source = CapacitySource.PEER_MONTHLY_AVERAGE
        else:
            monthly_workable_hours = None
            capacity_source = None
        base_target_hours = (
            calculate_manager_base_target(
                monthly_workable_hours,
                item.free_capacity_percentage,
            )
            if monthly_workable_hours is not None
            else 0.0
        )
        targets.append(ManagerTarget(
            employee_id=employee_id,
            manager_name=item.name,
            capacity_percentage=item.free_capacity_percentage,
            base_target_hours=base_target_hours,
            monthly_workable_hours=monthly_workable_hours,
            capacity_source=capacity_source,
            resolution=resolution,
        ))
    return tuple(targets)


def allocate_accounts_to_managers(
    sheet_name: str,
    calculations: Iterable[AccountManagementCalculation],
    targets: Iterable[ManagerTarget],
) -> tuple[ManagementAssignment, ...]:
    """Porta el reparto secuencial por cuotas proporcionales del HTML legacy."""
    account_records = tuple(
        sorted(
            (item for item in calculations if item.management_hours > 0),
            key=lambda item: _legacy_account_key(item.account_code, item.service_name),
        )
    )
    manager_targets = tuple(targets)
    total_units = sum(_to_units(item.management_hours) for item in account_records)
    manager_units = _manager_goal_units(total_units, manager_targets)
    assignments: list[ManagementAssignment] = []
    assigned_units = [0 for _ in manager_targets]
    manager_index = 0

    for account in account_records:
        account_remaining = _to_units(account.management_hours)
        while account_remaining:
            if manager_index >= len(manager_targets):
                raise ValueError("No hay Personas en Gestión para completar el reparto.")
            quota_remaining = max(manager_units[manager_index] - assigned_units[manager_index], 0)
            if manager_index == len(manager_targets) - 1:
                allocated_units = account_remaining
            elif quota_remaining <= 0:
                manager_index += 1
                continue
            else:
                allocated_units = max(1, min(account_remaining, quota_remaining))
            if allocated_units <= 0:
                manager_index += 1
                continue
            manager = manager_targets[manager_index]
            assignments.append(ManagementAssignment(
                sheet_name=sheet_name,
                employee_id=manager.employee_id,
                manager_name=manager.manager_name,
                account_code=account.account_code,
                service_name=account.service_name,
                management_hours=_from_units(allocated_units),
            ))
            account_remaining -= allocated_units
            assigned_units[manager_index] += allocated_units
            if (
                account_remaining
                or assigned_units[manager_index] >= manager_units[manager_index]
            ):
                manager_index += 1

    return tuple(assignments)


def rebalance_allocations(
    assignments: Iterable[ManagementAssignment],
    targets: Iterable[ManagerTarget],
) -> tuple[ManagementAssignment, ...]:
    """El HTML legacy no ejecuta una segunda pasada sobre el reparto de gestión."""
    del targets
    return tuple(assignments)


def validate_allocation(
    calculations: Iterable[AccountManagementCalculation],
    assignments: Iterable[ManagementAssignment],
) -> float:
    """Valida escalones y cuadre exacto por cuenta y para toda la hoja."""
    expected_by_account = {
        item.account_code: item.management_hours
        for item in calculations
        if item.management_hours > 0
    }
    assigned_by_account: dict[str, float] = defaultdict(float)
    for assignment in assignments:
        _to_units(assignment.management_hours)
        assigned_by_account[assignment.account_code] += assignment.management_hours
    for account_code, expected in expected_by_account.items():
        if not math.isclose(assigned_by_account[account_code], expected, abs_tol=_FLOAT_TOLERANCE):
            raise ValueError(f"El reparto de {account_code} no cuadra con sus horas de gestión.")
    unexpected = set(assigned_by_account) - set(expected_by_account)
    if unexpected:
        raise ValueError("El reparto contiene cuentas sin horas de gestión.")
    expected_total = sum(expected_by_account.values())
    assigned_total = sum(assigned_by_account.values())
    difference = assigned_total - expected_total
    if not math.isclose(difference, 0.0, abs_tol=_FLOAT_TOLERANCE):
        raise ValueError("El reparto total no cuadra con las horas de gestión calculadas.")
    return 0.0


def build_manager_summary(
    targets: Iterable[ManagerTarget],
    assignments: Iterable[ManagementAssignment],
) -> tuple[ManagerAllocationSummary, ...]:
    records = tuple(assignments)
    assigned = _assigned_hours_by_manager(records)
    accounts: dict[str, set[str]] = defaultdict(set)
    for record in records:
        accounts[record.manager_name].add(record.account_code)
    summaries: list[ManagerAllocationSummary] = []
    for target in targets:
        assigned_hours = assigned[target.manager_name]
        difference = assigned_hours - target.base_target_hours
        difference_percentage = (
            difference / target.base_target_hours * 100
            if target.base_target_hours > _FLOAT_TOLERANCE
            else 0.0
        )
        if target.monthly_workable_hours is None and (
            target.resolution is None or target.resolution.matched_employee_id is None
        ):
            status = AllocationStatus.NO_REFERENCE_MANAGER_CAPACITY
        elif target.monthly_workable_hours is None:
            status = AllocationStatus.CAPACITY_DATA_MISSING
        elif assigned_hours > target.base_target_hours + _FLOAT_TOLERANCE:
            status = AllocationStatus.ABOVE_TARGET_DUE_TO_OVERFLOW
        elif math.isclose(
            assigned_hours,
            target.base_target_hours,
            abs_tol=_FLOAT_TOLERANCE,
        ):
            status = AllocationStatus.WITHIN_TARGET
        else:
            status = AllocationStatus.BELOW_TARGET
        summaries.append(ManagerAllocationSummary(
            employee_id=target.employee_id,
            manager_name=target.manager_name,
            capacity_percentage=target.capacity_percentage,
            base_target_hours=target.base_target_hours,
            assigned_hours=assigned_hours,
            difference_vs_base_target=difference,
            difference_percentage=difference_percentage,
            accounts_assigned=len(accounts[target.manager_name]),
            status=status,
            monthly_workable_hours=target.monthly_workable_hours,
            capacity_source=target.capacity_source,
        ))
    return tuple(summaries)


def calculate_management_allocation(
    analysis: SheetAnalysis,
    calculations: Iterable[AccountManagementCalculation],
    configuration: BillingConfiguration,
) -> ManagementAllocationResult | None:
    records = tuple(calculations)
    total_management_hours = sum(item.management_hours for item in records)
    targets = calculate_manager_targets(total_management_hours, analysis, configuration)
    if total_management_hours > 0 and not targets:
        return None
    missing_targets = tuple(
        target for target in targets if target.monthly_workable_hours is None
    )
    if missing_targets:
        summaries = build_manager_summary(targets, ())
        incidents = tuple(_missing_capacity_incident(target) for target in missing_targets)
        no_reference_capacity = any(
            target.resolution is None or target.resolution.matched_employee_id is None
            for target in missing_targets
        )
        return ManagementAllocationResult(
            sheet_name=analysis.sheet_name,
            assignments=(),
            manager_summaries=summaries,
            total_management_hours=total_management_hours,
            total_assigned_hours=0.0,
            allocation_difference=-total_management_hours,
            allocation_status=(
                AllocationStatus.NO_REFERENCE_MANAGER_CAPACITY
                if no_reference_capacity
                else AllocationStatus.CAPACITY_DATA_MISSING
            ),
            split_account_count=0,
            incidents=incidents,
            total_base_target_hours=sum(item.base_target_hours for item in targets),
            overflow_hours=0.0,
            management_person_resolutions=tuple(
                item.resolution for item in targets if item.resolution is not None
            ),
        )
    if total_management_hours > 0 and sum(item.base_target_hours for item in targets) <= 0:
        raise ValueError("La capacidad objetivo total de los responsables debe ser mayor que 0%.")
    initial = allocate_accounts_to_managers(analysis.sheet_name, records, targets)
    assignments = initial
    allocation_difference = validate_allocation(records, assignments)
    summaries = build_manager_summary(targets, assignments)
    total_assigned_hours = sum(item.management_hours for item in assignments)
    total_base_target = sum(
        item.base_target_hours for item in targets if item.monthly_workable_hours is not None
    )
    overflow_hours = max(total_management_hours - total_base_target, 0.0)
    status = (
        AllocationStatus.ABOVE_TARGET_DUE_TO_OVERFLOW
        if overflow_hours > _FLOAT_TOLERANCE
        else AllocationStatus.CAPACITY_OK
    )
    manager_count_by_account: dict[str, set[str]] = defaultdict(set)
    for assignment in assignments:
        manager_count_by_account[assignment.account_code].add(assignment.manager_name)
    return ManagementAllocationResult(
        sheet_name=analysis.sheet_name,
        assignments=assignments,
        manager_summaries=summaries,
        total_management_hours=total_management_hours,
        total_assigned_hours=total_assigned_hours,
        allocation_difference=allocation_difference,
        allocation_status=status,
        split_account_count=sum(len(managers) > 1 for managers in manager_count_by_account.values()),
        incidents=tuple(
            _peer_average_incident(target, analysis.sheet_name)
            for target in targets
            if target.capacity_source == CapacitySource.PEER_MONTHLY_AVERAGE
        ),
        total_base_target_hours=total_base_target,
        overflow_hours=overflow_hours,
        management_person_resolutions=tuple(
            item.resolution for item in targets if item.resolution is not None
        ),
    )


def _missing_capacity_incident(target: ManagerTarget) -> str:
    resolution = target.resolution
    if resolution is None or resolution.matched_employee_id is None:
        return (
            "NO_REFERENCE_MANAGER_CAPACITY: "
            "No existen gestores presentes en esta hoja con capacidad válida para calcular "
            "la capacidad media de los gestores ausentes."
        )
    return (
        "CAPACITY_DATA_MISSING: "
        f"{target.manager_name}. No se ha podido determinar su calendario laboral mensual."
    )


def _peer_average_incident(target: ManagerTarget, sheet_name: str) -> str:
    return (
        "PEER_MONTHLY_AVERAGE: "
        f"{target.manager_name} no aparece en {sheet_name}. Se ha utilizado una capacidad "
        f"mensual estimada de {target.monthly_workable_hours:g}h, calculada como la media "
        "de los gestores presentes."
    )


def _has_valid_calendar_capacity(resolution: ManagementPersonResolution) -> bool:
    hours = resolution.monthly_workable_hours
    return (
        resolution.matched_employee_id is not None
        and resolution.capacity_source == CapacitySource.THEORETICAL_CALENDAR
        and hours is not None
        and math.isfinite(hours)
        and hours >= 0
    )


def _manager_goal_units(total_units: int, targets: tuple[ManagerTarget, ...]) -> tuple[int, ...]:
    if total_units == 0:
        return tuple(0 for _ in targets)
    if not targets:
        return ()
    total_base_target = sum(target.base_target_hours for target in targets)
    if total_base_target <= _FLOAT_TOLERANCE:
        return tuple(0 for _ in targets)
    desired_units = [
        total_units * target.base_target_hours / total_base_target
        for target in targets
    ]
    return _round_units_to_total(desired_units, total_units, targets)


def _round_units_to_total(
    desired_units: list[float],
    total_units: int,
    targets: tuple[ManagerTarget, ...],
) -> tuple[int, ...]:
    rounded = [math.floor(value + _FLOAT_TOLERANCE) for value in desired_units]
    remaining = total_units - sum(rounded)
    ranking = sorted(
        range(len(targets)),
        key=lambda index: (
            -(desired_units[index] - rounded[index]),
            normalize_identifier(targets[index].manager_name),
        ),
    )
    for index in ranking[:remaining]:
        rounded[index] += 1
    return tuple(rounded)


def _assigned_hours_by_manager(assignments: Iterable[ManagementAssignment]) -> dict[str, float]:
    result: dict[str, float] = defaultdict(float)
    for item in assignments:
        result[item.manager_name] += item.management_hours
    return result


def _to_units(hours: float) -> int:
    units = round(hours / ALLOCATION_STEP)
    if not math.isclose(hours, units * ALLOCATION_STEP, abs_tol=_FLOAT_TOLERANCE):
        raise ValueError("Las horas de gestión deben ser múltiplos de 0,5.")
    return units


def _from_units(units: int) -> float:
    return units * ALLOCATION_STEP


def _legacy_account_key(account_code: str, service_name: str) -> tuple[tuple[int, ...], str]:
    numeric_parts = tuple(int(part) for part in re.findall(r"\d+", account_code or service_name))
    return numeric_parts, normalize_identifier(service_name)


__all__ = [
    "allocate_accounts_to_managers",
    "build_manager_summary",
    "calculate_management_allocation",
    "calculate_manager_targets",
    "rebalance_allocations",
    "validate_allocation",
]
