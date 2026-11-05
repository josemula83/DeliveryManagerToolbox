import unittest

from delivery_manager_toolbox.modules.facturacion.business import (
    build_clean_billing_model,
    calculate_account_base_hours,
    calculate_billing,
    calculate_management_hours,
)
from delivery_manager_toolbox.modules.facturacion.configuration import (
    BillingConfiguration,
    FixedManagementAccount,
    add_fixed_management_account,
    add_management_person,
    add_management_person_account_exception,
    add_no_management_account,
)
from delivery_manager_toolbox.modules.facturacion.models import (
    AccountAssignment,
    AccountBaseHours,
    CalculationType,
    DailyHours,
    HierarchicalWorksheetAnalysis,
    Person,
)


def assignment(
    employee_id: str,
    name: str,
    account_code: str,
    *hours: float,
    service: str = "Servicio Demo",
    rate: float | None = None,
) -> AccountAssignment:
    return AccountAssignment(
        Person(employee_id, name),
        account_code,
        service,
        rate,
        f"Cliente > {service} > {account_code}",
        None,
        tuple(DailyHours(index + 1, value) for index, value in enumerate(hours)),
    )


def analysis_from(*assignments: AccountAssignment) -> HierarchicalWorksheetAnalysis:
    people: dict[str, Person] = {}
    for item in assignments:
        people.setdefault(item.person.employee_id, item.person)
    return HierarchicalWorksheetAnalysis(
        "Datos",
        tuple(people.values()),
        assignments,
        (),
        (),
        len(assignments),
    )


class CleanBillingModelTests(unittest.TestCase):
    def test_normal_person_is_preserved(self) -> None:
        source = analysis_from(assignment("1", "Persona Normal", "EXT-1", 8))
        clean = build_clean_billing_model(source, BillingConfiguration())
        self.assertEqual(clean.assignments, source.assignments)
        self.assertEqual(clean.audit.assignments_removed, 0)

    def test_management_person_is_removed(self) -> None:
        source = analysis_from(assignment("1", "Persona Gestión", "EXT-1", 8))
        configuration = add_management_person(BillingConfiguration(), "Persona Gestión", 50)
        clean = build_clean_billing_model(source, configuration)
        self.assertEqual(clean.assignments, ())
        self.assertEqual(clean.audit.management_people_detected, 1)

    def test_management_person_without_assignments_is_still_detected(self) -> None:
        source = HierarchicalWorksheetAnalysis(
            "Datos",
            (Person("1", "Persona Gestión"),),
            (),
            (),
            (),
            1,
        )
        configuration = add_management_person(BillingConfiguration(), "Persona Gestión", 50)
        clean = build_clean_billing_model(source, configuration)
        self.assertEqual(clean.audit.management_people_detected, 1)
        self.assertEqual(clean.audit.assignments_removed, 0)

    def test_management_person_is_removed_from_all_accounts(self) -> None:
        source = analysis_from(
            assignment("1", "Persona Gestión", "EXT-1", 8),
            assignment("1", "Persona Gestión", "EXT-2", 6),
        )
        configuration = add_management_person(BillingConfiguration(), "Persona Gestión", 50)
        clean = build_clean_billing_model(source, configuration)
        self.assertEqual(clean.audit.assignments_removed, 2)
        self.assertEqual(clean.assignments, ())

    def test_person_account_exception_is_preserved(self) -> None:
        source = analysis_from(assignment("1", "Persona Gestión", "EXT-1", 8))
        configuration = add_management_person(BillingConfiguration(), "Persona Gestión", 50)
        configuration = add_management_person_account_exception(
            configuration, "1", "Persona Gestión", "EXT-1", source.assignments
        )
        clean = build_clean_billing_model(source, configuration)
        self.assertEqual(clean.assignments, source.assignments)
        self.assertEqual(clean.audit.exceptions_preserved, 1)

    def test_other_accounts_of_same_manager_are_still_removed(self) -> None:
        source = analysis_from(
            assignment("1", "Persona Gestión", "EXT-1", 8),
            assignment("1", "Persona Gestión", "EXT-2", 6),
        )
        configuration = add_management_person(BillingConfiguration(), "Persona Gestión", 50)
        configuration = add_management_person_account_exception(
            configuration, "1", "Persona Gestión", "EXT-1", source.assignments
        )
        clean = build_clean_billing_model(source, configuration)
        self.assertEqual([item.account_code for item in clean.assignments], ["EXT-1"])
        self.assertEqual([item.account_code for item in clean.removed_assignments], ["EXT-2"])

    def test_two_management_people_are_removed(self) -> None:
        source = analysis_from(
            assignment("1", "Gestora Uno", "EXT-1", 8),
            assignment("2", "Gestor Dos", "EXT-1", 7),
        )
        configuration = add_management_person(BillingConfiguration(), "Gestora Uno", 50)
        configuration = add_management_person(configuration, "Gestor Dos", 50)
        clean = build_clean_billing_model(source, configuration)
        self.assertEqual(clean.audit.management_people_detected, 2)
        self.assertEqual(clean.audit.assignments_removed, 2)

    def test_employee_id_and_exact_name_avoid_similar_name_collision(self) -> None:
        source = analysis_from(
            assignment("100", "Ana Demo", "EXT-1", 8),
            assignment("101", "Ana Demos", "EXT-2", 7),
        )
        configuration = add_management_person(BillingConfiguration(), "Ana Demo", 50)
        clean = build_clean_billing_model(source, configuration)
        self.assertEqual([item.person.employee_id for item in clean.assignments], ["101"])
        self.assertEqual(clean.removed_assignments[0].employee_id, "100")

    def test_removed_hours_are_summed(self) -> None:
        source = analysis_from(
            assignment("1", "Persona Gestión", "EXT-1", 8, 2),
            assignment("1", "Persona Gestión", "EXT-2", 6.5),
        )
        configuration = add_management_person(BillingConfiguration(), "Persona Gestión", 50)
        clean = build_clean_billing_model(source, configuration)
        self.assertEqual(clean.audit.hours_removed, 16.5)

    def test_exception_hours_are_summed_across_tariff_rows(self) -> None:
        source = analysis_from(
            assignment("1", "Persona Gestión", "EXT-1", 8, rate=17),
            assignment("1", "Persona Gestión", "EXT-1", 2.5, rate=18),
        )
        configuration = add_management_person(BillingConfiguration(), "Persona Gestión", 50)
        configuration = add_management_person_account_exception(
            configuration, "1", "Persona Gestión", "EXT-1", source.assignments
        )
        clean = build_clean_billing_model(source, configuration)
        self.assertEqual(clean.audit.exceptions_preserved, 1)
        self.assertEqual(clean.audit.hours_preserved_by_exception, 10.5)

    def test_original_model_is_not_modified(self) -> None:
        source = analysis_from(assignment("1", "Persona Gestión", "EXT-1", 8))
        original = source.assignments
        configuration = add_management_person(BillingConfiguration(), "Persona Gestión", 50)
        build_clean_billing_model(source, configuration)
        self.assertIs(source.assignments, original)
        self.assertEqual(source.assignments[0].total_hours, 8)


