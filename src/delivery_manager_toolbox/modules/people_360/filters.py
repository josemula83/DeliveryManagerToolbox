"""Filtros puros sobre el modelo calculado."""

from __future__ import annotations

from delivery_manager_toolbox.modules.people_360.models import EmployeeTracking, People360Dashboard, Priority


def normalize_search(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def filter_employees(
    dashboard: People360Dashboard,
    supervisor: str | None = None,
    priority: Priority | None = None,
    mode: str = "Todos",
    search: str = "",
) -> tuple[EmployeeTracking, ...]:
    priority_by_supervisor = {item.supervisor: item.priority for item in dashboard.supervisors}
    query = normalize_search(search)
    result: list[EmployeeTracking] = []
    for item in dashboard.employees:
        employee = item.employee
        if supervisor and employee.supervisor_n1 != supervisor:
            continue
        if priority and priority_by_supervisor.get(employee.supervisor_n1 or "") is not priority:
            continue
        if query and query not in normalize_search(employee.name) and query not in normalize_search(employee.supervisor_n1 or ""):
            continue
        if mode == "Críticos" and priority_by_supervisor.get(employee.supervisor_n1 or "") is not Priority.CRITICAL:
            continue
        if mode == ">60 días" and not item.overdue:
            continue
        if mode == "Sin bitácora" and item.employee_journal_date is not None and item.supervisor_journal_date is not None:
            continue
        if mode == "Reunión pendiente" and not item.meetings_pending:
            continue
        if mode == "Cobertura incompleta" and item.employee_journal_date is not None and item.supervisor_journal_date is not None:
            continue
        result.append(item)
    return tuple(sorted(result, key=lambda item: item.employee.name.casefold()))
