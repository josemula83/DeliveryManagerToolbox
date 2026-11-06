from dataclasses import replace
import unittest

from delivery_manager_toolbox.modules.facturacion.allocation import (
    calculate_management_allocation,
    calculate_manager_targets,
)
from delivery_manager_toolbox.modules.facturacion.configuration import (
    BillingConfiguration,
    add_management_person,
)
from delivery_manager_toolbox.modules.facturacion.models import (
    AccountAssignment,
    AccountManagementCalculation,
    CalculationType,
    CapacitySource,
    DailyHours,
    Person,
    PersonMonthlyWorkCapacity,
    SheetAnalysis,
)


def service(person: Person, account: str, hours: float) -> AccountAssignment:
    return AccountAssignment(
        person=person,
        account_code=account,
        service_name=account,
        rate=26,
        original_description=f"{account} > {account} - Tarifa 26 EUR",
        account_type=None,
        daily_hours=(DailyHours(1, hours),),
    )


def calculation(account: str, hours: float) -> AccountManagementCalculation:
    return AccountManagementCalculation(
        account_code=account,
        service_name=account,
        base_hours=hours * 20,
        raw_management_hours=hours,
        management_hours=hours,
        calculation_type=CalculationType.PERCENTAGE,
        percentage_applied=5,
    )


def configuration(*records: tuple[str, float]) -> BillingConfiguration:
    result = BillingConfiguration()
    for name, percentage in records:
        result = add_management_person(result, name, percentage)
    return result


def with_calendar_capacities(
    source: SheetAnalysis,
    capacities_by_name: dict[str, float],
) -> SheetAnalysis:
    capacities = tuple(
        PersonMonthlyWorkCapacity(
            employee_id=person.employee_id,
            person_name=person.name,
            monthly_workable_hours=capacities_by_name[person.name],
            capacity_source=CapacitySource.THEORETICAL_CALENDAR,
        )
        for person in source.people
        if person.name in capacities_by_name
    )
    return replace(source, person_monthly_capacities=capacities)


class LegacyAllocationRegressionTests(unittest.TestCase):
    def test_month_base_uses_calendar_and_ignores_service_hours(self) -> None:
        ana = Person("M-1", "Ana")
        bea = Person("M-2", "Bea")
        normal = Person("N-1", "Persona Normal")
        source = SheetAnalysis(
            "Mes",
            (ana, bea, normal),
            (
                service(ana, "EXT-1", 100),
                service(normal, "EXT-2", 200),
            ),
            (),
            (),
            2,
        )

        source = with_calendar_capacities(source, {"Ana": 181, "Bea": 138})
        targets = calculate_manager_targets(
            999,
            source,
            configuration(("Ana", 100), ("Bea", 100)),
        )

        self.assertEqual([item.monthly_workable_hours for item in targets], [181, 138])
        self.assertEqual(
            [item.capacity_source for item in targets],
            [
                CapacitySource.THEORETICAL_CALENDAR,
                CapacitySource.THEORETICAL_CALENDAR,
            ],
        )

    def test_four_manager_case_matches_legacy_largest_remainders(self) -> None:
        managers = (
            Person("M-1", "Daniel"),
            Person("M-2", "Jose"),
            Person("M-3", "Pedro"),
            Person("M-4", "Rafael"),
        )
        source = SheetAnalysis(
            "Noviembre",
            managers,
            tuple(
                service(person, f"EXT-CAP-{index}", 181)
                for index, person in enumerate(managers, 1)
            ),
            (),
            (),
            4,
        )
        source = with_calendar_capacities(
            source,
            {person.name: 181 for person in managers},
        )
        config = configuration(
            ("Daniel", 100),
            ("Jose", 50),
            ("Pedro", 100),
            ("Rafael", 100),
        )

        result = calculate_management_allocation(
            source,
            (calculation("EXT-1", 991.5),),
            config,
        )

        self.assertEqual(
            [item.base_target_hours for item in result.manager_summaries],
            [181, 90.5, 181, 181],
        )
        self.assertEqual(
            [item.assigned_hours for item in result.manager_summaries],
            [283.5, 141.5, 283.5, 283],
        )
        self.assertEqual(result.total_assigned_hours, 991.5)
        self.assertEqual(result.allocation_difference, 0)
        self.assertEqual(result.split_account_count, 1)

    def test_account_is_split_exactly_at_legacy_quota_boundary(self) -> None:
        ana = Person("M-1", "Ana")
        bea = Person("M-2", "Bea")
        source = SheetAnalysis(
            "Mes",
            (ana, bea),
            (service(ana, "EXT-CAP-1", 100), service(bea, "EXT-CAP-2", 100)),
            (),
            (),
            2,
        )
        source = with_calendar_capacities(source, {"Ana": 100, "Bea": 100})

        result = calculate_management_allocation(
            source,
            (calculation("EXT-1", 91), calculation("EXT-2", 10)),
            configuration(("Ana", 100), ("Bea", 100)),
        )

        first = [item.management_hours for item in result.assignments if item.account_code == "EXT-1"]
        second = [item.management_hours for item in result.assignments if item.account_code == "EXT-2"]
        self.assertEqual(first, [50.5, 40.5])
        self.assertEqual(second, [10])

    def test_accounts_use_numeric_natural_order_before_distribution(self) -> None:
        ana = Person("M-1", "Ana")
        bea = Person("M-2", "Bea")
        source = SheetAnalysis(
            "Mes",
            (ana, bea),
            (service(ana, "EXT-CAP-1", 100), service(bea, "EXT-CAP-2", 100)),
            (),
            (),
            2,
        )
        source = with_calendar_capacities(source, {"Ana": 100, "Bea": 100})

        result = calculate_management_allocation(
            source,
            (calculation("EXT-10", 5), calculation("EXT-2", 5)),
            configuration(("Ana", 100), ("Bea", 100)),
        )

        self.assertEqual(
            [(item.manager_name, item.account_code) for item in result.assignments],
            [("Ana", "EXT-2"), ("Bea", "EXT-10")],
        )


if __name__ == "__main__":
    unittest.main()
