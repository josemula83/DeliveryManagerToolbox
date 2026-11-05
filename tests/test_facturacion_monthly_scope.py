from io import BytesIO
import unittest

from openpyxl import Workbook

from delivery_manager_toolbox.modules.facturacion.business import calculate_sheet_management
from delivery_manager_toolbox.modules.facturacion.configuration import (
    BillingConfiguration,
    add_management_person,
    add_management_person_account_exception,
)
from delivery_manager_toolbox.modules.facturacion.excel_reader import (
    inspect_workbook,
    parse_hierarchical_worksheet,
)
from delivery_manager_toolbox.modules.facturacion.view import _calculation_context_key


def build_two_month_workbook() -> bytes:
    workbook = Workbook()
    november = workbook.active
    november.title = "Horas servicio noviembre"
    november.append([None, None, 1])
    november.append([1001, "Persona Compartida", None])
    november.append([None, "Cliente > Servicio A > EXT-A - Tarifa 17,00 EUR", 100])
    november.append([2001, "Gestor Solo Noviembre", None])
    november.append([None, "Cliente > Servicio M > EXT-M - Tarifa 25,00 EUR", 10])
    november.append([3001, "Gestor Excepción", None])
    november.append([None, "Cliente > Servicio E > EXT-E - Tarifa 30,00 EUR", 20])

    december = workbook.create_sheet("Horas servicio diciembre")
    december.append([None, None, 1])
    december.append([1001, "Persona Compartida", None])
    december.append([None, "Cliente > Servicio A > EXT-A - Tarifa 18,00 EUR", 120])
    december.append([3001, "Gestor Excepción", None])
    december.append([None, "Cliente > Servicio F > EXT-F - Tarifa 35,00 EUR", 30])

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class MonthlyCalculationScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contents = build_two_month_workbook()
        self.november = parse_hierarchical_worksheet(self.contents, "Horas servicio noviembre")
        self.december = parse_hierarchical_worksheet(self.contents, "Horas servicio diciembre")

    def test_same_account_is_calculated_independently_each_month(self) -> None:
        november_result = calculate_sheet_management(self.november, BillingConfiguration())
        december_result = calculate_sheet_management(self.december, BillingConfiguration())
        november_account = next(item for item in november_result.results if item.account_code == "EXT-A")
        december_account = next(item for item in december_result.results if item.account_code == "EXT-A")
        self.assertEqual(november_account.base_hours, 100)
        self.assertEqual(december_account.base_hours, 120)
        self.assertNotEqual(november_account.base_hours, 220)
        self.assertNotEqual(december_account.base_hours, 220)
        self.assertEqual(november_result.sheet_name, "Horas servicio noviembre")
        self.assertEqual(december_result.sheet_name, "Horas servicio diciembre")

    def test_same_person_hours_are_not_mixed_between_months(self) -> None:
        november_result = calculate_sheet_management(self.november, BillingConfiguration())
        december_result = calculate_sheet_management(self.december, BillingConfiguration())
        self.assertEqual(november_result.summary.total_base_hours, 130)
        self.assertEqual(december_result.summary.total_base_hours, 150)

    def test_rates_remain_scoped_to_each_sheet(self) -> None:
        november_account = next(item for item in self.november.accounts if item.account_code == "EXT-A")
        december_account = next(item for item in self.december.accounts if item.account_code == "EXT-A")
        self.assertEqual(november_account.rates, (17.0,))
        self.assertEqual(december_account.rates, (18.0,))

    def test_management_person_only_in_november_does_not_affect_december(self) -> None:
        configuration = add_management_person(BillingConfiguration(), "Gestor Solo Noviembre", 50)
        november_result = calculate_sheet_management(self.november, configuration)
        december_result = calculate_sheet_management(self.december, configuration)
        self.assertEqual(november_result.audit.management_people_detected, 1)
        self.assertEqual(november_result.audit.hours_removed, 10)
        self.assertEqual(december_result.audit.management_people_detected, 0)
        self.assertEqual(december_result.audit.hours_removed, 0)

    def test_exception_is_applied_only_where_assignment_exists(self) -> None:
        configuration = add_management_person(BillingConfiguration(), "Gestor Excepción", 50)
        configuration = add_management_person_account_exception(
            configuration,
            "3001",
            "Gestor Excepción",
            "EXT-E",
            self.november.assignments,
        )
        november_result = calculate_sheet_management(self.november, configuration)
        december_result = calculate_sheet_management(self.december, configuration)
        self.assertEqual(november_result.audit.exceptions_preserved, 1)
        self.assertEqual(november_result.audit.hours_preserved_by_exception, 20)
        self.assertEqual(december_result.audit.exceptions_preserved, 0)
        self.assertEqual(december_result.audit.hours_removed, 30)

    def test_sheet_change_produces_a_different_ui_result_context(self) -> None:
        configuration = BillingConfiguration()
        november_context = _calculation_context_key(
            self.contents,
            self.november.sheet_name,
            configuration,
        )
        december_context = _calculation_context_key(
            self.contents,
            self.december.sheet_name,
            configuration,
        )
        self.assertNotEqual(november_context, december_context)

    def test_workbook_analysis_cannot_be_used_as_monthly_calculation_input(self) -> None:
        workbook_analysis = inspect_workbook(self.contents, "meses.xlsx")
        with self.assertRaisesRegex(TypeError, "SheetAnalysis"):
            calculate_sheet_management(workbook_analysis, BillingConfiguration())


if __name__ == "__main__":
    unittest.main()
