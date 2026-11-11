"""Modelo normalizado compartido por CSV, WebView2 y Aplantillado."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from delivery_manager_toolbox.modules.jira.service import CSV_PREFIX


class ExtractionResultError(ValueError):
    """Mensaje de extracción ausente o estructuralmente inválido."""


def normalize_name(value: str) -> str:
    return " ".join(value.split()).casefold()


@dataclass(frozen=True, slots=True)
class ExtractionRow:
    pkey: str
    person: str
    work_date: date | None
    hours: Decimal | None
    task: str
    comment: str
    original_values: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    date_from: date
    date_to: date
    pkeys: tuple[str, ...]
    header: tuple[str, ...]
    rows: tuple[ExtractionRow, ...]

    @property
    def people(self) -> tuple[str, ...]:
        names: dict[str, str] = {}
        for row in self.rows:
            key = normalize_name(row.person)
            if key and key not in names:
                names[key] = " ".join(row.person.split())
        return tuple(names.values())

    @property
    def years(self) -> tuple[int, ...]:
        return tuple(sorted({row.work_date.year for row in self.rows if row.work_date}, reverse=True))

    def to_transport(self) -> dict[str, object]:
        return {
            "from": self.date_from.isoformat(),
            "to": self.date_to.isoformat(),
            "pkeys": list(self.pkeys),
            "header": list(self.header),
            "rows": [
                {
                    "pkey": row.pkey,
                    "person": row.person,
                    "date": row.work_date.isoformat() if row.work_date else None,
                    "hours": str(row.hours) if row.hours is not None else None,
                    "task": row.task,
                    "comment": row.comment,
                    "original": list(row.original_values),
                }
                for row in self.rows
            ],
        }


def extraction_from_webview(message: Mapping[str, object]) -> ExtractionResult:
    if message.get("type") != "jira_extraction_completed":
        raise ExtractionResultError("Tipo de mensaje de extracción no reconocido.")
    payload = message.get("payload")
    if not isinstance(payload, Mapping):
        raise ExtractionResultError("El mensaje no contiene un resultado válido.")

    date_from = _parse_date(payload.get("from"), "fecha inicial")
    date_to = _parse_date(payload.get("to"), "fecha final")
    if date_from > date_to:
        raise ExtractionResultError("La fecha inicial no puede ser posterior a la final.")
    pkeys = _strings(payload.get("pkeys"), "PKEYS")
    raw_header = _strings(payload.get("header"), "cabecera", allow_empty_values=True)
    records = payload.get("records")
    if not isinstance(records, list):
        raise ExtractionResultError("Los registros de la extracción no son válidos.")

    full_header = (*CSV_PREFIX, *raw_header)
    indexes = _header_indexes(raw_header)
    rows = tuple(_normalize_row(record, indexes, len(full_header)) for record in records)
    return ExtractionResult(date_from, date_to, pkeys, full_header, rows)


def extraction_from_transport(payload: Mapping[str, object]) -> ExtractionResult:
    rows_value = payload.get("rows")
    if not isinstance(rows_value, list):
        raise ExtractionResultError("El transporte no contiene registros válidos.")
    rows: list[ExtractionRow] = []
    for value in rows_value:
        if not isinstance(value, Mapping):
            raise ExtractionResultError("El transporte contiene una fila no válida.")
        work_date = date.fromisoformat(str(value["date"])) if value.get("date") else None
        hours = Decimal(str(value["hours"])) if value.get("hours") is not None else None
        original = value.get("original")
        if not isinstance(original, list):
            raise ExtractionResultError("Faltan los valores originales de una fila.")
        rows.append(
            ExtractionRow(
                pkey=str(value.get("pkey", "")),
                person=str(value.get("person", "")),
                work_date=work_date,
                hours=hours,
                task=str(value.get("task", "")),
                comment=str(value.get("comment", "")),
                original_values=tuple(original),
            )
        )
    return ExtractionResult(
        _parse_date(payload.get("from"), "fecha inicial"),
        _parse_date(payload.get("to"), "fecha final"),
        _strings(payload.get("pkeys"), "PKEYS"),
        _strings(payload.get("header"), "cabecera", allow_empty_values=True),
        tuple(rows),
    )


def _normalize_row(
    record: object,
    indexes: Mapping[str, int | None],
    expected_length: int,
) -> ExtractionRow:
    if not isinstance(record, list) or len(record) != expected_length:
        raise ExtractionResultError("Una fila no coincide con la cabecera recibida.")
    raw = record[4:]
    person = _cell(raw, indexes["person"])
    task = _cell(raw, indexes["task"]) or "Sin título"
    comment = _cell(raw, indexes["comment"])
    date_text = _cell(raw, indexes["date"])
    hours_text = _cell(raw, indexes["hours"]).replace(",", ".")
    try:
        work_date = date.fromisoformat(date_text) if date_text else None
    except ValueError:
        work_date = None
    try:
        hours = Decimal(hours_text) if hours_text else Decimal("0")
    except InvalidOperation:
        hours = None
    return ExtractionRow(
        pkey=str(record[0] or "").strip(),
        person=" ".join(person.split()),
        work_date=work_date,
        hours=hours,
        task=task,
        comment=comment,
        original_values=tuple(record),
    )


def _header_indexes(header: Sequence[str]) -> dict[str, int | None]:
    normalized: dict[str, int] = {}
    for index, name in enumerate(header):
        normalized.setdefault(" ".join(name.split()).casefold(), index)

    def first(*aliases: str) -> int | None:
        return next((normalized[alias.casefold()] for alias in aliases if alias.casefold() in normalized), None)

    indexes = {
        "person": first("Name"),
        "date": first("Date"),
        "hours": first("Hours"),
        "task": first("Sub-Task Summary", "Summary"),
        "comment": first("Comment"),
    }
    missing = [key for key in ("person", "date", "hours") if indexes[key] is None]
    if missing:
        raise ExtractionResultError("La cabecera no contiene Name, Date y Hours.")
    return indexes


def _cell(values: Sequence[object], index: int | None) -> str:
    if index is None or index >= len(values):
        return ""
    return str(values[index] or "").strip()


def _parse_date(value: object, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise ExtractionResultError(f"La {label} no es válida.") from error


def _strings(value: object, label: str, allow_empty_values: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ExtractionResultError(f"La lista de {label} no es válida.")
    result = tuple(str(item).strip() for item in value)
    if not allow_empty_values and any(not item for item in result):
        raise ExtractionResultError(f"La lista de {label} contiene valores vacíos.")
    return result


__all__ = [
    "ExtractionResult",
    "ExtractionResultError",
    "ExtractionRow",
    "extraction_from_transport",
    "extraction_from_webview",
    "normalize_name",
]
