"""Lectura del Excel jerárquico de Facturación, sin reglas de reparto."""

from __future__ import annotations

from collections.abc import Iterable
from io import BytesIO
import math
import re

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

from delivery_manager_toolbox.modules.facturacion.models import (
    AccountAssignment,
    CapacitySource,
    ColumnMapping,
    DailyHours,
    ParsedAccountDescription,
    Person,
    PersonMonthlyWorkCapacity,
    SheetAnalysis,
    WorkbookAnalysis,
    WorksheetInfo,
    WorksheetPreview,
)
from delivery_manager_toolbox.modules.facturacion.validation import clean_display_value, normalize_identifier


class WorkbookInspectionError(ValueError):
    """El fichero no puede inspeccionarse como un libro Excel válido."""


_PERSON_IDENTIFIER = re.compile(r"^[A-Za-z]*\d+[A-Za-z0-9_-]*$")
_ACCOUNT_CODE = re.compile(r"\bEXT-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b", re.IGNORECASE)
_RATE = re.compile(r"\bTarifa\s+([0-9][0-9 .]*(?:[,.][0-9]+)?)", re.IGNORECASE)
_ACCOUNT_TYPE = re.compile(r"\bEUR\s*-\s*(.+?)\s*$", re.IGNORECASE)

_ALIASES = {
    "person": {"persona", "person", "empleado", "employee", "trabajador", "recurso", "nombre"},
    "account": {"cuenta", "account", "cliente", "client", "proyecto", "project"},
    "hours": {"horas", "hours", "hora", "hour", "horas incurridas", "incurred hours"},
    "rate": {"tarifa", "rate", "precio hora", "hourly rate", "importe hora"},
}


def inspect_workbook(contents: bytes, filename: str) -> WorkbookAnalysis:
    """Devuelve metadatos de las hojas sin interpretar sus filas."""
    workbook = _open_workbook(contents)
    try:
        worksheets: list[WorksheetInfo] = []
        suggested_sheet: str | None = None
        suggested_mapping = ColumnMapping()
        best_score = 0
        for worksheet in workbook.worksheets:
            headers = _read_headers(worksheet)
            mapping = detect_mapping(headers)
            score = sum(value is not None for value in (mapping.person, mapping.account, mapping.hours, mapping.rate))
            if score > best_score:
                best_score, suggested_sheet, suggested_mapping = score, worksheet.title, mapping
            worksheets.append(WorksheetInfo(
                name=worksheet.title,
                approximate_records=max((worksheet.max_row or 0) - 1, 0),
                columns=headers,
            ))
        return WorkbookAnalysis(
            filename=filename,
            worksheets=tuple(worksheets),
            suggested_sheet=suggested_sheet or (worksheets[0].name if worksheets else None),
            suggested_mapping=suggested_mapping,
        )
    finally:
        workbook.close()


def parse_hierarchical_worksheet(
    contents: bytes,
    sheet_name: str,
    *,
    raw_sample_size: int = 50,
) -> SheetAnalysis:
    """Convierte una hoja BBVA jerárquica en personas y asignaciones normalizadas."""
    workbook = _open_workbook(contents)
    try:
        if sheet_name not in workbook.sheetnames:
            raise WorkbookInspectionError(f"No existe la hoja seleccionada: {sheet_name}.")
        worksheet = workbook[sheet_name]
        max_column = worksheet.max_column or 0
        raw_columns = tuple(get_column_letter(index) for index in range(1, max_column + 1))
        raw_rows: list[tuple[object, ...]] = []
        people_by_id: dict[str, Person] = {}
        capacities_by_id: dict[str, PersonMonthlyWorkCapacity] = {}
        assignments: list[AccountAssignment] = []
        current_person: Person | None = None
        day_headers: tuple[object, ...] = ()
        scanned_rows = 0

        for row_number, values in enumerate(worksheet.iter_rows(values_only=True), 1):
            row = tuple(values[:max_column])
            scanned_rows += 1
            if len(raw_rows) < raw_sample_size:
                raw_rows.append(row)
            if row_number == 1:
                day_headers = tuple(row[2:])
                continue

            column_a = row[0] if len(row) > 0 else None
            column_b = row[1] if len(row) > 1 else None
            if _is_person_row(column_a, column_b):
                employee_id = _employee_id(column_a)
                current_person = people_by_id.setdefault(
                    normalize_identifier(employee_id),
                    Person(employee_id=employee_id, name=clean_display_value(column_b, "La persona")),
                )
                capacity = _person_monthly_capacity(current_person, day_headers, row[2:])
                if capacity is not None:
                    capacities_by_id[normalize_identifier(employee_id)] = capacity
                continue

            if current_person is not None and _is_account_row(column_a, column_b):
                description = clean_display_value(column_b, "La descripción de cuenta")
                parsed = parse_account_description(description)
                assignments.append(
                    AccountAssignment(
                        person=current_person,
                        account_code=parsed.account_code,
                        service_name=parsed.service_name,
                        rate=parsed.rate,
                        original_description=description,
                        account_type=parsed.account_type,
                        daily_hours=_daily_hours(day_headers, row[2:]),
                    )
                )

        return SheetAnalysis(
            sheet_name=sheet_name,
            people=tuple(people_by_id.values()),
            assignments=tuple(assignments),
            raw_columns=raw_columns,
            raw_rows=tuple(raw_rows),
            scanned_rows=scanned_rows,
            person_monthly_capacities=tuple(capacities_by_id.values()),
        )
    finally:
        workbook.close()


