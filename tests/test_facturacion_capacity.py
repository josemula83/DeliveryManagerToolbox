from io import BytesIO
from dataclasses import replace
import unittest

from openpyxl import Workbook

from delivery_manager_toolbox.modules.facturacion.allocation import (
    calculate_management_allocation,
    calculate_manager_targets,
)
from delivery_manager_toolbox.modules.facturacion.configuration import (
    BillingConfiguration,
    ManagementPerson,
    ManagementPersonAccountException,
    add_management_person,
    resolve_management_person_in_sheet,
    resolve_management_people_in_sheet,
)
from delivery_manager_toolbox.modules.facturacion.business import (
    calculate_sheet_management,
    calculate_sheet_rate_optimization,
)
from delivery_manager_toolbox.modules.facturacion.excel_reader import parse_hierarchical_worksheet
from delivery_manager_toolbox.modules.facturacion.models import (
    AccountManagementCalculation,
    AllocationStatus,
    CalculationType,
    CapacitySource,
    ManagementPersonMatchingMethod,
    Person,
    PersonMonthlyWorkCapacity,
    SheetAnalysis,
)


def workbook_with_capacities(
    november_total: float | None = 181,
    december_total: float | None = 160,
) -> bytes:
    workbook = Workbook()
    for index, (sheet_name, total) in enumerate((
        ("Horas servicio noviembre", november_total),
        ("Horas servicio diciembre", december_total),
    )):
        worksheet = workbook.active if index == 0 else workbook.create_sheet()
        worksheet.title = sheet_name
        worksheet.append([None, None, 1, 2, 3, "Horas"])
        worksheet.append([1001, "Daniel Alarcón Guerrero", 9, "V", 7, total])
        worksheet.append([2001, "Persona Normal", 9, 9, 7, total])
        worksheet.append([None, "Cliente > Servicio A > EXT-A - Tarifa 26 EUR", 10, 10, None, 20])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def calculation(account_code: str, hours: float) -> AccountManagementCalculation:
    return AccountManagementCalculation(
        account_code=account_code,
        service_name=f"Servicio {account_code}",
        base_hours=hours * 20,
        raw_management_hours=hours,
        management_hours=hours,
        calculation_type=CalculationType.PERCENTAGE,
        percentage_applied=5,
    )


def manager_configuration(percentage: float) -> BillingConfiguration:
    return add_management_person(
        BillingConfiguration(),
        "Daniel Alarcón Guerrero",
        percentage,
    )


def sheet_with_people(
    *records: tuple[str, str, float],
) -> SheetAnalysis:
    people = tuple(
        Person(employee_id, name, index)
        for index, (employee_id, name, _) in enumerate(records, 2)
    )
    capacities = tuple(
        PersonMonthlyWorkCapacity(
            employee_id=employee_id,
            person_name=name,
            monthly_workable_hours=hours,
            capacity_source=CapacitySource.PERSON_TOTAL_HOURS,
            source_row=index,
        )
        for index, (employee_id, name, hours) in enumerate(records, 2)
    )
    return SheetAnalysis("Mes", people, (), (), (), len(records), capacities)


