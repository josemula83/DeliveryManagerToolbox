from io import BytesIO
from pathlib import Path
import unittest

from openpyxl import Workbook

from delivery_manager_toolbox.modules.facturacion import MODULE
from delivery_manager_toolbox.modules.facturacion.excel_reader import (
    WorkbookInspectionError,
    inspect_workbook,
    parse_account_description,
    parse_hierarchical_worksheet,
    preview_worksheet,
)
from delivery_manager_toolbox.modules.facturacion.configuration import (
    BillingConfiguration,
    add_fixed_management_account,
    add_management_person_account_exception,
    add_management_rate_block,
    add_management_person,
    add_no_management_account,
    available_management_rate_accounts,
    available_no_management_accounts,
    available_exception_accounts,
    available_exception_people,
    available_values,
    configuration_from_json,
    configuration_to_json,
    load_configuration,
    reconcile_management_person_account_exceptions,
    remove_management_person_account_exception,
    remove_management_rate_block,
    replace_management_rate_block,
    replace_management_people,
    replace_no_management_accounts,
    save_configuration,
    update_management_person_percentage,
)
from delivery_manager_toolbox.modules.facturacion.models import (
    AccountAssignment,
    ColumnMapping,
    HierarchicalWorksheetAnalysis,
    Person,
)
from delivery_manager_toolbox.modules.facturacion.validation import (
    ConfigurationValidationError,
    normalize_identifier,
)


def build_test_workbook() -> bytes:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Resumen mensual"
    summary.append(["Cuenta", "Importe"])
    summary.append(["Cuenta A", 100])
    summary.append(["Cuenta B", 200])

    detail = workbook.create_sheet("Detalle")
    detail.append(["Persona", "Horas"])
    detail.append(["Persona 1", 10])
    detail.append(["Persona 2", 20])
    detail.append(["Persona 3", 30])

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def build_detection_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Datos"
    sheet.append(["Persona", "Cuenta", "Horas", "Tarifa"])
    sheet.append(["Ana Demo", "Cuenta Uno", 8, 90])
    sheet.append(["Bruno Demo", "Cuenta Dos", 4.5, 100])
    sheet.append(["Ana Demo", "Cuenta Dos", 2, 100])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def build_hierarchical_workbook() -> bytes:
    workbook = Workbook()
    november = workbook.active
    november.title = "Horas servicio noviembre"
    november.append([None, None, 1, 2, 3])
    november.append([200001, "Ana Demo", None, None, None])
    november.append([
        None,
        "Banco > Cliente > Servicio Uno > EXT-100-001 - Tarifa 17,00 EUR - Operativa",
        8,
        0,
        None,
    ])
    november.append([None, "F", "F", None, None])
    november.append([
        None,
        "Banco > Cliente > Servicio Uno > EXT-100-001 - Tarifa 18,50 EUR - Proyecto",
        None,
        None,
        None,
    ])
    november.append([200002, "Bruno Demo", None, None, None])
    november.append([
        None,
        "Banco > Cliente > Servicio Uno > EXT-100-001 - Tarifa 17,00 EUR - Operativa",
        4,
        None,
        None,
    ])
    november.append([None, "V", "V", None, None])
    november.append([None, "A", "A", None, None])
    november.append([None, "E", "E", None, None])
    november.append([None, "Formación", 8, None, None])
    november.append([200003, "Carla Sin Horas", None, None, None])
    november.append([200004, "Diego Demo", None, None, None])
    november.append([
        None,
        "Banco > Cliente > Servicio Dos > EXT-200-002 - Tarifa 1.234,50 EUR",
        None,
        None,
        None,
    ])

    december = workbook.create_sheet("Horas servicio diciembre")
    december.append([None, None, 1, 2])
    december.append([300001, "Elena Demo", None, None])
    december.append([
        None,
        "Banco > Cliente > Servicio Tres > EXT-300-003 - Tarifa 20,00 EUR - Operativa",
        7.5,
        None,
    ])

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def build_assignment(employee_id: str, name: str, account_code: str, service: str) -> AccountAssignment:
    return AccountAssignment(
        Person(employee_id, name),
        account_code,
        service,
        None,
        f"{service} > {account_code}",
        None,
    )


