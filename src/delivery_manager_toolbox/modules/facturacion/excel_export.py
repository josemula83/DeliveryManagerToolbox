"""Generación y validación del Excel final de Facturación BBVA V1.3."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from io import BytesIO
import math
from pathlib import Path
import re

from openpyxl import load_workbook
from openpyxl.cell import Cell
from openpyxl.formatting.formatting import ConditionalFormattingList
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.worksheet import Worksheet

from delivery_manager_toolbox.modules.facturacion.models import (
    ManagementAssignment,
    RateIncidentCode,
    SheetCalculationResult,
)
from delivery_manager_toolbox.modules.facturacion.validation import normalize_identifier


_FLOAT_TOLERANCE = 1e-7
_MANAGEMENT_TYPE = "Gestión"
_ORIGINAL_TYPE = "Original"
_ORIGINAL_MANAGER_TYPE = "Original gestor"
_SUMMARY_TITLE = "DETALLE GESTIÓN POR CUENTA"
_BLOCKING_RATE_INCIDENTS = {
    RateIncidentCode.NO_RATE_FOUND,
    RateIncidentCode.MULTIPLE_RATE_EXCEPTION,
    RateIncidentCode.RATE_CONSTRAINT,
}


class BillingExportError(ValueError):
    """Impide descargar un libro cuyo cálculo o contenido no cuadra."""


@dataclass(frozen=True, slots=True)
class SheetExportValidation:
    sheet_name: str
    management_hours: float
    exported_management_hours: float
    billable_hours: float
    summary_billable_hours: float
    total_amount: float
    managers: int
    accounts: int


@dataclass(frozen=True, slots=True)
class BillingExportArtifact:
    filename: str
    contents: bytes
    included_sheets: tuple[str, ...]
    total_management_hours: float
    total_billable_hours: float
    total_amount: float
    manager_count: int
    account_count: int
    incident_count: int
    validations: tuple[SheetExportValidation, ...]


@dataclass(frozen=True, slots=True)
class _ReportPerson:
    employee_id: str
    name: str
    row_type: str
    billable_hours: float
    management_hours: float
    rate: float
    amount: float


@dataclass(slots=True)
class _ReportAccount:
    code: str
    service_name: str
    people: list[_ReportPerson]

    @property
    def total_hours(self) -> float:
        return sum(item.billable_hours for item in self.people)

    @property
    def total_amount(self) -> float:
        return sum(item.amount for item in self.people)


def export_billing_workbook(
    original_contents: bytes,
    original_filename: str,
    results_by_sheet: Mapping[str, SheetCalculationResult],
) -> BillingExportArtifact:
    """Reescribe las hojas calculadas como ``buildSheetXml`` y valida el XLSX generado."""
    if not results_by_sheet:
        raise BillingExportError("No hay ningún mes calculado para exportar.")
    _validate_results_before_export(results_by_sheet)

    original_snapshot = bytes(original_contents)
    workbook = load_workbook(BytesIO(original_contents))
    original_sheet_names = tuple(workbook.sheetnames)
    try:
        missing_sheets = set(results_by_sheet) - set(workbook.sheetnames)
        if missing_sheets:
            raise BillingExportError(
                "El libro original no contiene las hojas calculadas: "
                + ", ".join(sorted(missing_sheets))
            )
        for sheet_name, result in results_by_sheet.items():
            _rewrite_sheet(workbook[sheet_name], result)
        output = BytesIO()
        workbook.save(output)
        contents = output.getvalue()
    finally:
        workbook.close()

    if original_contents != original_snapshot:
        raise BillingExportError("El contenido original cambió durante la exportación.")
    validations = validate_exported_workbook(
        contents,
        original_sheet_names,
        results_by_sheet,
    )
    optimizations = tuple(
        result.rate_optimization_result
        for result in results_by_sheet.values()
        if result.rate_optimization_result is not None
    )
    all_assignments = tuple(
        assignment
        for optimization in optimizations
        for assignment in optimization.assignments
    )
    return BillingExportArtifact(
        filename=export_filename(original_filename),
        contents=contents,
        included_sheets=tuple(results_by_sheet),
        total_management_hours=sum(item.management_hours for item in all_assignments),
        total_billable_hours=sum(item.billable_hours or 0.0 for item in all_assignments),
        total_amount=sum(item.amount or 0.0 for item in all_assignments),
        manager_count=len({item.employee_id for item in all_assignments}),
        account_count=len({item.account_code for item in all_assignments}),
        incident_count=sum(len(item.incidents) for item in optimizations),
        validations=validations,
    )


def export_filename(original_filename: str) -> str:
    """Añade el sufijo solicitado sin sobrescribir el nombre de entrada."""
    filename = Path(original_filename).name
    stem = filename[:-5] if filename.casefold().endswith(".xlsx") else filename
    return f"{stem or 'Facturacion_BBVA'}_FACTURACION.xlsx"


def validate_exported_workbook(
    contents: bytes,
    original_sheet_names: Iterable[str],
    results_by_sheet: Mapping[str, SheetCalculationResult],
) -> tuple[SheetExportValidation, ...]:
    """Reabre el libro y valida horas, tarifas, importes, resúmenes y estilos."""
    workbook = load_workbook(BytesIO(contents), data_only=False)
    try:
        expected_sheet_names = tuple(original_sheet_names)
        if tuple(workbook.sheetnames) != expected_sheet_names:
            raise BillingExportError("La exportación cambió los nombres o el orden de las hojas.")
        validations = tuple(
            _validate_exported_sheet(workbook[sheet_name], result)
            for sheet_name, result in results_by_sheet.items()
        )
        return validations
    finally:
        workbook.close()


def _validate_results_before_export(
    results_by_sheet: Mapping[str, SheetCalculationResult],
) -> None:
    for sheet_name, result in results_by_sheet.items():
        if result.sheet_name != sheet_name:
            raise BillingExportError(f"El resultado no corresponde a la hoja {sheet_name}.")
        allocation = result.allocation_result
        optimization = result.rate_optimization_result
        if result.summary.total_management_hours > _FLOAT_TOLERANCE and allocation is None:
            raise BillingExportError(f"{sheet_name}: el reparto de gestión no está calculado.")
        if optimization is None:
            raise BillingExportError(f"{sheet_name}: la optimización de tarifas no está calculada.")
        if allocation is not None and not math.isclose(
            allocation.allocation_difference,
            0.0,
            abs_tol=_FLOAT_TOLERANCE,
        ):
            raise BillingExportError(f"{sheet_name}: el reparto no cuadra.")
        blocking = [
            item for item in optimization.incidents if item.code in _BLOCKING_RATE_INCIDENTS
        ]
        if blocking:
            raise BillingExportError(
                f"{sheet_name}: " + "; ".join(item.message for item in blocking)
            )
        for item in optimization.assignments:
            if item.management_rate is None or item.billable_hours is None or item.amount is None:
                raise BillingExportError(
                    f"{sheet_name}: {item.account_code} no tiene tarifa e importe resueltos."
                )
        _validate_engine_totals(result)


def _validate_engine_totals(result: SheetCalculationResult) -> None:
    optimization = result.rate_optimization_result
    allocation = result.allocation_result
    if optimization is None:
        return
    optimized = optimization.assignments
    if allocation is not None:
        expected = _hours_by_manager_account(allocation.assignments)
        actual = _hours_by_manager_account(optimized)
        _assert_float_mappings_equal(
            expected,
            actual,
            f"{result.sheet_name}: la tarifa cambió el reparto gestor/cuenta.",
        )
    calculated = {
        item.account_code: item.management_hours
        for item in result.account_calculations
        if item.management_hours > 0
    }
    exported = _hours_by_account(optimized)
    _assert_float_mappings_equal(
        calculated,
        exported,
        f"{result.sheet_name}: las cuentas calculadas no cuadran con la optimización.",
    )


def _rewrite_sheet(worksheet: Worksheet, result: SheetCalculationResult) -> None:
    _clear_sheet_data(worksheet)
    accounts = _build_report_accounts(result)
    row = 1
    _write_row(worksheet, row, ((1, "FACTURACIÓN POR CUENTA"), (2, result.sheet_name)), "header")
    row += 1
    for account in accounts:
        row += 1
        _write_row(
            worksheet,
            row,
            (
                (1, "CUENTA"),
                (2, account.code),
                (3, account.service_name),
                (8, "TOTAL HORAS"),
                (9, account.total_hours),
            ),
            "header",
        )
        row += 1
        _write_row(
            worksheet,
            row,
            (
                (1, "Nº empleado"),
                (2, "Persona"),
                (3, "Tipo"),
                (4, "Horas facturables"),
                (5, "Horas gestión reales"),
                (6, "Tarifa €"),
                (7, "Importe €"),
            ),
            "header",
        )
        for person in account.people:
            row += 1
            _write_row(
                worksheet,
                row,
                (
                    (1, _excel_employee_id(person.employee_id)),
                    (2, person.name),
                    (3, person.row_type),
                    (4, person.billable_hours),
                    (5, person.management_hours),
                    (6, person.rate),
                    (7, person.amount),
                ),
                "management" if person.row_type == _MANAGEMENT_TYPE else "normal",
            )
        row += 1
        _write_row(
            worksheet,
            row,
            ((3, "TOTAL CUENTA"), (4, account.total_hours), (7, account.total_amount)),
            "header",
        )

    row += 3
    _write_row(
        worksheet,
        row,
        (
            (1, "RESUMEN GESTORES"),
            (2, "Horas informadas"),
            (3, "Horas gestión calculadas"),
            (4, "Horas mes"),
            (5, "Capacidad real %"),
            (6, "Objetivo %"),
            (7, "Desviación %"),
        ),
        "header",
    )
    optimization = result.rate_optimization_result
    allocation = result.allocation_result
    billing_by_employee = {
        item.employee_id: item for item in optimization.manager_summaries
    } if optimization else {}
    for summary in allocation.manager_summaries if allocation else ():
        billing = billing_by_employee.get(summary.employee_id)
        billable = billing.billable_hours if billing else 0.0
        month_hours = summary.monthly_workable_hours or 0.0
        real_capacity = billable / month_hours * 100 if month_hours else 0.0
        deviation = (
            (billable - summary.base_target_hours) / summary.base_target_hours * 100
            if summary.base_target_hours else 0.0
        )
        row += 1
        _write_row(
            worksheet,
            row,
            (
                (1, summary.manager_name),
                (2, billable),
                (3, summary.assigned_hours),
                (4, month_hours),
                (5, real_capacity),
                (6, summary.capacity_percentage),
                (7, deviation),
            ),
            "normal",
        )

    row += 3
    _write_row(worksheet, row, ((1, _SUMMARY_TITLE),), "header")
    row += 1
    _write_row(
        worksheet,
        row,
        ((1, "Gestor"), (2, "Cuenta"), (3, "Horas facturables"), (4, "Tarifa")),
        "header",
    )
    for item in _optimized_assignments(result):
        row += 1
        account_label = f"{item.account_code} - {item.service_name}" if item.service_name else item.account_code
        _write_row(
            worksheet,
            row,
            (
                (1, item.manager_name),
                (2, account_label),
                (3, item.billable_hours or 0.0),
                (4, item.management_rate or 0.0),
            ),
            "management",
        )
    worksheet.freeze_panes = None
    worksheet.auto_filter.ref = None


def _build_report_accounts(result: SheetCalculationResult) -> tuple[_ReportAccount, ...]:
    accounts: dict[str, _ReportAccount] = {}
    exception_keys = {
        (normalize_identifier(item.employee_id), normalize_identifier(item.account_code))
        for item in result.clean_model.preserved_exceptions
    }
    for assignment in result.clean_model.assignments:
        if not assignment.account_code:
            continue
        account = accounts.setdefault(
            normalize_identifier(assignment.account_code),
            _ReportAccount(assignment.account_code, assignment.service_name, []),
        )
        row_type = (
            _ORIGINAL_MANAGER_TYPE
            if (
                normalize_identifier(assignment.person.employee_id),
                normalize_identifier(assignment.account_code),
            ) in exception_keys
            else _ORIGINAL_TYPE
        )
        hours = assignment.total_hours
        rate = assignment.rate or 0.0
        account.people.append(_ReportPerson(
            employee_id=assignment.person.employee_id,
            name=assignment.person.name,
            row_type=row_type,
            billable_hours=hours,
            management_hours=0.0,
            rate=rate,
            amount=hours * rate,
        ))
    for calculation in result.account_calculations:
        accounts.setdefault(
            normalize_identifier(calculation.account_code),
            _ReportAccount(calculation.account_code, calculation.service_name, []),
        )
    for assignment in _optimized_assignments(result):
        account = accounts.setdefault(
            normalize_identifier(assignment.account_code),
            _ReportAccount(assignment.account_code, assignment.service_name, []),
        )
        account.people.append(_ReportPerson(
            employee_id=assignment.employee_id,
            name=assignment.manager_name,
            row_type=_MANAGEMENT_TYPE,
            billable_hours=assignment.billable_hours or 0.0,
            management_hours=assignment.management_hours,
            rate=assignment.management_rate or 0.0,
            amount=assignment.amount or 0.0,
        ))
    for account in accounts.values():
        account.people.sort(key=lambda item: (
            normalize_identifier(item.name),
            normalize_identifier(item.row_type),
        ))
    return tuple(sorted(accounts.values(), key=_legacy_account_key))


def _validate_exported_sheet(
    worksheet: Worksheet,
    result: SheetCalculationResult,
) -> SheetExportValidation:
    if worksheet["A1"].value != "FACTURACIÓN POR CUENTA" or worksheet["B1"].value != result.sheet_name:
        raise BillingExportError(f"{result.sheet_name}: falta la cabecera legacy.")
    expected = _optimized_assignments(result)
    main_rows = _management_rows(worksheet)
    summary_rows = _summary_rows(worksheet)
    expected_by_key = {
        (item.manager_name, item.account_code): item
        for item in expected
    }
    if len(expected_by_key) != len(expected):
        raise BillingExportError(f"{result.sheet_name}: el motor contiene gestor/cuenta duplicado.")
    if set(main_rows) != set(expected_by_key) or set(summary_rows) != set(expected_by_key):
        raise BillingExportError(f"{result.sheet_name}: faltan gestores o cuentas en la exportación.")
    for key, item in expected_by_key.items():
        main = main_rows[key]
        summary = summary_rows[key]
        _assert_close(main[0], item.management_hours, f"{result.sheet_name}: horas reales incorrectas para {key}.")
        _assert_close(main[1], item.billable_hours or 0.0, f"{result.sheet_name}: horas facturables incorrectas para {key}.")
        _assert_close(main[2], item.management_rate or 0.0, f"{result.sheet_name}: tarifa incorrecta para {key}.")
        _assert_close(main[3], item.amount or 0.0, f"{result.sheet_name}: importe incorrecto para {key}.")
        _assert_close(summary[0], item.billable_hours or 0.0, f"{result.sheet_name}: resumen de horas incorrecto para {key}.")
        _assert_close(summary[1], item.management_rate or 0.0, f"{result.sheet_name}: resumen de tarifa incorrecto para {key}.")
        if main[4] in {None, "00000000"}:
            raise BillingExportError(f"{result.sheet_name}: el gestor {key[0]} no está destacado.")

    exported_management = sum(item[0] for item in main_rows.values())
    exported_billable = sum(item[1] for item in main_rows.values())
    summary_billable = sum(item[0] for item in summary_rows.values())
    expected_management = sum(item.management_hours for item in expected)
    expected_billable = sum(item.billable_hours or 0.0 for item in expected)
    _assert_close(exported_management, expected_management, f"{result.sheet_name}: no cuadran las horas de gestión.")
    _assert_close(exported_billable, expected_billable, f"{result.sheet_name}: no cuadran las horas facturables.")
    _assert_close(summary_billable, expected_billable, f"{result.sheet_name}: no cuadra el resumen.")
    account_totals = _account_total_rows(worksheet)
    expected_account_totals = {
        item.code: (item.total_hours, item.total_amount)
        for item in _build_report_accounts(result)
    }
    if set(account_totals) != set(expected_account_totals):
        raise BillingExportError(f"{result.sheet_name}: faltan totales de cuenta.")
    for account_code, expected_totals in expected_account_totals.items():
        actual_totals = account_totals[account_code]
        _assert_close(actual_totals[0], expected_totals[0], f"{result.sheet_name}: total de horas incorrecto para {account_code}.")
        _assert_close(actual_totals[1], expected_totals[1], f"{result.sheet_name}: total de importe incorrecto para {account_code}.")
    _validate_original_rows(worksheet, result)
    return SheetExportValidation(
        sheet_name=result.sheet_name,
        management_hours=expected_management,
        exported_management_hours=exported_management,
        billable_hours=expected_billable,
        summary_billable_hours=summary_billable,
        total_amount=sum(item.amount or 0.0 for item in expected),
        managers=len({item.employee_id for item in expected}),
        accounts=len({item.account_code for item in expected}),
    )


def _management_rows(
    worksheet: Worksheet,
) -> dict[tuple[str, str], tuple[float, float, float, float, str | None]]:
    current_account = ""
    rows: dict[tuple[str, str], tuple[float, float, float, float, str | None]] = {}
    for row in worksheet.iter_rows():
        if row[0].value == "CUENTA":
            current_account = str(row[1].value or "")
        elif len(row) >= 7 and row[2].value == _MANAGEMENT_TYPE:
            key = (str(row[1].value or ""), current_account)
            if key in rows:
                raise BillingExportError(f"Gestor/cuenta duplicado en la hoja: {key}.")
            rows[key] = (
                _number(row[4].value),
                _number(row[3].value),
                _number(row[5].value),
                _number(row[6].value),
                row[0].fill.fgColor.rgb,
            )
    return rows


def _summary_rows(worksheet: Worksheet) -> dict[tuple[str, str], tuple[float, float]]:
    title_row = next(
        (cell.row for cell in worksheet["A"] if cell.value == _SUMMARY_TITLE),
        None,
    )
    if title_row is None:
        raise BillingExportError(f"{worksheet.title}: falta la tabla resumen final.")
    rows: dict[tuple[str, str], tuple[float, float]] = {}
    for row in worksheet.iter_rows(min_row=title_row + 2, max_col=4):
        manager = row[0].value
        if manager in (None, ""):
            continue
        account_label = str(row[1].value or "")
        account_code = account_label.split(" - ", 1)[0]
        key = (str(manager), account_code)
        if key in rows:
            raise BillingExportError(f"Gestor/cuenta duplicado en el resumen: {key}.")
        rows[key] = (_number(row[2].value), _number(row[3].value))
    return rows


def _account_total_rows(worksheet: Worksheet) -> dict[str, tuple[float, float]]:
    current_account = ""
    totals: dict[str, tuple[float, float]] = {}
    for row in worksheet.iter_rows():
        if row[0].value == "CUENTA":
            current_account = str(row[1].value or "")
        elif len(row) >= 7 and row[2].value == "TOTAL CUENTA":
            totals[current_account] = (_number(row[3].value), _number(row[6].value))
    return totals


def _validate_original_rows(worksheet: Worksheet, result: SheetCalculationResult) -> None:
    expected = Counter(
        (
            item.person.employee_id,
            item.person.name,
            item.account_code,
            item.total_hours,
            item.rate or 0.0,
        )
        for item in result.clean_model.assignments
        if item.account_code
    )
    current_account = ""
    actual: Counter[tuple[object, ...]] = Counter()
    for row in worksheet.iter_rows():
        if row[0].value == "CUENTA":
            current_account = str(row[1].value or "")
        elif len(row) >= 7 and row[2].value in {_ORIGINAL_TYPE, _ORIGINAL_MANAGER_TYPE}:
            employee_id = str(row[0].value)
            actual[(employee_id, str(row[1].value), current_account, _number(row[3].value), _number(row[5].value))] += 1
    normalized_expected = Counter(
        (str(_excel_employee_id(str(employee_id))), name, account, _rounded(hours), _rounded(rate))
        for employee_id, name, account, hours, rate in expected.elements()
    )
    if actual != normalized_expected:
        raise BillingExportError(f"{result.sheet_name}: no se conservaron todas las filas originales.")


def _optimized_assignments(result: SheetCalculationResult) -> tuple[ManagementAssignment, ...]:
    optimization = result.rate_optimization_result
    if optimization is None:
        return ()
    return tuple(sorted(
        optimization.assignments,
        key=lambda item: (
            normalize_identifier(item.manager_name),
            _account_numeric_parts(item.account_code),
            normalize_identifier(item.service_name),
        ),
    ))


def _clear_sheet_data(worksheet: Worksheet) -> None:
    for merged_range in tuple(worksheet.merged_cells.ranges):
        worksheet.unmerge_cells(str(merged_range))
    if worksheet.max_row:
        worksheet.delete_rows(1, worksheet.max_row)
    worksheet.conditional_formatting = ConditionalFormattingList()
    worksheet.data_validations.dataValidation = []


def _write_row(
    worksheet: Worksheet,
    row: int,
    values: Iterable[tuple[int, object]],
    style: str,
) -> None:
    for column, value in values:
        cell = worksheet.cell(row=row, column=column, value=_rounded(value))
        _apply_style(cell, style)


def _apply_style(cell: Cell, style: str) -> None:
    thin = Side(style="thin", color="B7C9E2")
    cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    cell.alignment = Alignment(vertical="center")
    if style == "header":
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.font = Font(color="FFFFFF", bold=True)
    elif style == "management":
        cell.fill = PatternFill("solid", fgColor="D9EAF7")
        cell.font = Font(color="17365D", bold=True)
    else:
        cell.fill = PatternFill(fill_type=None)
        cell.font = Font(color="000000")
    if isinstance(cell.value, float):
        cell.number_format = "0.00"


def _legacy_account_key(account: _ReportAccount) -> tuple[tuple[int, ...], str]:
    return _account_numeric_parts(account.code or account.service_name), normalize_identifier(account.service_name)


def _account_numeric_parts(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value))


def _excel_employee_id(employee_id: str) -> object:
    return int(employee_id) if employee_id.isdigit() else employee_id


def _rounded(value: object) -> object:
    if isinstance(value, float):
        return round(value, 4)
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    return float(value)


def _hours_by_account(assignments: Iterable[ManagementAssignment]) -> dict[str, float]:
    result: dict[str, float] = defaultdict(float)
    for item in assignments:
        result[item.account_code] += item.management_hours
    return result


def _hours_by_manager_account(
    assignments: Iterable[ManagementAssignment],
) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = defaultdict(float)
    for item in assignments:
        result[(item.employee_id, item.account_code)] += item.management_hours
    return result


def _assert_float_mappings_equal(
    expected: Mapping[object, float],
    actual: Mapping[object, float],
    message: str,
) -> None:
    if expected.keys() != actual.keys() or any(
        not math.isclose(value, actual[key], abs_tol=_FLOAT_TOLERANCE)
        for key, value in expected.items()
    ):
        raise BillingExportError(message)


def _assert_close(actual: float, expected: float, message: str) -> None:
    if not math.isclose(actual, expected, abs_tol=_FLOAT_TOLERANCE):
        raise BillingExportError(message)


__all__ = [
    "BillingExportArtifact",
    "BillingExportError",
    "SheetExportValidation",
    "export_billing_workbook",
    "export_filename",
    "validate_exported_workbook",
]
