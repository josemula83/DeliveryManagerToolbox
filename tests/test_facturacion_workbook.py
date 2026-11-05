from io import BytesIO
from unittest import TestCase
from unittest.mock import patch

from openpyxl import Workbook

from delivery_manager_toolbox.modules.facturacion.business import (
    calculate_sheet_management,
    calculate_workbook_management,
)
from delivery_manager_toolbox.modules.facturacion.configuration import (
    BillingConfiguration,
    add_management_person,
    add_no_management_account,
)
from delivery_manager_toolbox.modules.facturacion.excel_reader import (
    inspect_workbook,
    parse_hierarchical_worksheet,
)
from delivery_manager_toolbox.modules.facturacion.view import (
    _WORKBOOK_RESULT_CONTEXT_KEY,
    _WORKBOOK_RESULT_KEY,
    _discard_stale_workbook_calculation_result,
    _workbook_calculation_context_key,
)


def build_workbook(*, december_hours: float = 120) -> bytes:
    workbook = Workbook()
    november = workbook.active
    november.title = "Horas servicio noviembre"
    november.append([None, None, 1])
    november.append([1001, "Persona Compartida", None])
    november.append([None, "Cliente > Servicio A > EXT-A - Tarifa 17,00 EUR", 100])
    november.append([2001, "Gestor Noviembre", None])
    november.append([None, "Cliente > Servicio M > EXT-M - Tarifa 25,00 EUR", 10])

    december = workbook.create_sheet("Horas servicio diciembre")
    december.append([None, None, 1])
    december.append([1001, "Persona Compartida", None])
    december.append([None, "Cliente > Servicio A > EXT-A - Tarifa 18,00 EUR", december_hours])

    notes = workbook.create_sheet("Notas")
    notes.append(["Resumen interno"])
    notes.append(["Esta hoja no tiene el formato jerárquico"])

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class WorkbookCalculationTests(TestCase):
    def setUp(self) -> None:
        self.contents = build_workbook()
        information = inspect_workbook(self.contents, "meses.xlsx")
        self.analyses = tuple(
            parse_hierarchical_worksheet(self.contents, sheet_name)
            for sheet_name in information.sheet_names
        )
        self.november, self.december, self.notes = self.analyses

    def test_two_valid_sheets_generate_independent_results(self) -> None:
        result = calculate_workbook_management(self.analyses, BillingConfiguration())

        self.assertEqual(
            tuple(result.results_by_sheet),
            ("Horas servicio noviembre", "Horas servicio diciembre"),
        )
        november = result.results_by_sheet["Horas servicio noviembre"]
        december = result.results_by_sheet["Horas servicio diciembre"]
        november_account = next(item for item in november.results if item.account_code == "EXT-A")
        december_account = next(item for item in december.results if item.account_code == "EXT-A")
        self.assertEqual((november_account.base_hours, november_account.management_hours), (100, 5))
        self.assertEqual((december_account.base_hours, december_account.management_hours), (120, 6))

    def test_results_retain_their_sheet_names(self) -> None:
        result = calculate_workbook_management(self.analyses, BillingConfiguration())

        self.assertEqual(
            [item.sheet_name for item in result.results_by_sheet.values()],
            ["Horas servicio noviembre", "Horas servicio diciembre"],
        )

    def test_invalid_sheet_is_skipped_without_breaking_valid_months(self) -> None:
        result = calculate_workbook_management(self.analyses, BillingConfiguration())

        self.assertEqual(result.detected_sheet_count, 3)
        self.assertEqual(result.calculated_sheet_count, 2)
        self.assertEqual(len(result.skipped_sheets), 1)
        self.assertEqual(result.skipped_sheets[0].sheet_name, "Notas")

    def test_workbook_mode_reuses_the_single_sheet_engine(self) -> None:
        target = "delivery_manager_toolbox.modules.facturacion.business.calculate_sheet_management"
        with patch(target, wraps=calculate_sheet_management) as engine:
            calculate_workbook_management(self.analyses, BillingConfiguration())

        self.assertEqual(engine.call_count, 2)
        self.assertIs(engine.call_args_list[0].args[0], self.november)
        self.assertIs(engine.call_args_list[1].args[0], self.december)

    def test_individual_calculation_keeps_existing_behavior(self) -> None:
        result = calculate_sheet_management(self.november, BillingConfiguration())
        account = next(item for item in result.results if item.account_code == "EXT-A")

        self.assertEqual(result.sheet_name, "Horas servicio noviembre")
        self.assertEqual((account.base_hours, account.management_hours), (100, 5))

    def test_audit_is_independent_for_each_sheet(self) -> None:
        configuration = add_management_person(BillingConfiguration(), "Gestor Noviembre", 50)
        result = calculate_workbook_management(self.analyses, configuration)

        november = result.results_by_sheet["Horas servicio noviembre"]
        december = result.results_by_sheet["Horas servicio diciembre"]
        self.assertEqual((november.audit.assignments_removed, november.audit.hours_removed), (1, 10))
        self.assertEqual((december.audit.assignments_removed, december.audit.hours_removed), (0, 0))

    def test_configuration_change_invalidates_workbook_result(self) -> None:
        configuration = BillingConfiguration()
        original_context = _workbook_calculation_context_key(self.contents, configuration)
        changed_configuration = add_no_management_account(configuration, "EXT-A")
        changed_context = _workbook_calculation_context_key(self.contents, changed_configuration)
        state = {
            _WORKBOOK_RESULT_KEY: calculate_workbook_management(self.analyses, configuration),
            _WORKBOOK_RESULT_CONTEXT_KEY: original_context,
        }

        _discard_stale_workbook_calculation_result(state, changed_context)

        self.assertNotIn(_WORKBOOK_RESULT_KEY, state)
        self.assertNotIn(_WORKBOOK_RESULT_CONTEXT_KEY, state)

    def test_new_excel_invalidates_workbook_result(self) -> None:
        configuration = BillingConfiguration()
        original_context = _workbook_calculation_context_key(self.contents, configuration)
        changed_context = _workbook_calculation_context_key(
            build_workbook(december_hours=140),
            configuration,
        )
        state = {
            _WORKBOOK_RESULT_KEY: calculate_workbook_management(self.analyses, configuration),
            _WORKBOOK_RESULT_CONTEXT_KEY: original_context,
        }

        _discard_stale_workbook_calculation_result(state, changed_context)

        self.assertNotIn(_WORKBOOK_RESULT_KEY, state)
        self.assertNotIn(_WORKBOOK_RESULT_CONTEXT_KEY, state)


if __name__ == "__main__":
    import unittest

    unittest.main()