class FacturacionModuleTests(unittest.TestCase):
    def test_module_can_be_loaded(self) -> None:
        self.assertEqual(MODULE.key, "facturacion")
        self.assertEqual(MODULE.title, "Facturación")
        self.assertTrue(callable(MODULE.render))

    def test_excel_workbook_is_read(self) -> None:
        information = inspect_workbook(build_test_workbook(), "facturacion.xlsx")

        self.assertEqual(information.filename, "facturacion.xlsx")
        self.assertEqual(information.sheet_count, 2)
        self.assertEqual(information.approximate_records, 5)

    def test_sheet_names_are_identified_in_order(self) -> None:
        information = inspect_workbook(build_test_workbook(), "facturacion.xlsx")

        self.assertEqual(information.sheet_names, ("Resumen mensual", "Detalle"))
        self.assertEqual(
            [worksheet.approximate_records for worksheet in information.worksheets],
            [2, 3],
        )

    def test_invalid_excel_is_rejected(self) -> None:
        with self.assertRaises(WorkbookInspectionError):
            inspect_workbook(b"not-an-excel-file", "facturacion.xlsx")

    def test_basic_columns_are_detected_without_guessing(self) -> None:
        information = inspect_workbook(build_detection_workbook(), "datos.xlsx")

        self.assertEqual(information.suggested_sheet, "Datos")
        self.assertEqual(
            information.suggested_mapping,
            ColumnMapping("Persona", "Cuenta", "Horas", "Tarifa"),
        )

    def test_people_accounts_rates_and_hours_are_previewed(self) -> None:
        preview = preview_worksheet(
            build_detection_workbook(),
            "Datos",
            ColumnMapping("Persona", "Cuenta", "Horas", "Tarifa"),
        )

        self.assertEqual(preview.people, ("Ana Demo", "Bruno Demo"))
        self.assertEqual(preview.accounts, ("Cuenta Uno", "Cuenta Dos"))
        self.assertEqual(preview.rates, (90, 100))
        self.assertEqual(preview.hours, (8, 4.5, 2))