def parse_account_description(description: str) -> ParsedAccountDescription:
    """Extrae código EXT, servicio, tarifa y tipo de una descripción de cuenta."""
    cleaned = clean_display_value(description, "La descripción de cuenta")
    code_match = _ACCOUNT_CODE.search(cleaned)
    rate_match = _RATE.search(cleaned)
    type_match = _ACCOUNT_TYPE.search(cleaned)
    segments = [segment.strip() for segment in cleaned.split(">") if segment.strip()]
    service_name = ""
    if code_match:
        prefix = cleaned[: code_match.start()].rstrip(" >-")
        prefix_segments = [segment.strip() for segment in prefix.split(">") if segment.strip()]
        service_name = prefix_segments[-1] if prefix_segments else ""
    elif len(segments) > 1:
        service_name = segments[-2]
    elif segments:
        service_name = segments[0].split(" - Tarifa", 1)[0].strip()
    return ParsedAccountDescription(
        account_code=code_match.group(0).upper() if code_match else None,
        service_name=service_name,
        rate=_decimal_number(rate_match.group(1)) if rate_match else None,
        account_type=type_match.group(1).strip() if type_match else None,
    )


def detect_mapping(columns: Iterable[str]) -> ColumnMapping:
    """Mapeo conservado solo para formatos planos avanzados/manuales."""
    column_list = tuple(columns)

    def match(role: str) -> str | None:
        candidates = [column for column in column_list if normalize_identifier(column) in _ALIASES[role]]
        return candidates[0] if len(candidates) == 1 else None

    return ColumnMapping(person=match("person"), account=match("account"), hours=match("hours"), rate=match("rate"))


def preview_worksheet(
    contents: bytes,
    sheet_name: str,
    mapping: ColumnMapping,
    *,
    max_scan: int = 5_000,
    sample_size: int = 20,
    max_unique: int = 500,
) -> WorksheetPreview:
    """Lector plano legado, no usado por la vista principal."""
    workbook = _open_workbook(contents)
    try:
        if sheet_name not in workbook.sheetnames:
            raise WorkbookInspectionError(f"No existe la hoja seleccionada: {sheet_name}.")
        worksheet = workbook[sheet_name]
        columns = _read_headers(worksheet)
        selected = [value for value in (mapping.person, mapping.account, mapping.hours, mapping.rate) if value]
        if any(value not in columns for value in selected) or len(selected) != len(set(selected)):
            raise WorkbookInspectionError("El mapeo manual contiene columnas no válidas o repetidas.")
        positions = {column: index for index, column in enumerate(columns)}
        rows: list[tuple[object, ...]] = []
        people: dict[str, str] = {}
        accounts: dict[str, str] = {}
        rates: list[object] = []
        hours: list[object] = []
        scanned = 0
        for raw_row in worksheet.iter_rows(min_row=2, max_row=max_scan + 1, values_only=True):
            row = tuple(raw_row[: len(columns)])
            if not any(value is not None and str(value).strip() for value in row):
                continue
            scanned += 1
            if len(rows) < sample_size:
                rows.append(row)
            _collect_text(row, mapping.person, positions, people, max_unique)
            _collect_text(row, mapping.account, positions, accounts, max_unique)
            _collect_value(row, mapping.rate, positions, rates, max_unique)
            _collect_value(row, mapping.hours, positions, hours, max_unique)
        return WorksheetPreview(sheet_name, columns, tuple(rows), tuple(people.values()), tuple(accounts.values()), tuple(rates), tuple(hours), scanned)
    finally:
        workbook.close()


