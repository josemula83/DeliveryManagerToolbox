"""Reglas puras del extractor JIRA, compartidas con sus pruebas y documentación."""

from __future__ import annotations

import calendar
import csv
from datetime import date, timedelta
from io import StringIO
import re
from typing import Iterable, Mapping, Sequence
from urllib.parse import urlencode

from delivery_manager_toolbox.modules.jira.constants import (
    DATE_COLUMN_INDEX,
    DEFAULT_PROJECTS,
    REQUEST_BODY,
    REQUEST_HEADERS,
    TIMESHEET_ENDPOINT,
)
from delivery_manager_toolbox.modules.jira.models import (
    DateBlock,
    JiraRequestSpec,
    ParsedTimesheet,
)


CSV_PREFIX = ("PKEY", "Año", "Mes", "Año-Mes")
_YEAR_MONTH = re.compile(r"^\d{4}-\d{2}")


class JiraExtractionValidationError(ValueError):
    """Datos de entrada o respuesta incompatibles con el extractor."""


def default_dates(today: date | None = None) -> tuple[date, date]:
    current = today or date.today()
    return current.replace(day=1), current


def validate_selection(start: date, end: date, projects: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(projects)
    if start > end:
        raise JiraExtractionValidationError("La fecha inicial no puede ser posterior a la final.")
    if not selected:
        raise JiraExtractionValidationError("Selecciona al menos un proyecto.")
    unknown = tuple(project for project in selected if project not in DEFAULT_PROJECTS)
    if unknown:
        raise JiraExtractionValidationError("Proyecto no permitido: " + ", ".join(unknown))
    if len(selected) != len(set(selected)):
        raise JiraExtractionValidationError("La selección contiene proyectos duplicados.")
    return selected


def split_monthly_range(start: date, end: date) -> tuple[DateBlock, ...]:
    if start > end:
        raise JiraExtractionValidationError("La fecha inicial no puede ser posterior a la final.")
    blocks: list[DateBlock] = []
    cursor = start
    while cursor <= end:
        last_day = calendar.monthrange(cursor.year, cursor.month)[1]
        month_end = cursor.replace(day=last_day)
        block_end = min(month_end, end)
        blocks.append(DateBlock(cursor, block_end))
        cursor = block_end + timedelta(days=1)
    return tuple(blocks)


def build_request_spec(project: str, block: DateBlock) -> JiraRequestSpec:
    validate_selection(block.start, block.end, (project,))
    query = urlencode({
        "pkey": project,
        "startDate": block.start.isoformat(),
        "endDate": block.end.isoformat(),
    })
    return JiraRequestSpec(
        project=project,
        date_block=block,
        url=f"{TIMESHEET_ENDPOINT}?{query}",
        method="POST",
        headers=dict(REQUEST_HEADERS),
        body={"users": list(REQUEST_BODY["users"])},
    )


def build_request_plan(
    start: date,
    end: date,
    projects: Iterable[str],
) -> tuple[JiraRequestSpec, ...]:
    selected = validate_selection(start, end, projects)
    blocks = split_monthly_range(start, end)
    return tuple(build_request_spec(project, block) for project in selected for block in blocks)


def parse_timesheet_response(project: str, payload: Mapping[str, object]) -> ParsedTimesheet:
    validate_selection(date.min, date.min, (project,))
    data = payload.get("data")
    if not isinstance(data, list):
        raise JiraExtractionValidationError("Respuesta sin datos.")
    if not data or not isinstance(data[0], list) or not data[0]:
        raise JiraExtractionValidationError("Respuesta sin una cabecera válida.")
    header = tuple(str(value) if value is not None else "" for value in data[0])
    rows: list[tuple[object, ...]] = []
    for raw_row in data[1:]:
        if not isinstance(raw_row, list):
            raise JiraExtractionValidationError("La respuesta contiene una fila no válida.")
        year, month, year_month = _period_columns(raw_row)
        rows.append((project, year, month, year_month, *raw_row))
    return ParsedTimesheet((*CSV_PREFIX, *header), tuple(rows))


def consolidate_timesheets(reports: Iterable[ParsedTimesheet]) -> ParsedTimesheet | None:
    records = tuple(reports)
    if not records:
        return None
    header = records[0].header
    rows: list[tuple[object, ...]] = []
    for report in records:
        if report.header != header:
            raise JiraExtractionValidationError(
                "Las consultas de JIRA han devuelto cabeceras incompatibles."
            )
        rows.extend(report.rows)
    return ParsedTimesheet(header, tuple(rows))


def serialize_csv(report: ParsedTimesheet) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(
        output,
        delimiter=";",
        quotechar='"',
        quoting=csv.QUOTE_ALL,
        lineterminator="\n",
    )
    writer.writerow(report.header)
    writer.writerows(report.rows)
    return output.getvalue().encode("utf-8-sig")


def export_filename(start: date, end: date) -> str:
    if start > end:
        raise JiraExtractionValidationError("La fecha inicial no puede ser posterior a la final.")
    return f"Incurridos_BBVA_{start.isoformat()}_{end.isoformat()}.csv"


def _period_columns(row: Sequence[object]) -> tuple[str, str, str]:
    value = row[DATE_COLUMN_INDEX] if len(row) > DATE_COLUMN_INDEX else ""
    text = str(value or "")
    if not _YEAR_MONTH.match(text):
        return "", "", ""
    return text[:4], text[5:7], text[:7]


__all__ = [
    "CSV_PREFIX",
    "JiraExtractionValidationError",
    "build_request_plan",
    "build_request_spec",
    "consolidate_timesheets",
    "default_dates",
    "export_filename",
    "parse_timesheet_response",
    "serialize_csv",
    "split_monthly_range",
    "validate_selection",
]
