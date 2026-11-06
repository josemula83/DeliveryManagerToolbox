"""Calendario laboral teórico y descuentos por ausencias para Facturación."""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from datetime import date, datetime
import math
import re

from delivery_manager_toolbox.modules.facturacion.models import (
    ManagerWorkCalendar,
    Person,
    WorkCalendarDay,
)
from delivery_manager_toolbox.modules.facturacion.validation import normalize_identifier


_MONTH_NAMES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
_WEEKDAY_NAMES = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
_NON_WORKING_CODES = {
    "v",
    "a",
    "vacacion",
    "vacaciones",
    "c",
    "compensado",
    "compensados",
    "e",
    "enfermedad",
    "enfermedades",
    "baja",
    "f",
    "formacion",
    "festivo",
    "festivos",
    "ausencia",
    "ausencias",
}
_FILENAME_PERIOD = re.compile(r"(?<!\d)(20\d{2})[-_. ](0?[1-9]|1[0-2])(?!\d)")


def resolve_sheet_period(
    filename: str | None,
    sheet_name: str,
    workbook_sheet_names: Iterable[str],
    headers: Iterable[object] = (),
) -> tuple[int, int] | None:
    """Resuelve el periodo usando fechas de cabecera o la secuencia del workbook."""
    sheet_names = tuple(workbook_sheet_names)
    target_month = _month_from_text(sheet_name)
    header_years = {
        value.year for value in headers if isinstance(value, (date, datetime))
    }
    header_months = {
        value.month for value in headers if isinstance(value, (date, datetime))
    }
    if len(header_years) == 1 and len(header_months) == 1:
        return next(iter(header_years)), target_month or next(iter(header_months))
    if not filename:
        return None
    match = _FILENAME_PERIOD.search(filename)
    if match is None or target_month is None:
        return None
    anchor_year, anchor_month = int(match.group(1)), int(match.group(2))
    sheet_months = tuple(_month_from_text(name) for name in sheet_names)
    anchor_indexes = [index for index, month in enumerate(sheet_months) if month == anchor_month]
    target_indexes = [
        index
        for index, (name, month) in enumerate(zip(sheet_names, sheet_months, strict=True))
        if name == sheet_name and month == target_month
    ]
    if not target_indexes:
        return None
    if not anchor_indexes:
        year = anchor_year + (1 if target_month < anchor_month else 0)
        return year, target_month
    anchor_index = min(anchor_indexes, key=lambda index: abs(index - target_indexes[0]))
    target_index = target_indexes[0]
    year = anchor_year
    if target_index > anchor_index:
        previous = anchor_month
        for month in sheet_months[anchor_index + 1:target_index + 1]:
            if month is None:
                continue
            if month < previous:
                year += 1
            previous = month
    elif target_index < anchor_index:
        following = anchor_month
        for month in reversed(sheet_months[target_index:anchor_index]):
            if month is None:
                continue
            if month > following:
                year -= 1
            following = month
    return year, target_month


def build_theoretical_work_calendar(
    person: Person,
    sheet_name: str,
    year: int,
    month: int,
) -> ManagerWorkCalendar:
    """Genera L-J 9 h, viernes 7 h y fin de semana 0 h."""
    days: list[WorkCalendarDay] = []
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        current = date(year, month, day)
        theoretical = 9.0 if current.weekday() <= 3 else 7.0 if current.weekday() == 4 else 0.0
        days.append(WorkCalendarDay(
            day=day,
            date_label=current.strftime("%d/%m/%Y"),
            weekday_name=_WEEKDAY_NAMES[current.weekday()],
            theoretical_hours=theoretical,
            absence_code=None,
            capacity_hours=theoretical,
        ))
    theoretical_total = sum(item.theoretical_hours for item in days)
    return ManagerWorkCalendar(
        employee_id=person.employee_id,
        manager_name=person.name,
        sheet_name=sheet_name,
        year=year,
        month=month,
        daily_capacity=tuple(days),
        theoretical_monthly_hours=theoretical_total,
        absence_hours=0.0,
        monthly_workable_hours=theoretical_total,
    )


