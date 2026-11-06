from io import BytesIO
import math
import unittest

from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill

from delivery_manager_toolbox.modules.facturacion.business import (
    calculate_sheet_management,
    calculate_sheet_rate_optimization,
    calculate_workbook_management,
)
from delivery_manager_toolbox.modules.facturacion.configuration import (
    BillingConfiguration,
    add_management_person,
    add_management_person_account_exception,
)
from delivery_manager_toolbox.modules.facturacion.excel_export import (
    BillingExportError,
    export_billing_workbook,
)
from delivery_manager_toolbox.modules.facturacion.excel_reader import (
    parse_hierarchical_worksheet,
)


NOVEMBER = "Horas servicio noviembre"
DECEMBER = "Horas servicio diciembre"


def source_workbook(*, missing_rate: bool = False) -> bytes:
    workbook = Workbook()
    november = workbook.active
    november.title = NOVEMBER
    _fill_month_sheet(november, 17 if not missing_rate else None, 50, include_exception=True)
    december = workbook.create_sheet(DECEMBER)
    _fill_month_sheet(december, 18, 60, include_exception=False)
    notes = workbook.create_sheet("Notas")
    notes["A1"] = "Contenido original sin calcular"
    november.column_dimensions["B"].width = 48
    november["D1"].fill = PatternFill("solid", fgColor="FFF2CC")
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _fill_month_sheet(worksheet, rate: float | None, daily_hours: float, *, include_exception: bool) -> None:
    worksheet.append([None, None, 1, 2, "Horas"])
    worksheet.append([1001, "Persona Original", None, None, None])
    rate_text = f" - Tarifa {rate:.2f} EUR" if rate is not None else ""
    worksheet.append([None, f"Cliente > Servicio A > EXT-A{rate_text}", daily_hours, daily_hours, daily_hours * 2])
    worksheet.append([2001, "Gestor Principal", None, None, None])
    worksheet.append([None, "Cliente > Servicio M > EXT-M - Tarifa 25.00 EUR", 5, 5, 10])
    if include_exception:
        worksheet.append([None, "Cliente > Servicio E > EXT-E - Tarifa 30.00 EUR", 10, 10, 20])


def configured_results(contents: bytes):
    november = parse_hierarchical_worksheet(contents, NOVEMBER)
    december = parse_hierarchical_worksheet(contents, DECEMBER)
    configuration = add_management_person(
        BillingConfiguration(), "Gestor Principal", 20, "2001"
    )
    configuration = add_management_person_account_exception(
        configuration,
        "2001",
        "Gestor Principal",
        "EXT-E",
        november.assignments,
    )
    result = calculate_workbook_management((november, december), configuration)
    return configuration, november, december, result


def management_rows(worksheet):
    account = ""
    rows = []
    for row in worksheet.iter_rows():
        if row[0].value == "CUENTA":
            account = row[1].value
        elif len(row) >= 7 and row[2].value == "Gestión":
            rows.append({
                "employee_id": str(row[0].value),
                "manager": row[1].value,
                "account": account,
                "billable": row[3].value,
                "management": row[4].value,
                "rate": row[5].value,
                "amount": row[6].value,
                "fill": row[0].fill.fgColor.rgb,
            })
    return rows


class BillingExcelExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contents = source_workbook()
        self.configuration, self.november, self.december, self.workbook_result = configured_results(
            self.contents
        )

    def test_single_sheet_export_rewrites_only_selected_month(self) -> None:
        result = calculate_sheet_management(self.november, self.configuration)
        result = calculate_sheet_rate_optimization(result, self.november, self.configuration)

        artifact = export_billing_workbook(
            self.contents,
            "2026-11-Grupos_propios_BBVA.xlsx",
            {NOVEMBER: result},
        )
        workbook = load_workbook(BytesIO(artifact.contents))
        try:
            self.assertEqual(workbook[NOVEMBER]["A1"].value, "FACTURACIÓN POR CUENTA")
            self.assertEqual(workbook[DECEMBER]["A1"].value, None)
            self.assertEqual(workbook[DECEMBER]["B2"].value, "Persona Original")
            self.assertEqual(workbook["Notas"]["A1"].value, "Contenido original sin calcular")
            self.assertEqual(workbook[NOVEMBER].column_dimensions["B"].width, 48)
        finally:
            workbook.close()
        self.assertEqual(artifact.filename, "2026-11-Grupos_propios_BBVA_FACTURACION.xlsx")
        self.assertEqual(artifact.included_sheets, (NOVEMBER,))

    def test_multiple_sheets_keep_names_and_months_independent(self) -> None:
        artifact = export_billing_workbook(
            self.contents,
            "meses.xlsx",
            self.workbook_result.results_by_sheet,
        )
        workbook = load_workbook(BytesIO(artifact.contents))
        try:
            self.assertEqual(workbook.sheetnames, [NOVEMBER, DECEMBER, "Notas"])
            november_rows = management_rows(workbook[NOVEMBER])
            december_rows = management_rows(workbook[DECEMBER])
            self.assertEqual(sum(item["management"] for item in november_rows), 6)
            self.assertEqual(sum(item["management"] for item in december_rows), 6)
            self.assertEqual({item["rate"] for item in november_rows if item["account"] == "EXT-A"}, {17})
            self.assertEqual({item["rate"] for item in december_rows}, {45})
            self.assertEqual(sum(item["billable"] for item in december_rows), 2.4)
        finally:
            workbook.close()
        self.assertEqual(len(artifact.validations), 2)

    def test_original_people_and_exception_are_preserved(self) -> None:
        artifact = export_billing_workbook(
            self.contents, "meses.xlsx", self.workbook_result.results_by_sheet
        )
        workbook = load_workbook(BytesIO(artifact.contents))
        try:
            rows = [
                tuple(cell.value for cell in row[:7])
                for row in workbook[NOVEMBER].iter_rows()
            ]
        finally:
            workbook.close()
        self.assertTrue(any(row[1:4] == ("Persona Original", "Original", 100) for row in rows))
        self.assertTrue(any(row[1:4] == ("Gestor Principal", "Original gestor", 20) for row in rows))
        self.assertTrue(any(row[1] == "Gestor Principal" and row[2] == "Gestión" for row in rows))

    def test_hours_rates_amounts_and_summary_match_engine(self) -> None:
        artifact = export_billing_workbook(
            self.contents, "meses.xlsx", self.workbook_result.results_by_sheet
        )
        workbook = load_workbook(BytesIO(artifact.contents))
        try:
            for sheet_name, result in self.workbook_result.results_by_sheet.items():
                rows = management_rows(workbook[sheet_name])
                expected = {
                    (item.manager_name, item.account_code): item
                    for item in result.rate_optimization_result.assignments
                }
                self.assertEqual({(item["manager"], item["account"]) for item in rows}, set(expected))
                for row in rows:
                    item = expected[(row["manager"], row["account"])]
                    self.assertEqual(row["management"], item.management_hours)
                    self.assertEqual(row["billable"], item.billable_hours)
                    self.assertEqual(row["rate"], item.management_rate)
                    self.assertEqual(row["amount"], item.amount)
                    self.assertTrue(math.isclose(row["amount"], row["billable"] * row["rate"]))
                validation = next(item for item in artifact.validations if item.sheet_name == sheet_name)
                self.assertEqual(validation.management_hours, validation.exported_management_hours)
                self.assertEqual(validation.billable_hours, validation.summary_billable_hours)
        finally:
            workbook.close()

    def test_summary_has_legacy_four_columns_and_deterministic_order(self) -> None:
        artifact = export_billing_workbook(
            self.contents, "meses.xlsx", self.workbook_result.results_by_sheet
        )
        workbook = load_workbook(BytesIO(artifact.contents))
        try:
            worksheet = workbook[NOVEMBER]
            title_row = next(cell.row for cell in worksheet["A"] if cell.value == "DETALLE GESTIÓN POR CUENTA")
            headings = [worksheet.cell(title_row + 1, column).value for column in range(1, 5)]
            summary = [
                tuple(worksheet.cell(row, column).value for column in range(1, 5))
                for row in range(title_row + 2, worksheet.max_row + 1)
            ]
        finally:
            workbook.close()
        self.assertEqual(headings, ["Gestor", "Cuenta", "Horas facturables", "Tarifa"])
        self.assertEqual(summary, sorted(summary, key=lambda item: (item[0].casefold(), item[1])))

    def test_management_rows_are_visually_distinct_and_not_duplicated(self) -> None:
        artifact = export_billing_workbook(
            self.contents, "meses.xlsx", self.workbook_result.results_by_sheet
        )
        workbook = load_workbook(BytesIO(artifact.contents))
        try:
            for sheet_name in (NOVEMBER, DECEMBER):
                rows = management_rows(workbook[sheet_name])
                keys = [(item["manager"], item["account"]) for item in rows]
                self.assertEqual(len(keys), len(set(keys)))
                self.assertTrue(all(item["fill"] not in {None, "00000000"} for item in rows))
        finally:
            workbook.close()

    def test_generated_file_reopens_and_original_bytes_do_not_change(self) -> None:
        original = bytes(self.contents)
        artifact = export_billing_workbook(
            self.contents, "meses.xlsx", self.workbook_result.results_by_sheet
        )
        reopened = load_workbook(BytesIO(artifact.contents))
        reopened.close()
        self.assertEqual(self.contents, original)
        self.assertNotEqual(artifact.contents, original)

    def test_export_is_blocked_when_a_rate_is_missing(self) -> None:
        contents = source_workbook(missing_rate=True)
        _, _, _, workbook_result = configured_results(contents)

        with self.assertRaisesRegex(BillingExportError, "tarifa base válida"):
            export_billing_workbook(contents, "sin-tarifa.xlsx", workbook_result.results_by_sheet)


if __name__ == "__main__":
    unittest.main()