def _open_workbook(contents: bytes):
    if not contents:
        raise WorkbookInspectionError("El archivo Excel está vacío.")
    try:
        return load_workbook(BytesIO(contents), read_only=True, data_only=True)
    except (InvalidFileException, OSError, ValueError, KeyError) as exc:
        raise WorkbookInspectionError("No se pudo leer el archivo como un libro XLSX válido.") from exc
    except Exception as exc:
        raise WorkbookInspectionError("El archivo Excel está dañado o usa un formato no compatible.") from exc


def _is_person_row(column_a: object, column_b: object) -> bool:
    if column_a is None or column_b is None or not str(column_b).strip():
        return False
    if isinstance(column_a, bool):
        return False
    if isinstance(column_a, (int, float)):
        return True
    return bool(_PERSON_IDENTIFIER.fullmatch(str(column_a).strip()))


def _is_account_row(column_a: object, column_b: object) -> bool:
    if column_a is not None and str(column_a).strip():
        return False
    if column_b is None:
        return False
    text = normalize_identifier(column_b)
    return "ext-" in text or "tarifa" in text


def _employee_id(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return clean_display_value(value, "El número de empleado")


def _daily_hours(headers: tuple[object, ...], values: tuple[object, ...]) -> tuple[DailyHours, ...]:
    result: list[DailyHours] = []
    for index, value in enumerate(values):
        if index < len(headers) and _is_total_hours_header(headers[index]):
            continue
        if value is None or value == "" or isinstance(value, bool):
            continue
        if not isinstance(value, (int, float)):
            continue
        day = headers[index] if index < len(headers) and headers[index] is not None else index + 1
        result.append(DailyHours(day=day, hours=float(value)))
    return tuple(result)


def _person_monthly_capacity(
    person: Person,
    headers: tuple[object, ...],
    values: tuple[object, ...],
) -> PersonMonthlyWorkCapacity | None:
    for index, header in enumerate(headers):
        if not _is_total_hours_header(header) or index >= len(values):
            continue
        value = values[index]
        if _is_valid_workable_hour_value(value):
            return PersonMonthlyWorkCapacity(
                employee_id=person.employee_id,
                person_name=person.name,
                monthly_workable_hours=float(value),
                capacity_source=CapacitySource.PERSON_TOTAL_HOURS,
            )

    daily_total = sum(
        float(value)
        for index, value in enumerate(values)
        if (index >= len(headers) or not _is_total_hours_header(headers[index]))
        and _is_valid_workable_hour_value(value)
    )
    has_numeric_daily_value = any(
        (index >= len(headers) or not _is_total_hours_header(headers[index]))
        and _is_valid_workable_hour_value(value)
        for index, value in enumerate(values)
    )
    if not has_numeric_daily_value:
        return None
    return PersonMonthlyWorkCapacity(
        employee_id=person.employee_id,
        person_name=person.name,
        monthly_workable_hours=daily_total,
        capacity_source=CapacitySource.DAILY_HOURS,
    )


def _is_total_hours_header(value: object) -> bool:
    if value is None:
        return False
    return normalize_identifier(value) in {
        "horas",
        "hours",
        "total horas",
        "horas total",
        "total hours",
    }


def _is_valid_workable_hour_value(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _decimal_number(value: str) -> float:
    compact = value.replace(" ", "")
    if "," in compact and "." in compact:
        if compact.rfind(",") > compact.rfind("."):
            compact = compact.replace(".", "").replace(",", ".")
        else:
            compact = compact.replace(",", "")
    elif "," in compact:
        compact = compact.replace(",", ".")
    return float(compact)


def _read_headers(worksheet) -> tuple[str, ...]:
    first_row = next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    result: list[str] = []
    for index, value in enumerate(first_row, 1):
        result.append(f"Columna {index}" if value is None or not str(value).strip() else clean_display_value(value, "La cabecera"))
    return tuple(result)


def _collect_text(row, column, positions, target, limit) -> None:
    if column is None or len(target) >= limit:
        return
    value = row[positions[column]] if positions[column] < len(row) else None
    if value is not None and str(value).strip():
        display = clean_display_value(value, column)
        target.setdefault(normalize_identifier(display), display)


def _collect_value(row, column, positions, target, limit) -> None:
    if column is None or len(target) >= limit:
        return
    value = row[positions[column]] if positions[column] < len(row) else None
    if value is not None and value != "" and value not in target:
        target.append(value)