class FacturacionConfigurationTests(unittest.TestCase):
    def test_configured_values_are_removed_from_available_options(self) -> None:
        candidates = ("Ána Demo", "Bruno Demo", "Carla Demo")

        self.assertEqual(
            available_values(candidates, (" ana   demo ", "BRUNO DEMO")),
            ("Carla Demo",),
        )

    def test_management_person_is_created(self) -> None:
        configuration = add_management_person(BillingConfiguration(), " Ana Demo ", 25)
        self.assertEqual(configuration.management_people[0].name, "Ana Demo")
        self.assertEqual(configuration.management_people[0].free_capacity_percentage, 25)

    def test_management_person_percentage_is_updated(self) -> None:
        configuration = add_management_person(BillingConfiguration(), "Ana Demo", 25)
        updated = update_management_person_percentage(configuration, "ana demo", 40)
        self.assertEqual(updated.management_people[0].free_capacity_percentage, 40)

    def test_percentage_must_be_between_zero_and_one_hundred(self) -> None:
        for value in (-0.1, 100.1):
            with self.subTest(value=value), self.assertRaises(ConfigurationValidationError):
                add_management_person(BillingConfiguration(), "Ana Demo", value)

    def test_no_management_account_is_created(self) -> None:
        configuration = add_no_management_account(BillingConfiguration(), "Cuenta Uno")
        self.assertEqual(configuration.no_management_accounts, ("Cuenta Uno",))

    def test_fixed_management_account_is_created(self) -> None:
        configuration = add_fixed_management_account(BillingConfiguration(), "Cuenta Uno", 3.5)
        self.assertEqual(configuration.fixed_management_accounts[0].fixed_hours, 3.5)

    def test_fixed_hours_cannot_be_negative(self) -> None:
        with self.assertRaises(ConfigurationValidationError):
            add_fixed_management_account(BillingConfiguration(), "Cuenta Uno", -0.5)

    def test_fixed_hours_use_half_hour_steps(self) -> None:
        with self.assertRaises(ConfigurationValidationError):
            add_fixed_management_account(BillingConfiguration(), "Cuenta Uno", 1.25)

    def test_management_rate_block_is_created(self) -> None:
        configuration = add_management_rate_block(
            BillingConfiguration(), "Bloque Norte", ["Cuenta Uno", "Cuenta Dos"], 17.25
        )
        self.assertEqual(configuration.management_rate_blocks[0].accounts, ("Cuenta Uno", "Cuenta Dos"))
        self.assertEqual(configuration.management_rate_blocks[0].management_rate, 17.25)

    def test_management_rate_block_is_updated(self) -> None:
        configuration = add_management_rate_block(BillingConfiguration(), "Bloque Norte", ["Cuenta Uno"], 17)
        updated = replace_management_rate_block(
            configuration, "Bloque Norte", "Bloque Norte", ["Cuenta Dos"], 18.5
        )
        self.assertEqual(updated.management_rate_blocks[0].accounts, ("Cuenta Dos",))
        self.assertEqual(updated.management_rate_blocks[0].management_rate, 18.5)

    def test_management_rate_block_is_removed(self) -> None:
        configuration = add_management_rate_block(BillingConfiguration(), "Bloque Norte", ["Cuenta Uno"], 17)
        self.assertEqual(remove_management_rate_block(configuration, "bloque norte").management_rate_blocks, ())

    def test_account_cannot_belong_to_two_rate_blocks(self) -> None:
        configuration = add_management_rate_block(BillingConfiguration(), "Bloque Uno", ["Cuenta Uno"], 17)
        with self.assertRaises(ConfigurationValidationError):
            add_management_rate_block(configuration, "Bloque Dos", [" cuenta uno "], 18)

    def test_rate_block_requires_unique_name_and_accounts(self) -> None:
        configuration = add_management_rate_block(BillingConfiguration(), "Bloque Uno", ["Cuenta Uno"], 17)
        with self.assertRaises(ConfigurationValidationError):
            add_management_rate_block(configuration, " bloque uno ", ["Cuenta Dos"], 18)
        with self.assertRaises(ConfigurationValidationError):
            add_management_rate_block(
                BillingConfiguration(),
                "Bloque duplicado",
                ["Cuenta Uno", " cuenta uno "],
                17,
            )

    def test_rate_block_name_is_required(self) -> None:
        with self.assertRaises(ConfigurationValidationError):
            add_management_rate_block(BillingConfiguration(), " ", ["Cuenta Uno"], 17)

    def test_negative_management_rate_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationValidationError):
            add_management_rate_block(BillingConfiguration(), "Bloque", ["Cuenta Uno"], -0.01)

    def test_all_detected_accounts_are_initially_available_for_a_rate_block(self) -> None:
        catalog = ("EXT-1", "EXT-2", "EXT-3")
        self.assertEqual(
            available_management_rate_accounts(catalog, BillingConfiguration()),
            catalog,
        )

    def test_no_management_accounts_are_excluded_from_rate_blocks(self) -> None:
        configuration = add_no_management_account(BillingConfiguration(), "EXT-1")
        self.assertEqual(
            available_management_rate_accounts(("EXT-1", "EXT-2"), configuration),
            ("EXT-2",),
        )

    def test_accounts_in_other_rate_blocks_are_excluded(self) -> None:
        configuration = add_management_rate_block(BillingConfiguration(), "Bloque A", ["EXT-1"], 17)
        self.assertEqual(
            available_management_rate_accounts(("EXT-1", "EXT-2"), configuration),
            ("EXT-2",),
        )

    def test_editing_rate_block_keeps_its_current_accounts_available(self) -> None:
        configuration = add_management_rate_block(BillingConfiguration(), "Bloque A", ["EXT-1"], 17)
        configuration = add_management_rate_block(configuration, "Bloque B", ["EXT-2"], 18)
        self.assertEqual(
            available_management_rate_accounts(("EXT-1", "EXT-2", "EXT-3"), configuration, "Bloque A"),
            ("EXT-1", "EXT-3"),
        )

    def test_fixed_management_account_remains_available_for_rate_blocks(self) -> None:
        configuration = add_fixed_management_account(BillingConfiguration(), "EXT-1", 3.5)
        self.assertEqual(
            available_management_rate_accounts(("EXT-1", "EXT-2"), configuration),
            ("EXT-1", "EXT-2"),
        )

    def test_removing_rate_block_releases_its_accounts(self) -> None:
        configuration = add_management_rate_block(BillingConfiguration(), "Bloque A", ["EXT-1"], 17)
        configuration = remove_management_rate_block(configuration, "Bloque A")
        self.assertEqual(
            available_management_rate_accounts(("EXT-1", "EXT-2"), configuration),
            ("EXT-1", "EXT-2"),
        )

    def test_removing_no_management_account_releases_it_for_rate_blocks(self) -> None:
        configuration = add_no_management_account(BillingConfiguration(), "EXT-1")
        configuration = replace_no_management_accounts(configuration, [])
        self.assertEqual(
            available_management_rate_accounts(("EXT-1", "EXT-2"), configuration),
            ("EXT-1", "EXT-2"),
        )

    def test_rate_block_accounts_are_excluded_from_no_management_options(self) -> None:
        configuration = add_management_rate_block(BillingConfiguration(), "Bloque A", ["EXT-1"], 17)
        self.assertEqual(
            available_no_management_accounts(("EXT-1", "EXT-2"), configuration),
            ("EXT-2",),
        )

    def test_rate_block_and_no_management_conflict_is_rejected_both_ways(self) -> None:
        with self.assertRaisesRegex(ConfigurationValidationError, "sin gestión"):
            add_management_rate_block(
                add_no_management_account(BillingConfiguration(), "EXT-1"),
                "Bloque A",
                ["EXT-1"],
                17,
            )
        with self.assertRaisesRegex(ConfigurationValidationError, "pertenece al bloque"):
            add_no_management_account(
                add_management_rate_block(BillingConfiguration(), "Bloque A", ["EXT-1"], 17),
                "EXT-1",
            )

    def test_duplicates_ignore_spaces_case_and_accents(self) -> None:
        configuration = add_management_person(BillingConfiguration(), "Ána   Demo", 10)
        with self.assertRaises(ConfigurationValidationError):
            add_management_person(configuration, " ana demo ", 20)
        self.assertEqual(normalize_identifier(" ÁNA   Demo "), "ana demo")

    def test_configuration_is_saved_and_loaded(self) -> None:
        configuration = add_management_person(BillingConfiguration(), "Ana Demo", 30)
        configuration = add_management_rate_block(configuration, "Bloque Demo", ["EXT-1"], 17.25)
        path = Path.cwd() / "config" / ".facturacion-test.json"
        try:
            save_configuration(configuration, path)
            self.assertEqual(load_configuration(path), configuration)
        finally:
            path.unlink(missing_ok=True)

    def test_configuration_can_be_exported_and_imported(self) -> None:
        configuration = add_management_rate_block(BillingConfiguration(), "Bloque", ["Cuenta"], 17.5)
        self.assertEqual(configuration_from_json(configuration_to_json(configuration)), configuration)
        self.assertIn('"schema_version": 3', configuration_to_json(configuration))
        self.assertIn('"management_rate": 17.5', configuration_to_json(configuration))

    def test_schema_v1_is_migrated_to_management_rate_blocks(self) -> None:
        legacy_json = """{
          "schema_version": 1,
          "management_people": [],
          "non_billable_accounts": ["EXT-1"],
          "fixed_management_accounts": [],
          "fixed_amount_groups": [
            {"name": "Bloque heredado", "accounts": ["EXT-2"], "fixed_amount": 17.25}
          ],
          "preserve_manager_original_hours": false,
          "source_sheet": null,
          "column_mapping": {}
        }"""
        configuration = configuration_from_json(legacy_json)
        self.assertEqual(configuration.no_management_accounts, ("EXT-1",))
        self.assertEqual(configuration.management_rate_blocks[0].name, "Bloque heredado")
        self.assertEqual(configuration.management_rate_blocks[0].management_rate, 17.25)

    def test_invalid_json_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationValidationError):
            configuration_from_json("{contenido inválido")


class ManagementPersonAccountExceptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analysis = parse_hierarchical_worksheet(
            build_hierarchical_workbook(),
            "Horas servicio noviembre",
        )
        configuration = add_management_person(BillingConfiguration(), "Ana Demo", 25)
        self.configuration = add_management_person(configuration, "Bruno Demo", 50)

    def test_only_management_people_are_available(self) -> None:
        people = available_exception_people(self.configuration, self.analysis)
        self.assertEqual([(person.employee_id, person.name) for person in people], [
            ("200001", "Ana Demo"),
            ("200002", "Bruno Demo"),
        ])

    def test_available_accounts_depend_on_selected_person(self) -> None:
        ana_accounts = available_exception_accounts(
            self.configuration, self.analysis, "200001", "Ana Demo"
        )
        diego_configuration = add_management_person(BillingConfiguration(), "Diego Demo", 0)
        diego_accounts = available_exception_accounts(
            diego_configuration, self.analysis, "200004", "Diego Demo"
        )
        self.assertEqual([item.account_code for item in ana_accounts], ["EXT-100-001"])
        self.assertEqual([item.account_code for item in diego_accounts], ["EXT-200-002"])

    def test_account_where_person_does_not_work_is_not_available(self) -> None:
        accounts = available_exception_accounts(
            self.configuration, self.analysis, "200001", "Ana Demo"
        )
        self.assertNotIn("EXT-200-002", [item.account_code for item in accounts])

    def test_valid_exception_is_created(self) -> None:
        updated = add_management_person_account_exception(
            self.configuration,
            "200001",
            "Ana Demo",
            "EXT-100-001",
            self.analysis.assignments,
        )
        exception = updated.management_person_account_exceptions[0]
        self.assertEqual((exception.employee_id, exception.account_code), ("200001", "EXT-100-001"))

    def test_duplicate_exception_is_rejected(self) -> None:
        updated = add_management_person_account_exception(
            self.configuration, "200001", "Ana Demo", "EXT-100-001", self.analysis.assignments
        )
        with self.assertRaises(ConfigurationValidationError):
            add_management_person_account_exception(
                updated, "200001", "Ana Demo", "EXT-100-001", self.analysis.assignments
            )

    def test_exception_requires_a_management_person(self) -> None:
        with self.assertRaisesRegex(ConfigurationValidationError, "Persona en Gestión"):
            add_management_person_account_exception(
                BillingConfiguration(),
                "200001",
                "Ana Demo",
                "EXT-100-001",
                self.analysis.assignments,
            )

    def test_exception_for_unassigned_account_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationValidationError, "no aparece en la cuenta"):
            add_management_person_account_exception(
                self.configuration, "200001", "Ana Demo", "EXT-200-002", self.analysis.assignments
            )

    def test_exception_is_removed(self) -> None:
        updated = add_management_person_account_exception(
            self.configuration, "200001", "Ana Demo", "EXT-100-001", self.analysis.assignments
        )
        updated = remove_management_person_account_exception(updated, "200001", "EXT-100-001")
        self.assertEqual(updated.management_person_account_exceptions, ())

    def test_removing_management_person_removes_its_exceptions(self) -> None:
        updated = add_management_person_account_exception(
            self.configuration, "200001", "Ana Demo", "EXT-100-001", self.analysis.assignments
        )
        updated = replace_management_people(updated, [("Bruno Demo", 50)])
        self.assertEqual(updated.management_person_account_exceptions, ())

    def test_changing_excel_removes_invalid_exceptions(self) -> None:
        updated = add_management_person_account_exception(
            self.configuration, "200001", "Ana Demo", "EXT-100-001", self.analysis.assignments
        )
        december = parse_hierarchical_worksheet(
            build_hierarchical_workbook(),
            "Horas servicio diciembre",
        )
        reconciled, removed = reconcile_management_person_account_exceptions(updated, december)
        self.assertEqual(removed, 1)
        self.assertEqual(reconciled.management_person_account_exceptions, ())

    def test_exceptions_are_saved_and_loaded_through_json(self) -> None:
        updated = add_management_person_account_exception(
            self.configuration, "200001", "Ana Demo", "EXT-100-001", self.analysis.assignments
        )
        self.assertEqual(configuration_from_json(configuration_to_json(updated)), updated)

    def test_employee_id_distinguishes_people_with_similar_names(self) -> None:
        assignments = (
            build_assignment("100", "Ana Demo", "EXT-1", "Servicio Uno"),
            build_assignment("101", "Ana Demos", "EXT-2", "Servicio Dos"),
        )
        configuration = add_management_person(BillingConfiguration(), "Ana Demo", 0)
        custom_analysis = HierarchicalWorksheetAnalysis(
            "Datos",
            (Person("100", "Ana Demo"), Person("101", "Ana Demos")),
            assignments,
            (),
            (),
            0,
        )
        accounts = available_exception_accounts(configuration, custom_analysis, "100", "Ana Demo")
        self.assertEqual([item.account_code for item in accounts], ["EXT-1"])
        created = add_management_person_account_exception(
            configuration, "100", "Ana Demo", "EXT-1", assignments
        )
        self.assertEqual(created.management_person_account_exceptions[0].employee_id, "100")

    def test_schema_v2_ignores_deprecated_global_boolean(self) -> None:
        legacy_json = """{
          "schema_version": 2,
          "management_people": [{"name": "Ana Demo", "free_capacity_percentage": 25}],
          "no_management_accounts": [],
          "fixed_management_accounts": [],
          "management_rate_blocks": [],
          "preserve_manager_original_hours": true,
          "source_sheet": null,
          "column_mapping": {}
        }"""
        configuration = configuration_from_json(legacy_json)
        self.assertEqual(configuration.management_person_account_exceptions, ())
        self.assertNotIn("preserve_manager_original_hours", configuration_to_json(configuration))


class HierarchicalExcelParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contents = build_hierarchical_workbook()
        self.analysis = parse_hierarchical_worksheet(self.contents, "Horas servicio noviembre")

    def test_detects_person_rows(self) -> None:
        self.assertEqual([person.employee_id for person in self.analysis.people], ["200001", "200002", "200003", "200004"])
        self.assertEqual(self.analysis.people[0].name, "Ana Demo")

    def test_detects_multiple_accounts_for_one_person(self) -> None:
        assignments = [item for item in self.analysis.assignments if item.person.employee_id == "200001"]
        self.assertEqual(len(assignments), 2)

    def test_changes_current_person(self) -> None:
        self.assertEqual(self.analysis.assignments[2].person.name, "Bruno Demo")
        self.assertEqual(self.analysis.assignments[3].person.name, "Diego Demo")

    def test_ignores_auxiliary_rows(self) -> None:
        descriptions = [item.original_description for item in self.analysis.assignments]
        for auxiliary in ("F", "V", "A", "E", "Formación"):
            self.assertNotIn(auxiliary, descriptions)
        self.assertEqual(len(self.analysis.assignments), 4)

    def test_extracts_ext_code_and_service(self) -> None:
        parsed = parse_account_description(
            "Banco > Cliente > Retail Digital Services > EXT-300690-00161 - Tarifa 17,00 EUR - Operativa"
        )
        self.assertEqual(parsed.account_code, "EXT-300690-00161")
        self.assertEqual(parsed.service_name, "Retail Digital Services")
        self.assertEqual(parsed.account_type, "Operativa")

    def test_extracts_rate_with_decimal_comma(self) -> None:
        self.assertEqual(self.analysis.assignments[0].rate, 17.0)
        self.assertEqual(self.analysis.assignments[3].rate, 1234.5)

    def test_same_account_with_different_rates_is_not_deduplicated(self) -> None:
        ana_assignments = [item for item in self.analysis.assignments if item.person.name == "Ana Demo"]
        self.assertEqual([item.account_code for item in ana_assignments], ["EXT-100-001", "EXT-100-001"])
        self.assertEqual([item.rate for item in ana_assignments], [17.0, 18.5])

    def test_repeated_account_for_different_people_is_preserved(self) -> None:
        repeated = [item for item in self.analysis.assignments if item.account_code == "EXT-100-001"]
        self.assertEqual(len(repeated), 3)
        self.assertEqual({item.person.name for item in repeated}, {"Ana Demo", "Bruno Demo"})
        self.assertEqual(len(self.analysis.accounts), 2)

    def test_different_month_sheets_are_parsed_independently(self) -> None:
        december = parse_hierarchical_worksheet(self.contents, "Horas servicio diciembre")
        self.assertEqual([person.name for person in december.people], ["Elena Demo"])
        self.assertEqual(december.assignments[0].account_code, "EXT-300-003")

    def test_person_without_hours_is_preserved(self) -> None:
        summary = next(item for item in self.analysis.person_summaries if item.name == "Carla Sin Horas")
        self.assertEqual(summary.account_count, 0)

    def test_account_without_hours_is_preserved(self) -> None:
        account = next(item for item in self.analysis.assignments if item.account_code == "EXT-200-002")
        self.assertEqual(account.daily_hours, ())

    def test_daily_hours_are_normalized_without_calculating_distribution(self) -> None:
        records = self.analysis.normalized_hours
        self.assertEqual([(item.day, item.hours) for item in records[:3]], [(1, 8.0), (2, 0.0), (1, 4.0)])


if __name__ == "__main__":
    unittest.main()

