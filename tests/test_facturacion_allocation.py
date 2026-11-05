import unittest

from delivery_manager_toolbox.modules.facturacion.allocation import (
    allocate_accounts_to_managers,
    calculate_management_allocation,
    calculate_manager_targets,
    rebalance_allocations,
    validate_allocation,
)
from delivery_manager_toolbox.modules.facturacion.business import calculate_sheet_management
from delivery_manager_toolbox.modules.facturacion.configuration import (
    BillingConfiguration,
    add_management_person,
)
from delivery_manager_toolbox.modules.facturacion.models import (
    AccountAssignment,
    AccountManagementCalculation,
    AllocationStatus,
    CalculationType,
    DailyHours,
    Person,
    SheetAnalysis,
)


def calculation(
    account_code: str,
    management_hours: float,
    *,
    calculation_type: CalculationType = CalculationType.PERCENTAGE,
) -> AccountManagementCalculation:
    return AccountManagementCalculation(
        account_code=account_code,
        service_name=f"Servicio {account_code}",
        base_hours=management_hours * 20,
        raw_management_hours=management_hours,
        management_hours=management_hours,
        calculation_type=calculation_type,
        percentage_applied=5.0 if calculation_type == CalculationType.PERCENTAGE else 0.0,
    )


def configuration(*managers: tuple[str, float]) -> BillingConfiguration:
    result = BillingConfiguration()
    for name, capacity in managers:
        result = add_management_person(result, name, capacity)
    return result


def analysis(sheet_name: str, *manager_names: str, base_hours: float = 100) -> SheetAnalysis:
    normal_person = Person("N-1", "Persona Normal")
    managers = tuple(Person(f"M-{index}", name) for index, name in enumerate(manager_names, 1))
    assignment = AccountAssignment(
        person=normal_person,
        account_code="EXT-1",
        service_name="Servicio Uno",
        rate=17.0,
        original_description="Servicio Uno > EXT-1",
        account_type=None,
        daily_hours=(DailyHours(1, base_hours),),
    )
    return SheetAnalysis(sheet_name, (normal_person, *managers), (assignment,), (), (), 1)


