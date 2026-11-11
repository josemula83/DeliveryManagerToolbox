"""Reglas de negocio y métricas de People 360."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from statistics import fmean

from delivery_manager_toolbox.modules.people_360.models import (
    EmployeeTracking, GlobalMetrics, Journal, JournalKind, People360Dashboard,
    People360Dataset, Priority, PRIORITY_RANK, SupervisorSummary,
)


def calculate_supervisor_priority(
    coverage: float | None, medium_min: float = 60.0, ok_above: float = 80.0,
) -> Priority:
    if coverage is None:
        return Priority.NO_DATA
    if coverage > ok_above:
        return Priority.OK
    if coverage >= medium_min:
        return Priority.MEDIUM
    return Priority.CRITICAL


def is_bitacora_overdue(
    last_date: datetime | date | None,
    reference_date: datetime | date,
    threshold_days: int = 60,
) -> bool:
    if last_date is None:
        return False
    last_day = last_date.date() if isinstance(last_date, datetime) else last_date
    reference_day = reference_date.date() if isinstance(reference_date, datetime) else reference_date
    return (reference_day - last_day).days > threshold_days


def build_dashboard(
    dataset: People360Dataset,
    reference_date: datetime | date | None = None,
    threshold_days: int = 60,
    medium_min: float = 60.0,
    ok_above: float = 80.0,
) -> People360Dashboard:
    reference = reference_date or datetime.now()
    journals_by_employee: dict[str, list[Journal]] = defaultdict(list)
    meetings_by_employee = defaultdict(list)
    for journal in dataset.journals:
        journals_by_employee[journal.employee_id].append(journal)
    for meeting in dataset.meetings:
        meetings_by_employee[meeting.employee_id].append(meeting)

    tracking: list[EmployeeTracking] = []
    for employee in dataset.employees:
        journals = journals_by_employee[employee.employee_id]
        valid_employee = [j for j in journals if j.is_valid and j.kind is JournalKind.EMPLOYEE]
        valid_supervisor = [j for j in journals if j.is_valid and j.kind is JournalKind.SUPERVISOR]
        invalid_employee = any(not j.is_valid and j.kind is JournalKind.EMPLOYEE for j in journals)
        invalid_supervisor = any(not j.is_valid and j.kind is JournalKind.SUPERVISOR for j in journals)
        employee_date = _latest_date(valid_employee)
        supervisor_date = _latest_date(valid_supervisor)
        days = _days_since(supervisor_date, reference)
        meetings = [m for m in meetings_by_employee[employee.employee_id] if m.is_valid]
        observations: list[str] = []
        if not employee.supervisor_n1:
            observations.append("Sin Supervisor N1")
        if invalid_employee or invalid_supervisor:
            observations.append("Existen registros no válidos")
        tracking.append(EmployeeTracking(
            employee=employee,
            employee_journal_date=employee_date,
            employee_journal_state=_journal_state(employee_date, invalid_employee),
            supervisor_journal_date=supervisor_date,
            supervisor_journal_state=_journal_state(supervisor_date, invalid_supervisor),
            days_since_supervisor_journal=days,
            overdue=is_bitacora_overdue(supervisor_date, reference, threshold_days),
            meetings_completed=sum(m.is_completed for m in meetings),
            meetings_pending=sum(m.is_pending for m in meetings),
            coverage_employee=100.0 if employee_date else 0.0,
            coverage_supervisor=100.0 if supervisor_date else 0.0,
            observations="; ".join(observations),
        ))

    by_supervisor: dict[str, list[EmployeeTracking]] = defaultdict(list)
    for item in tracking:
        if item.employee.supervisor_n1:
            by_supervisor[item.employee.supervisor_n1].append(item)
    supervisors: list[SupervisorSummary] = []
    for supervisor, team in by_supervisor.items():
        employee_coverage = _coverage(team, "employee_journal_date")
        supervisor_coverage = _coverage(team, "supervisor_journal_date")
        dates = [d for item in team for d in (item.employee_journal_date, item.supervisor_journal_date) if d]
        supervisors.append(SupervisorSummary(
            supervisor=supervisor, employee_count=len(team), employee_coverage=employee_coverage,
            supervisor_coverage=supervisor_coverage, overdue_count=sum(item.overdue for item in team),
            meetings_completed=sum(item.meetings_completed for item in team),
            meetings_pending=sum(item.meetings_pending for item in team),
            priority=calculate_supervisor_priority(supervisor_coverage, medium_min, ok_above),
            last_update=max(dates, default=None),
        ))
    supervisors.sort(key=lambda s: (PRIORITY_RANK[s.priority], s.supervisor_coverage if s.supervisor_coverage is not None else float("inf"), s.supervisor.casefold()))
    metrics = GlobalMetrics(
        total_employees=len(tracking), total_supervisors=len(supervisors),
        average_employee_coverage=_mean([s.employee_coverage for s in supervisors]),
        average_supervisor_coverage=_mean([s.supervisor_coverage for s in supervisors]),
        critical_supervisors=sum(s.priority is Priority.CRITICAL for s in supervisors),
        medium_supervisors=sum(s.priority is Priority.MEDIUM for s in supervisors),
        ok_supervisors=sum(s.priority is Priority.OK for s in supervisors),
        overdue_people=sum(item.overdue for item in tracking),
        pending_meetings=sum(item.meetings_pending for item in tracking),
    )
    return People360Dashboard(dataset, tuple(tracking), tuple(supervisors), metrics)


def _latest_date(journals: list[Journal]) -> datetime | None:
    return max((j.occurred_at for j in journals if j.occurred_at), default=None)


def _days_since(value: datetime | None, reference: datetime | date) -> int | None:
    if value is None:
        return None
    reference_day = reference.date() if isinstance(reference, datetime) else reference
    return (reference_day - value.date()).days


def _journal_state(value: datetime | None, has_invalid: bool) -> str:
    if value:
        return "Válida"
    return "No válida" if has_invalid else "Sin bitácora"


def _coverage(team: list[EmployeeTracking], field: str) -> float | None:
    if not team:
        return None
    return round(sum(getattr(item, field) is not None for item in team) * 100 / len(team), 2)


def _mean(values: list[float | None]) -> float | None:
    available = [value for value in values if value is not None]
    return round(fmean(available), 2) if available else None
