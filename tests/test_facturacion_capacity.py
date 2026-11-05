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
    add_management_person,
)
from delivery_manager_toolbox.modules.facturacion.excel_reader import parse_hierarchical_worksheet
from delivery_manager_toolbox.modules.facturacion.models import (
    AccountManagementCalculation,
    AllocationStatus,
    CalculationType,
    CapacitySource,
    Person,
    PersonMonthlyWorkCapacity,
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
        self.assertEqual(target.target_hours, 181)

    def test_percentages_apply_to_monthly_workable_hours(self) -> None:
        half = calculate_manager_targets(999, self.november, manager_configuration(50))[0]
        quarter = calculate_manager_targets(10, self.november, manager_configuration(25))[0]

        self.assertEqual(half.target_hours, 90.5)
        self.assertEqual(quarter.target_hours, 45.25)

    def test_capacity_never_depends_on_total_management_hours(self) -> None:
        config = manager_configuration(100)
        first = calculate_manager_targets(100, self.november, config)[0]
        second = calculate_manager_targets(991.5, self.november, config)[0]

        self.assertEqual(first.target_hours, 181)
        self.assertEqual(second.target_hours, 181)

    def test_capacity_is_specific_to_each_month(self) -> None:
        config = manager_configuration(100)

        november = calculate_manager_targets(100, self.november, config)[0]
        december = calculate_manager_targets(100, self.december, config)[0]

        self.assertEqual(november.target_hours, 181)
        self.assertEqual(december.target_hours, 160)

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

        self.assertEqual(result.manager_summaries[0].status, AllocationStatus.CAPACITY_NOT_FOUND)
        self.assertTrue(any("MANAGER_MONTHLY_CAPACITY_NOT_FOUND" in item for item in result.incidents))

    def test_required_management_above_capacity_reports_exact_deficit(self) -> None:
        result = calculate_management_allocation(
            self.november,
            (calculation("EXT-A", 200),),
            manager_configuration(100),
        )

        self.assertEqual(result.allocation_status, AllocationStatus.INSUFFICIENT_CAPACITY)
        self.assertEqual(result.total_management_capacity, 181)
        self.assertEqual(result.capacity_deficit_hours, 19)
        self.assertEqual(result.total_assigned_hours, 200)
        self.assertEqual(result.manager_summaries[0].status, AllocationStatus.CAPACITY_EXCEEDED)

    def test_spare_capacity_is_not_forced_to_be_consumed(self) -> None:
        result = calculate_management_allocation(
            self.november,
            (calculation("EXT-A", 50),),
            manager_configuration(100),
        )

        self.assertEqual(result.allocation_status, AllocationStatus.CAPACITY_OK)
        self.assertEqual(result.total_assigned_hours, 50)
        self.assertEqual(result.capacity_surplus_hours, 131)
        self.assertEqual(result.manager_summaries[0].capacity_remaining_hours, 131)

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

    def test_real_four_manager_example_reports_358_hour_deficit(self) -> None:
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
            people=(*self.november.people, *people),
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
            item.manager_name: item.management_capacity_hours
            for item in result.manager_summaries
        }
        self.assertEqual(capacities_by_manager["Daniel Alarcón Guerrero"], 181)
        self.assertEqual(capacities_by_manager["Jose Antonio Ferrandez Mula"], 90.5)
        self.assertEqual(capacities_by_manager["Pedro Miguel Franco Sarabia"], 181)
        self.assertEqual(capacities_by_manager["Rafael Juan Guerra"], 181)
        self.assertEqual(result.total_management_capacity, 633.5)
        self.assertEqual(result.capacity_deficit_hours, 358)
        self.assertEqual(result.allocation_status, AllocationStatus.INSUFFICIENT_CAPACITY)


if __name__ == "__main__":
    unittest.main()