class ManagementHoursCalculationTests(unittest.TestCase):
    def test_percentage_examples(self) -> None:
        examples = ((100, 5), (83, 4), (89, 4), (91, 4.5), (137, 6.5), (6, 0.5), (0, 0))
        for base_hours, expected in examples:
            with self.subTest(base_hours=base_hours):
                result = calculate_management_hours(
                    AccountBaseHours("EXT-1", "Servicio", base_hours),
                    BillingConfiguration(),
                )
                self.assertEqual(result.management_hours, expected)
                self.assertEqual(result.calculation_type, CalculationType.PERCENTAGE)

    def test_no_management_account_returns_zero(self) -> None:
        configuration = add_no_management_account(BillingConfiguration(), "EXT-1")
        result = calculate_management_hours(AccountBaseHours("EXT-1", "Servicio", 100), configuration)
        self.assertEqual(result.management_hours, 0)
        self.assertEqual(result.calculation_type, CalculationType.NO_MANAGEMENT)

    def test_fixed_management_uses_configured_value(self) -> None:
        configuration = add_fixed_management_account(BillingConfiguration(), "EXT-1", 7.5)
        result = calculate_management_hours(AccountBaseHours("EXT-1", "Servicio", 200), configuration)
        self.assertEqual(result.management_hours, 7.5)
        self.assertEqual(result.calculation_type, CalculationType.FIXED)

    def test_no_management_has_priority_over_fixed_management(self) -> None:
        configuration = BillingConfiguration(
            no_management_accounts=("EXT-1",),
            fixed_management_accounts=(FixedManagementAccount("EXT-1", 7.5),),
        )
        result = calculate_management_hours(AccountBaseHours("EXT-1", "Servicio", 200), configuration)
        self.assertEqual(result.management_hours, 0)
        self.assertEqual(result.calculation_type, CalculationType.NO_MANAGEMENT)

    def test_multiple_people_in_one_account_are_aggregated(self) -> None:
        source = analysis_from(
            assignment("1", "Persona Uno", "EXT-1", 80),
            assignment("2", "Persona Dos", "EXT-1", 90),
            assignment("3", "Persona Tres", "EXT-1", 20),
        )
        clean = build_clean_billing_model(source, BillingConfiguration())
        base = calculate_account_base_hours(clean, source.accounts)
        self.assertEqual(base[0].base_hours, 190)

    def test_same_account_with_multiple_rates_is_aggregated_by_code(self) -> None:
        source = analysis_from(
            assignment("1", "Persona Uno", "EXT-1", 10, rate=17),
            assignment("1", "Persona Uno", "EXT-1", 5, rate=18),
        )
        clean = build_clean_billing_model(source, BillingConfiguration())
        base = calculate_account_base_hours(clean, source.accounts)
        self.assertEqual(len(base), 1)
        self.assertEqual(base[0].base_hours, 15)

    def test_full_result_builds_global_summary(self) -> None:
        source = analysis_from(
            assignment("1", "Persona Gestión", "EXT-1", 20),
            assignment("2", "Persona Normal", "EXT-1", 80),
            assignment("2", "Persona Normal", "EXT-2", 6),
        )
        configuration = add_management_person(BillingConfiguration(), "Persona Gestión", 50)
        result = calculate_billing(source, configuration)
        self.assertEqual(result.summary.assignments_removed, 1)
        self.assertEqual(result.summary.hours_removed, 20)
        self.assertEqual(result.summary.total_accounts, 2)
        self.assertEqual(result.summary.total_base_hours, 86)
        self.assertEqual(result.summary.total_management_hours, 4.5)


if __name__ == "__main__":
    unittest.main()
