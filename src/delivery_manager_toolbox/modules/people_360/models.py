"""Modelo normalizado y estable de People 360."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Priority(str, Enum):
    CRITICAL = "Crítica"
    MEDIUM = "Media"
    OK = "OK"
    NO_DATA = "Sin datos"


PRIORITY_RANK = {
    Priority.CRITICAL: 0,
    Priority.MEDIUM: 1,
    Priority.OK: 2,
    Priority.NO_DATA: 3,
}


class JournalKind(str, Enum):
    EMPLOYEE = "empleado"
    SUPERVISOR = "supervisor"


@dataclass(frozen=True, slots=True)
class Employee:
    employee_id: str
    name: str
    supervisor_n1: str | None
    supervisor_n2: str | None = None
    email: str | None = None
    status: str | None = None
    technology: str | None = None
    client_technology: str | None = None
    location: str | None = None
    job_role: str | None = None
    area: str | None = None
    client: str | None = None
    service: str | None = None
    team: str | None = None
    last_modified: datetime | None = None


@dataclass(frozen=True, slots=True)
class Journal:
    employee_id: str
    employee_name: str
    kind: JournalKind | None
    occurred_at: datetime | None
    content: str | None
    is_valid: bool
    issue: str | None = None


@dataclass(frozen=True, slots=True)
class Meeting:
    employee_id: str
    employee_name: str
    requested_at: datetime | None
    status: str | None
    managed_at: datetime | None
    managed_by: str | None
    is_valid: bool

    @property
    def is_pending(self) -> bool:
        return (self.status or "").strip().casefold() == "pendiente"

    @property
    def is_completed(self) -> bool:
        return (self.status or "").strip().casefold() == "gestionada"


@dataclass(frozen=True, slots=True)
class DataIssue:
    sheet: str
    row: int
    message: str


@dataclass(frozen=True, slots=True)
class People360Dataset:
    employees: tuple[Employee, ...]
    journals: tuple[Journal, ...]
    meetings: tuple[Meeting, ...]
    issues: tuple[DataIssue, ...] = ()
    source_name: str = ""
    loaded_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True, slots=True)
class EmployeeTracking:
    employee: Employee
    employee_journal_date: datetime | None
    employee_journal_state: str
    supervisor_journal_date: datetime | None
    supervisor_journal_state: str
    days_since_supervisor_journal: int | None
    overdue: bool
    meetings_completed: int
    meetings_pending: int
    coverage_employee: float | None
    coverage_supervisor: float | None
    observations: str


@dataclass(frozen=True, slots=True)
class SupervisorSummary:
    supervisor: str
    employee_count: int
    employee_coverage: float | None
    supervisor_coverage: float | None
    overdue_count: int
    meetings_completed: int
    meetings_pending: int
    priority: Priority
    last_update: datetime | None


@dataclass(frozen=True, slots=True)
class GlobalMetrics:
    total_employees: int
    total_supervisors: int
    average_employee_coverage: float | None
    average_supervisor_coverage: float | None
    critical_supervisors: int
    medium_supervisors: int
    ok_supervisors: int
    overdue_people: int
    pending_meetings: int


@dataclass(frozen=True, slots=True)
class People360Dashboard:
    dataset: People360Dataset
    employees: tuple[EmployeeTracking, ...]
    supervisors: tuple[SupervisorSummary, ...]
    metrics: GlobalMetrics
