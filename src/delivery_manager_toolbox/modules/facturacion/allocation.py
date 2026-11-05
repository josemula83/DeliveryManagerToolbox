"""Reparto determinista de horas de gestión entre Personas en Gestión."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
import math
import re

from delivery_manager_toolbox.modules.facturacion.configuration import BillingConfiguration
from delivery_manager_toolbox.modules.facturacion.models import (
    AccountManagementCalculation,
    AllocationStatus,
    ManagementAllocationResult,
    ManagementAssignment,
    ManagerAllocationSummary,
    ManagerTarget,
    SheetAnalysis,
)
from delivery_manager_toolbox.modules.facturacion.validation import normalize_identifier


ALLOCATION_STEP = 0.5
_FLOAT_TOLERANCE = 1e-9
_NATURAL_PART = re.compile(r"(\d+)")


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
    employee_ids = {
        normalize_identifier(person.name): person.employee_id
        for person in analysis.people
    }
    for exception in configuration.management_person_account_exceptions:
        employee_ids.setdefault(
            normalize_identifier(exception.person_name),
            exception.employee_id,
        )
    monthly_capacities = {
        normalize_identifier(item.person_name): item
        for item in analysis.person_monthly_capacities
    }

    targets: list[ManagerTarget] = []
    for item in configured:
        capacity = monthly_capacities.get(normalize_identifier(item.name))
        monthly_workable_hours = capacity.monthly_workable_hours if capacity is not None else None
        base_target_hours = (
            monthly_workable_hours * item.free_capacity_percentage / 100
            if monthly_workable_hours is not None
            else 0.0
        )
        targets.append(ManagerTarget(
            employee_id=employee_ids.get(normalize_identifier(item.name), item.name),
            manager_name=item.name,
            capacity_percentage=item.free_capacity_percentage,
            base_target_hours=base_target_hours,
            monthly_workable_hours=monthly_workable_hours,
            capacity_source=capacity.capacity_source if capacity is not None else None,
        ))
    return tuple(targets)


def allocate_accounts_to_managers(
    sheet_name: str,
    calculations: Iterable[AccountManagementCalculation],
    targets: Iterable[ManagerTarget],
) -> tuple[ManagementAssignment, ...]:
    """Asigna cuentas secuencialmente, priorizando estabilidad y objetivos operativos."""
    account_records = tuple(
        sorted(
            (item for item in calculations if item.management_hours > 0),
            key=lambda item: _natural_key(item.account_code),
        )
    )
    manager_targets = tuple(targets)
    total_units = sum(_to_units(item.management_hours) for item in account_records)
    manager_units = _manager_goal_units(total_units, manager_targets)
    assignments: list[ManagementAssignment] = []
    assigned_units = [0 for _ in manager_targets]
    manager_index = 0
    has_overflow = total_units * ALLOCATION_STEP > sum(
        item.base_target_hours for item in manager_targets
    ) + _FLOAT_TOLERANCE

    for account in account_records:
        account_remaining = _to_units(account.management_hours)
        while account_remaining:
            if manager_index >= len(manager_targets):
                raise ValueError("No hay Personas en Gestión para completar el reparto.")
            if manager_index == len(manager_targets) - 1:
                allocated_units = account_remaining
            else:
                target = manager_targets[manager_index]
                goal_remaining = max(manager_units[manager_index] - assigned_units[manager_index], 0)
                if account_remaining <= goal_remaining:
                    allocated_units = account_remaining
                elif not has_overflow:
                    preferred_max_units = math.floor(
                        target.preferred_max_hours / ALLOCATION_STEP + _FLOAT_TOLERANCE
                    )
                    preferred_min_units = math.ceil(
                        target.preferred_min_hours / ALLOCATION_STEP - _FLOAT_TOLERANCE
                    )
                    if assigned_units[manager_index] + account_remaining <= preferred_max_units:
                        allocated_units = account_remaining
                    elif assigned_units[manager_index] >= preferred_min_units:
                        manager_index += 1
                        continue
                    else:
                        allocated_units = min(account_remaining, goal_remaining)
                else:
                    overshoot = assigned_units[manager_index] + account_remaining - manager_units[manager_index]
                    undershoot = manager_units[manager_index] - assigned_units[manager_index]
                    if overshoot <= undershoot:
                        allocated_units = account_remaining
                    elif goal_remaining:
                        allocated_units = min(account_remaining, goal_remaining)
                    else:
                        manager_index += 1
                        continue
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
    """Acerca el reparto a sus objetivos operativos sin deshacer cuentas estables."""
    records = list(assignments)
    manager_targets = tuple(targets)
    if not records or not manager_targets:
        return tuple(records)

    employee_by_manager = {item.manager_name: item.employee_id for item in manager_targets}
    assigned = _assigned_hours_by_manager(records)
    total_units = sum(_to_units(item.management_hours) for item in records)
    goal_units = _manager_goal_units(total_units, manager_targets)
    goals = {
        target.manager_name: _from_units(goal_units[index])
        for index, target in enumerate(manager_targets)
    }

    moved = True
    while moved:
        moved = False
        donors = sorted(
            manager_targets,
            key=lambda item: (assigned[item.manager_name] - goals[item.manager_name], normalize_identifier(item.manager_name)),
            reverse=True,
        )
        receivers = sorted(
            manager_targets,
            key=lambda item: (assigned[item.manager_name] - goals[item.manager_name], normalize_identifier(item.manager_name)),
        )
        for donor in donors:
            if assigned[donor.manager_name] <= goals[donor.manager_name] + _FLOAT_TOLERANCE:
                continue
            for receiver in receivers:
                if receiver.manager_name == donor.manager_name:
                    continue
                if assigned[receiver.manager_name] >= goals[receiver.manager_name] - _FLOAT_TOLERANCE:
                    continue
                candidate_assigned = dict(assigned)
                candidate_assigned[donor.manager_name] -= ALLOCATION_STEP
                candidate_assigned[receiver.manager_name] += ALLOCATION_STEP
                if _total_absolute_deviation(candidate_assigned, goals) >= _total_absolute_deviation(assigned, goals):
                    continue
                receiver_accounts = {
                    item.account_code for item in records if item.manager_name == receiver.manager_name
                }
                donor_index = _transferable_assignment_index(records, donor.manager_name, receiver.manager_name)
                if donor_index is None:
                    continue
                donor_record = records[donor_index]
                if (
                    donor_record.account_code not in receiver_accounts
                    and assigned[donor.manager_name] <= donor.preferred_max_hours + _FLOAT_TOLERANCE
                ):
                    continue
                _transfer_half_hour(
                    records,
                    donor_index,
                    receiver.manager_name,
                    employee_by_manager[receiver.manager_name],
                )
                records = _merge_assignments(records)
                assigned[donor.manager_name] -= ALLOCATION_STEP
                assigned[receiver.manager_name] += ALLOCATION_STEP
                moved = True
                break
            if moved:
                break
    return tuple(records)


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
        if target.monthly_workable_hours is None:
            status = AllocationStatus.CAPACITY_DATA_MISSING
        elif assigned_hours > target.base_target_hours + _FLOAT_TOLERANCE:
            status = AllocationStatus.ABOVE_TARGET_DUE_TO_OVERFLOW
        elif assigned_hours + _FLOAT_TOLERANCE >= target.preferred_min_hours:
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
            preferred_min_hours=target.preferred_min_hours,
            preferred_max_hours=target.preferred_max_hours,
            overflow_hours_assigned=max(assigned_hours - target.base_target_hours, 0.0),
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
    initial = allocate_accounts_to_managers(analysis.sheet_name, records, targets)
    assignments = rebalance_allocations(initial, targets)
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
    incidents = tuple(
        f"MANAGER_MONTHLY_CAPACITY_NOT_FOUND: {item.manager_name} ({analysis.sheet_name})"
        for item in targets
        if item.monthly_workable_hours is None
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
        incidents=incidents,
        total_base_target_hours=total_base_target,
        overflow_hours=overflow_hours,
    )


def _manager_goal_units(total_units: int, targets: tuple[ManagerTarget, ...]) -> tuple[int, ...]:
    if total_units == 0:
        return tuple(0 for _ in targets)
    if not targets:
        return ()
    total_hours = _from_units(total_units)
    total_base_target = sum(target.base_target_hours for target in targets)
    if total_hours > total_base_target + _FLOAT_TOLERANCE:
        overflow_share = (total_hours - total_base_target) / len(targets)
        desired_units = [
            (target.base_target_hours + overflow_share) / ALLOCATION_STEP
            for target in targets
        ]
        return _round_units_to_total(desired_units, total_units, targets)

    result: list[int] = []
    remaining = total_units
    for target in targets:
        desired = round(target.base_target_hours / ALLOCATION_STEP)
        assigned = min(desired, remaining)
        result.append(assigned)
        remaining -= assigned
    if remaining:
        result[-1] += remaining
    return tuple(result)


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


def _transferable_assignment_index(
    records: list[ManagementAssignment],
    donor_name: str,
    receiver_name: str,
) -> int | None:
    receiver_accounts = {
        item.account_code for item in records if item.manager_name == receiver_name
    }
    candidates = [
        (index, item)
        for index, item in enumerate(records)
        if item.manager_name == donor_name and item.management_hours >= ALLOCATION_STEP
    ]
    candidates.sort(key=lambda pair: (
        pair[1].account_code not in receiver_accounts,
        _natural_key(pair[1].account_code),
    ))
    return candidates[0][0] if candidates else None


def _transfer_half_hour(
    records: list[ManagementAssignment],
    donor_index: int,
    receiver_name: str,
    receiver_employee_id: str,
) -> None:
    donor = records[donor_index]
    if math.isclose(donor.management_hours, ALLOCATION_STEP, abs_tol=_FLOAT_TOLERANCE):
        records.pop(donor_index)
    else:
        records[donor_index] = replace(
            donor,
            management_hours=donor.management_hours - ALLOCATION_STEP,
        )
    records.append(replace(
        donor,
        employee_id=receiver_employee_id,
        manager_name=receiver_name,
        management_hours=ALLOCATION_STEP,
    ))


def _merge_assignments(records: Iterable[ManagementAssignment]) -> list[ManagementAssignment]:
    merged: dict[tuple[str, str], ManagementAssignment] = {}
    for item in records:
        key = (item.manager_name, item.account_code)
        if key in merged:
            merged[key] = replace(
                merged[key],
                management_hours=merged[key].management_hours + item.management_hours,
            )
        else:
            merged[key] = item
    return sorted(
        merged.values(),
        key=lambda item: (normalize_identifier(item.manager_name), _natural_key(item.account_code)),
    )


def _assigned_hours_by_manager(assignments: Iterable[ManagementAssignment]) -> dict[str, float]:
    result: dict[str, float] = defaultdict(float)
    for item in assignments:
        result[item.manager_name] += item.management_hours
    return result


def _total_absolute_deviation(assigned: dict[str, float], targets: dict[str, float]) -> float:
    return sum(abs(assigned[name] - target) for name, target in targets.items())


def _to_units(hours: float) -> int:
    units = round(hours / ALLOCATION_STEP)
    if not math.isclose(hours, units * ALLOCATION_STEP, abs_tol=_FLOAT_TOLERANCE):
        raise ValueError("Las horas de gestión deben ser múltiplos de 0,5.")
    return units


def _from_units(units: int) -> float:
    return units * ALLOCATION_STEP


def _natural_key(value: str) -> tuple[tuple[int, object], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in _NATURAL_PART.split(value)
        if part
    )


__all__ = [
    "allocate_accounts_to_managers",
    "build_manager_summary",
    "calculate_management_allocation",
    "calculate_manager_targets",
    "rebalance_allocations",
    "validate_allocation",
]
