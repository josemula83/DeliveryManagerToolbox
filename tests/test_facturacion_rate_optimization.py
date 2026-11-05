import unittest

from delivery_manager_toolbox.modules.facturacion.configuration import (
    BillingConfiguration,
    add_management_rate_block,
)
from delivery_manager_toolbox.modules.facturacion.models import (
    AccountAssignment,
    AllocationStatus,
    DailyHours,
    ManagementAllocationResult,
    ManagementAssignment,
    ManagerAllocationSummary,
    Person,
    RateIncidentCode,
    RateSource,
    SheetAnalysis,
)
from delivery_manager_toolbox.modules.facturacion.rate_optimization import (
    optimize_management_rates,
)


def analysis(sheet_name: str, account_rates: dict[str, tuple[float, ...]]) -> SheetAnalysis:
    person = Person("P-1", "Persona Normal")
    assignments = tuple(
        AccountAssignment(
            person=person,
            account_code=account_code,
            service_name=f"Servicio {account_code}",
            rate=rate,
            original_description=f"Servicio {account_code} > {account_code} - Tarifa {rate}",
            account_type=None,
            daily_hours=(DailyHours(1, 1.0),),
        )
        for account_code, rates in account_rates.items()
        for rate in rates
    )
    return SheetAnalysis(sheet_name, (person,), assignments, (), (), len(assignments))


def allocation(
    sheet_name: str,
    *records: tuple[str, str, str, float],
    outside_tolerance: bool = False,
) -> ManagementAllocationResult:
    assignments = tuple(
        ManagementAssignment(
            sheet_name=sheet_name,
            employee_id=employee_id,
            manager_name=manager_name,
            account_code=account_code,
            service_name=f"Servicio {account_code}",
            management_hours=hours,
        )
        for employee_id, manager_name, account_code, hours in records
    )
    summaries = tuple(
        ManagerAllocationSummary(
            employee_id=employee_id,
            manager_name=manager_name,
            capacity_percentage=100,
            target_hours=sum(item.management_hours for item in assignments if item.employee_id == employee_id),
            assigned_hours=sum(item.management_hours for item in assignments if item.employee_id == employee_id),
            difference_hours=0,
            difference_percentage=0,
            accounts_assigned=len({item.account_code for item in assignments if item.employee_id == employee_id}),
            status=(
                AllocationStatus.OUTSIDE_TOLERANCE
                if outside_tolerance
                else AllocationStatus.OK
            ),
        )
        for employee_id, manager_name in dict.fromkeys(
            (item.employee_id, item.manager_name) for item in assignments
        )
    )
    total = sum(item.management_hours for item in assignments)
    return ManagementAllocationResult(
        sheet_name=sheet_name,
        assignments=assignments,
        manager_summaries=summaries,
        total_management_hours=total,
        total_assigned_hours=total,
        allocation_difference=0,
        allocation_status=(
            AllocationStatus.OUTSIDE_TOLERANCE
            if outside_tolerance
            else AllocationStatus.OK
        ),
        split_account_count=0,
    )