class ManagerAllocationTests(unittest.TestCase):
    def test_one_manager_receives_all_hours(self) -> None:
        source = analysis("Noviembre", "Ana")
        result = calculate_management_allocation(
            source,
            (calculation("EXT-1", 10),),
            configuration(("Ana", 100)),
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.assignments[0].management_hours, 10)
        self.assertEqual(result.manager_summaries[0].assigned_hours, 10)

    def test_two_equal_managers_split_fifty_fifty(self) -> None:
        source = analysis("Noviembre", "Ana", "Bea")
        result = calculate_management_allocation(
            source,
            (calculation("EXT-1", 20),),
            configuration(("Ana", 100), ("Bea", 100)),
        )

        self.assertEqual([item.target_hours for item in result.manager_summaries], [10, 10])
        self.assertEqual([item.assigned_hours for item in result.manager_summaries], [10, 10])

    def test_capacities_fifty_and_one_hundred_split_one_third_two_thirds(self) -> None:
        source = analysis("Noviembre", "Ana", "Bea")
        result = calculate_management_allocation(
            source,
            (calculation("EXT-1", 30),),
            configuration(("Ana", 50), ("Bea", 100)),
        )

        self.assertEqual([item.target_hours for item in result.manager_summaries], [10, 20])
        self.assertEqual([item.assigned_hours for item in result.manager_summaries], [10, 20])

    def test_four_manager_targets_follow_capacity_weights(self) -> None:
        source = analysis("Noviembre", "Daniel", "Jose", "Pedro", "Rafael")
        targets = calculate_manager_targets(
            100,
            source,
            configuration(("Jose", 50), ("Pedro", 100), ("Rafael", 100), ("Daniel", 100)),
        )

        by_name = {item.manager_name: item.target_hours for item in targets}
        self.assertAlmostEqual(by_name["Jose"], 14.2857142857)
        self.assertAlmostEqual(by_name["Pedro"], 28.5714285714)
        self.assertAlmostEqual(by_name["Rafael"], 28.5714285714)
        self.assertAlmostEqual(by_name["Daniel"], 28.5714285714)

    def test_total_is_exact_and_every_assignment_uses_half_hour_steps(self) -> None:
        source = analysis("Noviembre", "Ana", "Bea", "Carla")
        calculations = (calculation("EXT-2", 4.5), calculation("EXT-10", 7.5))
        result = calculate_management_allocation(
            source,
            calculations,
            configuration(("Ana", 50), ("Bea", 100), ("Carla", 100)),
        )

        self.assertEqual(result.total_management_hours, result.total_assigned_hours)
        self.assertEqual(result.allocation_difference, 0)
        self.assertTrue(all((item.management_hours * 2).is_integer() for item in result.assignments))
        self.assertEqual(validate_allocation(calculations, result.assignments), 0)

    def test_account_stays_whole_when_it_matches_a_manager_quota(self) -> None:
        source = analysis("Noviembre", "Ana", "Bea")
        result = calculate_management_allocation(
            source,
            (calculation("EXT-1", 5), calculation("EXT-2", 5)),
            configuration(("Ana", 100), ("Bea", 100)),
        )

        self.assertEqual(result.split_account_count, 0)
        self.assertEqual(len(result.assignments), 2)

    def test_account_is_split_when_needed_to_reach_targets(self) -> None:
        source = analysis("Noviembre", "Ana", "Bea")
        result = calculate_management_allocation(
            source,
            (calculation("EXT-1", 10),),
            configuration(("Ana", 100), ("Bea", 100)),
        )

        self.assertEqual(result.split_account_count, 1)
        self.assertEqual([item.management_hours for item in result.assignments], [5, 5])

    def test_zero_management_account_does_not_generate_assignments(self) -> None:
        source = analysis("Noviembre", "Ana")
        result = calculate_management_allocation(
            source,
            (calculation("EXT-0", 0), calculation("EXT-1", 2)),
            configuration(("Ana", 100)),
        )

        self.assertEqual({item.account_code for item in result.assignments}, {"EXT-1"})

    def test_fixed_management_is_allocated_exactly(self) -> None:
        source = analysis("Noviembre", "Ana", "Bea")
        fixed = calculation("EXT-FIXED", 7.5, calculation_type=CalculationType.FIXED)
        result = calculate_management_allocation(
            source,
            (fixed,),
            configuration(("Ana", 100), ("Bea", 100)),
        )

        self.assertEqual(sum(item.management_hours for item in result.assignments), 7.5)
        self.assertEqual(result.allocation_difference, 0)

    def test_allocation_is_deterministic(self) -> None:
        source = analysis("Noviembre", "Zoé", "Ana", "Bea")
        calculations = (
            calculation("EXT-10", 4.5),
            calculation("EXT-2", 3),
            calculation("EXT-1", 5.5),
        )
        config = configuration(("Zoé", 100), ("Ana", 50), ("Bea", 100))

        first = calculate_management_allocation(source, calculations, config)
        second = calculate_management_allocation(source, calculations, config)

        self.assertEqual(first, second)

    def test_months_have_independent_allocation_results(self) -> None:
        config = configuration(("Ana", 100), ("Bea", 100))
        november = calculate_sheet_management(
            analysis("Noviembre", "Ana", "Bea", base_hours=100),
            config,
        )
        december = calculate_sheet_management(
            analysis("Diciembre", "Ana", "Bea", base_hours=120),
            config,
        )

        self.assertEqual(november.allocation_result.total_management_hours, 5)
        self.assertEqual(december.allocation_result.total_management_hours, 6)
        self.assertTrue(all(item.sheet_name == "Noviembre" for item in november.allocation_result.assignments))
        self.assertTrue(all(item.sheet_name == "Diciembre" for item in december.allocation_result.assignments))

    def test_impossible_tolerance_is_reported_but_total_still_matches(self) -> None:
        source = analysis("Noviembre", "Ana", "Bea")
        result = calculate_management_allocation(
            source,
            (calculation("EXT-1", 0.5),),
            configuration(("Ana", 100), ("Bea", 100)),
        )

        self.assertEqual(result.allocation_status, AllocationStatus.OUTSIDE_TOLERANCE)
        self.assertEqual(result.total_assigned_hours, 0.5)
        self.assertEqual(result.allocation_difference, 0)
        self.assertTrue(all(item.status == AllocationStatus.OUTSIDE_TOLERANCE for item in result.manager_summaries))

    def test_rebalance_never_increases_total_deviation(self) -> None:
        source = analysis("Noviembre", "Ana", "Bea", "Carla")
        calculations = (calculation("EXT-1", 2.5), calculation("EXT-2", 7.5))
        config = configuration(("Ana", 50), ("Bea", 100), ("Carla", 100))
        targets = calculate_manager_targets(10, source, config)
        initial = allocate_accounts_to_managers("Noviembre", calculations, targets)

        rebalanced = rebalance_allocations(initial, targets)

        target_by_name = {item.manager_name: item.target_hours for item in targets}
        self.assertLessEqual(
            self._deviation(rebalanced, target_by_name),
            self._deviation(initial, target_by_name),
        )
        self.assertEqual(validate_allocation(calculations, rebalanced), 0)

    @staticmethod
    def _deviation(assignments, targets) -> float:
        assigned = {name: 0.0 for name in targets}
        for item in assignments:
            assigned[item.manager_name] += item.management_hours
        return sum(abs(assigned[name] - target) for name, target in targets.items())


if __name__ == "__main__":
    unittest.main()