def is_non_working_code(value: object) -> bool:
    """Clasifica códigos no facturables sin aplicar coincidencias difusas."""
    if value is None or isinstance(value, (bool, int, float, date, datetime)):
        return False
    if not str(value).strip():
        return False
    normalized = normalize_identifier(value)
    if normalized in _NON_WORKING_CODES:
        return True
    words = set(re.findall(r"[a-z]+", normalized))
    return bool(words & (_NON_WORKING_CODES - {"v", "c", "e", "f"}))


def detect_person_absences(
    headers: tuple[object, ...],
    values: tuple[object, ...],
    year: int,
    month: int,
    row_label: object | None = None,
) -> dict[int, str]:
    """Extrae ausencias de cualquier fila perteneciente a una persona."""
    absences: dict[int, str] = {}
    row_is_absence = _is_non_working_row_label(row_label)
    for index, value in enumerate(values):
        value_is_absence = is_non_working_code(value)
        row_marks_absence = row_is_absence and (
            (isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0)
            or (isinstance(value, str) and bool(value.strip()))
        )
        if not value_is_absence and not row_marks_absence:
            continue
        day = _day_from_header(headers[index] if index < len(headers) else None, year, month)
        if day is not None:
            absences[day] = (
                str(value).strip()
                if value_is_absence
                else str(row_label).strip()
            )
    return absences


def apply_absences_to_calendar(
    work_calendar: ManagerWorkCalendar,
    absences: dict[int, str],
) -> ManagerWorkCalendar:
    """Anula la capacidad de cada día con ausencia y conserva su código."""
    days = tuple(
        WorkCalendarDay(
            day=item.day,
            date_label=item.date_label,
            weekday_name=item.weekday_name,
            theoretical_hours=item.theoretical_hours,
            absence_code=absences.get(item.day),
            capacity_hours=0.0 if item.day in absences else item.theoretical_hours,
        )
        for item in work_calendar.daily_capacity
    )
    absence_hours = sum(
        item.theoretical_hours for item in days if item.absence_code is not None
    )
    return ManagerWorkCalendar(
        employee_id=work_calendar.employee_id,
        manager_name=work_calendar.manager_name,
        sheet_name=work_calendar.sheet_name,
        year=work_calendar.year,
        month=work_calendar.month,
        daily_capacity=days,
        theoretical_monthly_hours=work_calendar.theoretical_monthly_hours,
        absence_hours=absence_hours,
        monthly_workable_hours=calculate_monthly_workable_hours(days),
    )


def calculate_monthly_workable_hours(days: Iterable[WorkCalendarDay]) -> float:
    return sum(item.capacity_hours for item in days)


def calculate_manager_base_target(monthly_workable_hours: float, percentage: float) -> float:
    return monthly_workable_hours * percentage / 100


def floor_to_half_hour(value: float) -> float:
    """Normaliza una capacidad hacia abajo al escalón común de 0,5 h."""
    return math.floor((value + 1e-12) / 0.5) * 0.5


def _month_from_text(value: str) -> int | None:
    normalized = normalize_identifier(value)
    return next((month for name, month in _MONTH_NAMES.items() if name in normalized), None)


def _is_non_working_row_label(value: object) -> bool:
    if value is None or not str(value).strip():
        return False
    return normalize_identifier(value) in _NON_WORKING_CODES


def _day_from_header(value: object, year: int, month: int) -> int | None:
    if isinstance(value, (date, datetime)):
        return value.day if value.year == year and value.month == month else None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and float(value).is_integer():
        day = int(value)
        return day if 1 <= day <= calendar.monthrange(year, month)[1] else None
    if isinstance(value, str) and value.strip().isdigit():
        day = int(value.strip())
        return day if 1 <= day <= calendar.monthrange(year, month)[1] else None
    return None


__all__ = [
    "apply_absences_to_calendar",
    "build_theoretical_work_calendar",
    "calculate_manager_base_target",
    "calculate_monthly_workable_hours",
    "detect_person_absences",
    "floor_to_half_hour",
    "is_non_working_code",
    "resolve_sheet_period",
]