class RateOptimizationTests(unittest.TestCase):
    def test_configured_block_rate_is_always_used(self) -> None:
        source = analysis("Noviembre", {"EXT-1": (17, 26)})
        distributed = allocation("Noviembre", ("M-1", "Ana", "EXT-1", 5))
        config = add_management_rate_block(BillingConfiguration(), "Bloque", ("EXT-1",), 18)

        result = optimize_management_rates(source, distributed, config)

        self.assertEqual(result.assignments[0].management_rate, 18)
        self.assertEqual(result.assignments[0].rate_source, RateSource.CONFIGURED_BLOCK)

    def test_highest_detected_account_rate_is_selected(self) -> None:
        source = analysis("Noviembre", {"EXT-1": (17, 26)})
        distributed = allocation("Noviembre", ("M-1", "Ana", "EXT-1", 5))

        result = optimize_management_rates(source, distributed, BillingConfiguration())

        self.assertEqual(result.assignments[0].management_rate, 26)
        self.assertEqual(result.assignments[0].rate_source, RateSource.OPTIMIZED_RATE)

    def test_same_account_in_different_months_does_not_share_rates(self) -> None:
        november = optimize_management_rates(
            analysis("Noviembre", {"EXT-1": (17, 26)}),
            allocation("Noviembre", ("M-1", "Ana", "EXT-1", 5)),
            BillingConfiguration(),
        )
        december = optimize_management_rates(
            analysis("Diciembre", {"EXT-1": (19,)}),
            allocation("Diciembre", ("M-1", "Ana", "EXT-1", 6)),
            BillingConfiguration(),
        )

        self.assertEqual(november.assignments[0].management_rate, 26)
        self.assertEqual(december.assignments[0].management_rate, 19)

    def test_same_manager_account_is_consolidated_to_one_rate(self) -> None:
        source = analysis("Noviembre", {"EXT-1": (17, 26)})
        distributed = allocation(
            "Noviembre",
            ("M-1", "Ana", "EXT-1", 2),
            ("M-1", "Ana", "EXT-1", 3),
        )

        result = optimize_management_rates(source, distributed, BillingConfiguration())

        self.assertEqual(len(result.assignments), 1)
        self.assertEqual(result.assignments[0].management_hours, 5)
        self.assertEqual(result.assignments[0].management_rate, 26)
        self.assertEqual(result.consolidated_record_count, 1)
        self.assertFalse(any(item.code == RateIncidentCode.MULTIPLE_RATE_EXCEPTION for item in result.incidents))

    def test_optimization_does_not_change_hours_by_account_or_month(self) -> None:
        source = analysis("Noviembre", {"EXT-1": (17,), "EXT-2": (22,)})
        distributed = allocation(
            "Noviembre",
            ("M-1", "Ana", "EXT-1", 2.5),
            ("M-2", "Bea", "EXT-1", 1.5),
            ("M-2", "Bea", "EXT-2", 3),
        )

        result = optimize_management_rates(source, distributed, BillingConfiguration())

        optimized_by_account = {
            item.account_code: item.management_hours for item in result.account_summaries
        }
        self.assertEqual(optimized_by_account, {"EXT-1": 4, "EXT-2": 3})
        self.assertEqual(result.billing_summary.total_management_hours, 7)

    def test_half_hour_steps_and_manager_totals_are_preserved(self) -> None:
        source = analysis("Noviembre", {"EXT-1": (17,), "EXT-2": (22,)})
        distributed = allocation(
            "Noviembre",
            ("M-1", "Ana", "EXT-1", 2.5),
            ("M-1", "Ana", "EXT-2", 1.5),
            ("M-2", "Bea", "EXT-2", 3),
        )

        result = optimize_management_rates(source, distributed, BillingConfiguration())

        self.assertTrue(all((item.management_hours * 2).is_integer() for item in result.assignments))
        self.assertEqual(
            {item.manager_name: item.management_hours for item in result.manager_summaries},
            {"Ana": 4, "Bea": 3},
        )

    def test_lower_rate_is_not_concentrated_when_higher_rate_is_valid(self) -> None:
        source = analysis("Noviembre", {"EXT-1": (17, 26)})
        distributed = allocation(
            "Noviembre",
            ("M-1", "Ana", "EXT-1", 4),
            ("M-2", "Bea", "EXT-1", 6),
        )

        result = optimize_management_rates(source, distributed, BillingConfiguration())

        self.assertEqual({item.management_rate for item in result.assignments}, {26})

    def test_amount_equals_hours_times_rate(self) -> None:
        source = analysis("Noviembre", {"EXT-1": (26,)})
        distributed = allocation("Noviembre", ("M-1", "Ana", "EXT-1", 4.5))

        result = optimize_management_rates(source, distributed, BillingConfiguration())

        self.assertEqual(result.assignments[0].amount, 117)
        self.assertEqual(result.billing_summary.total_management_amount, 117)
        self.assertEqual(result.billing_summary.weighted_average_rate, 26)

    def test_block_has_priority_over_higher_detected_rate(self) -> None:
        source = analysis("Noviembre", {"EXT-1": (17, 30)})
        distributed = allocation("Noviembre", ("M-1", "Ana", "EXT-1", 4))
        config = add_management_rate_block(BillingConfiguration(), "Bloque", ("EXT-1",), 18)

        result = optimize_management_rates(source, distributed, config)

        self.assertEqual(result.assignments[0].management_rate, 18)
        self.assertEqual(result.billing_summary.configured_block_accounts, 1)
        self.assertEqual(result.billing_summary.automatically_optimized_accounts, 0)

    def test_missing_rate_generates_clear_incident(self) -> None:
        source = analysis("Noviembre", {"EXT-1": ()})
        distributed = allocation("Noviembre", ("M-1", "Ana", "EXT-1", 4))

        result = optimize_management_rates(source, distributed, BillingConfiguration())

        self.assertIsNone(result.assignments[0].management_rate)
        self.assertIsNone(result.assignments[0].amount)
        self.assertEqual(result.assignments[0].rate_status, RateIncidentCode.NO_RATE_FOUND.value)
        self.assertTrue(any(item.code == RateIncidentCode.NO_RATE_FOUND for item in result.incidents))

    def test_outside_tolerance_is_exposed_without_changing_hours(self) -> None:
        source = analysis("Noviembre", {"EXT-1": (26,)})
        distributed = allocation(
            "Noviembre",
            ("M-1", "Ana", "EXT-1", 0.5),
            outside_tolerance=True,
        )

        result = optimize_management_rates(source, distributed, BillingConfiguration())

        self.assertEqual(result.billing_summary.total_management_hours, 0.5)
        self.assertTrue(any(
            item.code == RateIncidentCode.OUTSIDE_TOLERANCE_AFTER_OPTIMIZATION
            for item in result.incidents
        ))


if __name__ == "__main__":
    unittest.main()
