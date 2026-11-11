"""Fuentes de datos y normalización del libro People 360."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO, Protocol

from openpyxl import load_workbook

from delivery_manager_toolbox.core.logging import get_logger
from delivery_manager_toolbox.modules.people_360.models import (
    DataIssue,
    Employee,
    Journal,
    JournalKind,
    Meeting,
    People360Dataset,
)


LOGGER = get_logger(__name__)
REQUIRED_SHEETS = ("Empleados", "Bitacoras", "Historico reuniones")


class People360Repository(Protocol):
    def load(self) -> People360Dataset: ...


class ExcelPeople360Repository:
    """Lee el esquema real del XLSX desde ruta o contenido en memoria."""

    def __init__(self, source: Path | str | BinaryIO, source_name: str | None = None):
        self._source = source
        self._source_name = source_name or Path(str(source)).name

    def load(self) -> People360Dataset:
        workbook = load_workbook(self._source, read_only=True, data_only=True)
        try:
            missing = [name for name in REQUIRED_SHEETS if name not in workbook.sheetnames]
            if missing:
                raise ValueError(f"Faltan hojas requeridas: {', '.join(missing)}")
            issues: list[DataIssue] = []
            employees = _read_employees(workbook["Empleados"], issues)
            journals = _read_journals(workbook["Bitacoras"], issues)
            meetings = _read_meetings(workbook["Historico reuniones"], issues)
            LOGGER.info(
                "People 360 cargado: %d empleados, %d bitácoras, %d reuniones, %d incidencias",
                len(employees), len(journals), len(meetings), len(issues),
            )
            return People360Dataset(
                employees=tuple(employees), journals=tuple(journals), meetings=tuple(meetings),
                issues=tuple(issues), source_name=self._source_name,
            )
        finally:
            workbook.close()


class LocalPeople360Repository(ExcelPeople360Repository):
    """Fuente local habilitada únicamente por configuración explícita."""

    def __init__(self, path: Path):
        super().__init__(path, path.name)


def _rows(sheet):
    iterator = sheet.iter_rows(values_only=True)
    headers = [str(value).strip() if value is not None else "" for value in next(iterator)]
    for row_number, values in enumerate(iterator, start=2):
        yield row_number, dict(zip(headers, values, strict=False))


def _text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = _text(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).replace(tzinfo=None)
    except ValueError:
        pass
    for pattern in ("%d/%m/%Y", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _read_employees(sheet, issues: list[DataIssue]) -> list[Employee]:
    by_id: dict[str, Employee] = {}
    for row_number, row in _rows(sheet):
        employee_id = _text(row.get("numeroEmpleado"))
        name = _text(row.get("nombre"))
        if not employee_id or not name:
            issues.append(DataIssue(sheet.title, row_number, "Empleado sin identificador o nombre"))
            continue
        employee = Employee(
            employee_id=employee_id, name=name, supervisor_n1=_text(row.get("supervisor_n1")),
            supervisor_n2=_text(row.get("supervisor_n2")), email=_text(row.get("email")),
            status=_text(row.get("estado")), technology=_text(row.get("tecnologia")),
            client_technology=_text(row.get("tecnologiaCliente")), location=_text(row.get("ubicacion")),
            job_role=_text(row.get("jobRole")), area=_text(row.get("area")), client=_text(row.get("cliente")),
            service=_text(row.get("servicio")), team=_text(row.get("equipo")),
            last_modified=parse_datetime(row.get("fecha_ultima_modificacion")),
        )
        previous = by_id.get(employee_id)
        if previous:
            issues.append(DataIssue(sheet.title, row_number, "Identificador de empleado duplicado"))
            previous_date = previous.last_modified or datetime.min
            current_date = employee.last_modified or datetime.min
            if current_date < previous_date:
                continue
        by_id[employee_id] = employee
    return list(by_id.values())


def _read_journals(sheet, issues: list[DataIssue]) -> list[Journal]:
    result: list[Journal] = []
    for row_number, row in _rows(sheet):
        employee_id = _text(row.get("numeroEmpleado")) or ""
        raw_kind = (_text(row.get("tipo")) or "").casefold()
        kind = JournalKind(raw_kind) if raw_kind in {item.value for item in JournalKind} else None
        content = _text(row.get("bitacora_empleado" if kind is JournalKind.EMPLOYEE else "bitacora_supervisor")) if kind else None
        occurred_at = parse_datetime(row.get("fecha"))
        valid = bool(employee_id and kind and occurred_at and content)
        issue = None
        if not valid:
            issue = "Bitácora incompleta, con tipo desconocido o fecha inválida"
            issues.append(DataIssue(sheet.title, row_number, issue))
        result.append(Journal(employee_id, _text(row.get("nombre")) or "", kind, occurred_at, content, valid, issue))
    return result


def _read_meetings(sheet, issues: list[DataIssue]) -> list[Meeting]:
    result: list[Meeting] = []
    for row_number, row in _rows(sheet):
        employee_id = _text(row.get("numeroEmpleado")) or ""
        status = _text(row.get("estado"))
        requested_at = parse_datetime(row.get("fechaSolicitud"))
        valid = bool(employee_id and status and requested_at)
        if not valid:
            issues.append(DataIssue(sheet.title, row_number, "Reunión incompleta o con fecha inválida"))
        result.append(Meeting(employee_id, _text(row.get("nombre")) or "", requested_at, status, parse_datetime(row.get("fechaGestion")), _text(row.get("gestionadaPor")), valid))
    return result