class ManagementPersonResolutionTests(unittest.TestCase):
    def test_exact_employee_id_has_priority_over_name(self) -> None:
        source = sheet_with_people(("105275", "Nombre actualizado", 181))
        manager = ManagementPerson("Nombre histórico", 50, "105275")

        resolution = resolve_management_person_in_sheet(manager, source)

        self.assertEqual(resolution.matching_method, ManagementPersonMatchingMethod.EXACT_EMPLOYEE_ID)
        self.assertEqual(resolution.matched_employee_id, "105275")
        self.assertEqual(resolution.matched_person_name, "Nombre actualizado")
        self.assertEqual(resolution.matched_source_row, 2)
        self.assertEqual(resolution.monthly_workable_hours, 181)

    def test_normalized_name_matches_accents_without_configured_employee_id(self) -> None:
        source = sheet_with_people(("105275", "José Antonio Ferrandez Mula", 181))
        manager = ManagementPerson("Jose Antonio Ferrandez Mula", 50)

        resolution = resolve_management_person_in_sheet(manager, source)

        self.assertIsNone(resolution.configured_employee_id)
        self.assertEqual(resolution.matching_method, ManagementPersonMatchingMethod.NORMALIZED_NAME)
        self.assertEqual(resolution.matched_employee_id, "105275")
        self.assertEqual(resolution.monthly_workable_hours, 181)

    def test_historical_configuration_can_inherit_exact_id_from_exception(self) -> None:
        source = sheet_with_people(("105275", "Nombre leído del Excel", 181))
        config = BillingConfiguration(
            management_people=(ManagementPerson("Jose Antonio Ferrandez Mula", 50),),
            management_person_account_exceptions=(
                ManagementPersonAccountException(
                    "105275",
                    "Jose Antonio Ferrandez Mula",
                    "EXT-1",
                ),
            ),
        )

        resolution = resolve_management_people_in_sheet(config, source)[0]

        self.assertEqual(resolution.configured_employee_id, "105275")
        self.assertEqual(resolution.matching_method, ManagementPersonMatchingMethod.EXACT_EMPLOYEE_ID)
        self.assertEqual(resolution.matched_person_name, "Nombre leído del Excel")

    def test_normalized_name_matches_double_and_surrounding_spaces(self) -> None:
        source = sheet_with_people(("2001", "Pedro Miguel Franco Sarabia", 181))
        manager = ManagementPerson("  Pedro  Miguel Franco   Sarabia ", 100)

        resolution = resolve_management_person_in_sheet(manager, source)

        self.assertEqual(resolution.matching_method, ManagementPersonMatchingMethod.NORMALIZED_NAME)
        self.assertEqual(resolution.matched_employee_id, "2001")

    def test_similar_but_different_name_is_not_matched(self) -> None:
        source = sheet_with_people(("3001", "Rafael Juan Guerra", 181))
        manager = ManagementPerson("Rafael Joan Guerra", 100)

        resolution = resolve_management_person_in_sheet(manager, source)

        self.assertEqual(resolution.matching_method, ManagementPersonMatchingMethod.NOT_FOUND)
        self.assertIsNone(resolution.matched_employee_id)
        self.assertIsNone(resolution.monthly_workable_hours)

    def test_ambiguous_normalized_name_is_not_matched(self) -> None:
        source = sheet_with_people(
            ("3001", "Rafael Juan Guerra", 181),
            ("3002", "Ráfael  Juan Guerra", 160),
        )
        manager = ManagementPerson("Rafael Juan Guerra", 100)

        resolution = resolve_management_person_in_sheet(manager, source)

        self.assertEqual(
            resolution.matching_method,
            ManagementPersonMatchingMethod.AMBIGUOUS_NORMALIZED_NAME,
        )
        self.assertIsNone(resolution.matched_employee_id)

    def test_all_present_managers_resolve_monthly_hours(self) -> None:
        records = (
            ("1001", "Daniel Alarcón Guerrero", 181),
            ("1002", "Jose Antonio Ferrandez Mula", 181),
            ("1003", "Pedro Miguel Franco Sarabia", 181),
            ("1004", "Rafael Juan Guerra", 181),
        )
        source = sheet_with_people(*records)
        config = BillingConfiguration()
        for _, name, _ in records:
            config = add_management_person(config, name, 100)

        targets = calculate_manager_targets(991.5, source, config)

        self.assertTrue(all(item.monthly_workable_hours == 181 for item in targets))
        self.assertTrue(all(item.employee_id.startswith("100") for item in targets))
        self.assertTrue(all(item.base_target_hours == 181 for item in targets))


class MonthlyManagerCapacityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contents = workbook_with_capacities()
        self.november = parse_hierarchical_worksheet(
            self.contents,
            "Horas servicio noviembre",
        )
        self.december = parse_hierarchical_worksheet(
            self.contents,
            "Horas servicio diciembre",
        )

    def test_person_total_hours_has_priority_and_is_not_counted_as_account_daily_hours(self) -> None:
        capacity = next(
            item for item in self.november.person_monthly_capacities
            if item.person_name == "Daniel Alarcón Guerrero"
        )
        account = next(item for item in self.november.assignments if item.account_code == "EXT-A")

        self.assertEqual(capacity.monthly_workable_hours, 181)
        self.assertEqual(capacity.capacity_source, CapacitySource.PERSON_TOTAL_HOURS)
        self.assertEqual(account.total_hours, 20)

    def test_daniel_capacity_is_181_at_one_hundred_percent(self) -> None:
        target = calculate_manager_targets(999, self.november, manager_configuration(100))[0]

        self.assertEqual(target.monthly_workable_hours, 181)
        self.assertEqual(target.base_target_hours, 181)

    def test_percentages_apply_to_monthly_workable_hours(self) -> None:
        half = calculate_manager_targets(999, self.november, manager_configuration(50))[0]
        quarter = calculate_manager_targets(10, self.november, manager_configuration(25))[0]

        self.assertEqual(half.base_target_hours, 90.5)
        self.assertEqual(quarter.base_target_hours, 45.25)

    def test_capacity_never_depends_on_total_management_hours(self) -> None:
        config = manager_configuration(100)
        first = calculate_manager_targets(100, self.november, config)[0]
        second = calculate_manager_targets(991.5, self.november, config)[0]

        self.assertEqual(first.base_target_hours, 181)
        self.assertEqual(second.base_target_hours, 181)

    def test_capacity_is_specific_to_each_month(self) -> None:
        config = manager_configuration(100)

        november = calculate_manager_targets(100, self.november, config)[0]
        december = calculate_manager_targets(100, self.december, config)[0]

        self.assertEqual(november.base_target_hours, 181)
        self.assertEqual(december.base_target_hours, 160)

    def test_daily_fallback_ignores_absence_codes(self) -> None:
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Mes"
        worksheet.append([None, None, 1, 2, 3, 4, 5])
        worksheet.append([1001, "Daniel Alarcón Guerrero", 9, "Vacaciones", 7, "F", "Enfermedad"])
        output = BytesIO()
        workbook.save(output)
        workbook.close()

        parsed = parse_hierarchical_worksheet(output.getvalue(), "Mes")
        capacity = parsed.person_monthly_capacities[0]

        self.assertEqual(capacity.monthly_workable_hours, 16)
        self.assertEqual(capacity.capacity_source, CapacitySource.DAILY_HOURS)

    def test_missing_capacity_generates_manager_incident(self) -> None:
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Mes"
        worksheet.append([None, None, 1, 2, "Horas"])
        worksheet.append([1001, "Daniel Alarcón Guerrero", "V", "F", None])
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        parsed = parse_hierarchical_worksheet(output.getvalue(), "Mes")

        result = calculate_management_allocation(
            parsed,
            (calculation("EXT-A", 5),),
            manager_configuration(100),
        )

        self.assertEqual(result.manager_summaries[0].status, AllocationStatus.CAPACITY_DATA_MISSING)
        self.assertEqual(result.allocation_status, AllocationStatus.CAPACITY_DATA_MISSING)
        self.assertEqual(result.assignments, ())
        self.assertEqual(result.total_assigned_hours, 0)
        self.assertEqual(result.allocation_difference, -5)
        self.assertTrue(any("MANAGER_MONTHLY_CAPACITY_NOT_FOUND" in item for item in result.incidents))

    def test_absent_manager_blocks_allocation_and_rate_optimization(self) -> None:
        config = add_management_person(BillingConfiguration(), "Gestor Ausente", 100)

        allocated = calculate_sheet_management(self.november, config)
        optimized = calculate_sheet_rate_optimization(allocated, self.november, config)

        self.assertEqual(
            allocated.allocation_result.allocation_status,
            AllocationStatus.CAPACITY_DATA_MISSING,
        )
        self.assertEqual(allocated.allocation_result.assignments, ())
        self.assertTrue(any(
            "MANAGER_NOT_FOUND_IN_ACTIVE_SHEET" in item
            for item in allocated.allocation_result.incidents
        ))
        self.assertIsNone(optimized.rate_optimization_result)

    def test_required_management_above_base_target_reports_exact_overflow(self) -> None:
        result = calculate_management_allocation(
            self.november,
            (calculation("EXT-A", 200),),
            manager_configuration(100),
        )

        self.assertEqual(result.allocation_status, AllocationStatus.ABOVE_TARGET_DUE_TO_OVERFLOW)
        self.assertEqual(result.total_base_target_hours, 181)
        self.assertEqual(result.overflow_hours, 19)
        self.assertEqual(result.total_assigned_hours, 200)
        self.assertEqual(
            result.manager_summaries[0].status,
            AllocationStatus.ABOVE_TARGET_DUE_TO_OVERFLOW,
        )

    def test_spare_capacity_is_not_forced_to_be_consumed(self) -> None:
        result = calculate_management_allocation(
            self.november,
            (calculation("EXT-A", 50),),
            manager_configuration(100),
        )

        self.assertEqual(result.allocation_status, AllocationStatus.CAPACITY_OK)
        self.assertEqual(result.total_assigned_hours, 50)
        self.assertEqual(result.overflow_hours, 0)
        self.assertEqual(result.manager_summaries[0].difference_vs_base_target, -131)

    def test_allocation_preserves_required_hours_for_every_account(self) -> None:
        calculations = (calculation("EXT-A", 100), calculation("EXT-B", 81.5))

        result = calculate_management_allocation(
            self.november,
            calculations,
            manager_configuration(100),
        )

        assigned = {}
        for item in result.assignments:
            assigned[item.account_code] = assigned.get(item.account_code, 0) + item.management_hours
        self.assertEqual(assigned, {"EXT-A": 100, "EXT-B": 81.5})
        self.assertEqual(result.allocation_difference, 0)

    def test_real_four_manager_example_distributes_358_hour_overflow_equally(self) -> None:
        managers = (
            ("Daniel Alarcón Guerrero", 100),
            ("Jose Antonio Ferrandez Mula", 50),
            ("Pedro Miguel Franco Sarabia", 100),
            ("Rafael Juan Guerra", 100),
        )
        people = tuple(Person(f"M-{index}", name) for index, (name, _) in enumerate(managers, 1))
        capacities = tuple(
            PersonMonthlyWorkCapacity(
                employee_id=person.employee_id,
                person_name=person.name,
                monthly_workable_hours=181,
                capacity_source=CapacitySource.PERSON_TOTAL_HOURS,
            )
            for person in people
        )
        source = replace(
            self.november,
            people=people,
            person_monthly_capacities=capacities,
        )
        config = BillingConfiguration()
        for name, percentage in managers:
            config = add_management_person(config, name, percentage)

        result = calculate_management_allocation(
            source,
            (calculation("EXT-A", 991.5),),
            config,
        )

        capacities_by_manager = {
            item.manager_name: item.base_target_hours
            for item in result.manager_summaries
        }
        self.assertEqual(capacities_by_manager["Daniel Alarcón Guerrero"], 181)
        self.assertEqual(capacities_by_manager["Jose Antonio Ferrandez Mula"], 90.5)
        self.assertEqual(capacities_by_manager["Pedro Miguel Franco Sarabia"], 181)
        self.assertEqual(capacities_by_manager["Rafael Juan Guerra"], 181)
        self.assertEqual(result.total_base_target_hours, 633.5)
        self.assertEqual(result.overflow_hours, 358)
        self.assertEqual(result.allocation_status, AllocationStatus.ABOVE_TARGET_DUE_TO_OVERFLOW)
        overflow_by_manager = {
            item.manager_name: item.overflow_hours_assigned
            for item in result.manager_summaries
        }
        self.assertEqual(overflow_by_manager, {
            "Daniel Alarcón Guerrero": 89.5,
            "Jose Antonio Ferrandez Mula": 89.5,
            "Pedro Miguel Franco Sarabia": 89.5,
            "Rafael Juan Guerra": 89.5,
        })
        self.assertEqual(sum(item.assigned_hours for item in result.manager_summaries), 991.5)

    def test_complete_account_within_tolerance_is_not_split(self) -> None:
        people = (Person("M-1", "Ana"), Person("M-2", "Bea"))
        capacities = tuple(
            PersonMonthlyWorkCapacity(
                employee_id=person.employee_id,
                person_name=person.name,
                monthly_workable_hours=100,
                capacity_source=CapacitySource.PERSON_TOTAL_HOURS,
            )
            for person in people
        )
        source = replace(
            self.november,
            people=(*self.november.people, *people),
            person_monthly_capacities=capacities,
        )
        config = add_management_person(BillingConfiguration(), "Ana", 100)
        config = add_management_person(config, "Bea", 100)

        result = calculate_management_allocation(
            source,
            (calculation("EXT-001", 91), calculation("EXT-002", 10)),
            config,
        )

        second_account = [item for item in result.assignments if item.account_code == "EXT-002"]
        self.assertEqual(len(second_account), 1)
        self.assertEqual(second_account[0].manager_name, "Ana")
        self.assertEqual(result.split_account_count, 0)


if __name__ == "__main__":
    unittest.main()
