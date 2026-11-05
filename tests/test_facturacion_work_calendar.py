from io import BytesIO
import unittest

from openpyxl import Workbook

from delivery_manager_toolbox.modules.facturacion.excel_reader import parse_hierarchical_worksheet
from delivery_manager_toolbox.modules.facturacion.models import Person
from delivery_manager_toolbox.modules.facturacion.work_calendar import (
    apply_absences_to_calendar,
    build_theoretical_work_calendar,
    calculate_manager_base_target,
    is_non_working_code,
)


class TheoreticalWorkCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.person = Person("1001", "Daniel Alarcón Guerrero")

    def test_november_2026_without_absences_has_181_hours(self) -> None:
        result = build_theoretical_work_calendar(
            self.person,
            "Horas servicio noviembre",
            2026,
            11,
        )

        self.assertEqual(result.theoretical_monthly_hours, 181)
        self.assertEqual(result.monthly_workable_hours, 181)
        self.assertEqual(result.absence_hours, 0)

    def test_monday_vacation_and_friday_compensated_subtract_16_hours(self) -> None:
        theoretical = build_theoretical_work_calendar(self.person, "Noviembre", 2026, 11)

        result = apply_absences_to_calendar(theoretical, {2: "V", 6: "Compensado"})

        self.assertEqual(result.absence_hours, 16)
        self.assertEqual(result.monthly_workable_hours, 165)
        self.assertEqual(result.daily_capacity[1].capacity_hours, 0)
        self.assertEqual(result.daily_capacity[5].capacity_hours, 0)

    def test_weekend_capacity_is_always_zero(self) -> None:
        result = build_theoretical_work_calendar(self.person, "Noviembre", 2026, 11)

        self.assertEqual(result.daily_capacity[0].weekday_name, "Domingo")
        self.assertEqual(result.daily_capacity[0].capacity_hours, 0)
        self.assertEqual(result.daily_capacity[6].weekday_name, "Sábado")
        self.assertEqual(result.daily_capacity[6].capacity_hours, 0)

    def test_capacity_percentage_is_applied_after_calendar(self) -> None:
        self.assertEqual(calculate_manager_base_target(181, 50), 90.5)
        self.assertEqual(calculate_manager_base_target(181, 25), 45.25)

    def test_non_working_codes_are_centralized(self) -> None:
        for value in ("V", "Vacaciones", "F", "Formación", "E", "Enfermedad", "Baja", "C", "Compensados", "Festivo", "Ausencia", "A"):
            with self.subTest(value=value):
                self.assertTrue(is_non_working_code(value))
        for value in (9, 7, "Trabajo", ""):
            with self.subTest(value=value):
                self.assertFalse(is_non_working_code(value))

    def test_auxiliary_rows_remove_training_sickness_and_compensated_days(self) -> None:
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Horas servicio noviembre"
        worksheet.append([None, None, 2, 3, 6])
        worksheet.append([1001, "Daniel Alarcón Guerrero", None, None, None])
        worksheet.append([None, "Ausencias", "Formación", "Enfermedad", "Compensado"])
        output = BytesIO()
        workbook.save(output)
        workbook.close()

        result = parse_hierarchical_worksheet(
            output.getvalue(),
            "Horas servicio noviembre",
            filename="2026-11-Grupos_propios_BBVA.xlsx",
        )

        capacity = result.person_monthly_capacities[0]
        self.assertEqual(capacity.absence_hours, 25)
        self.assertEqual(capacity.monthly_workable_hours, 156)

    def test_auxiliary_absence_row_with_numeric_hours_marks_the_day(self) -> None:
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Horas servicio noviembre"
        worksheet.append([None, None, 2, 3])
        worksheet.append([1001, "Daniel Alarcón Guerrero", None, None])
        worksheet.append([None, "V", 9, None])
        output = BytesIO()
        workbook.save(output)
        workbook.close()

        result = parse_hierarchical_worksheet(
            output.getvalue(),
            "Horas servicio noviembre",
            filename="2026-11-Grupos_propios_BBVA.xlsx",
        )

        capacity = result.person_monthly_capacities[0]
        self.assertEqual(capacity.absence_hours, 9)
        self.assertEqual(capacity.monthly_workable_hours, 172)

    def test_december_and_next_january_use_real_calendar_years(self) -> None:
        workbook = Workbook()
        for index, sheet_name in enumerate((
            "Horas servicio noviembre",
            "Horas servicio diciembre",
            "Horas servicio enero",
        )):
            worksheet = workbook.active if index == 0 else workbook.create_sheet()
            worksheet.title = sheet_name
            worksheet.append([None, None, 1])
            worksheet.append([1001, "Daniel Alarcón Guerrero", None])
        output = BytesIO()
        workbook.save(output)
        workbook.close()

        december = parse_hierarchical_worksheet(
            output.getvalue(),
            "Horas servicio diciembre",
            filename="2026-11-Grupos_propios_BBVA.xlsx",
        )
        january = parse_hierarchical_worksheet(
            output.getvalue(),
            "Horas servicio enero",
            filename="2026-11-Grupos_propios_BBVA.xlsx",
        )

        self.assertEqual((december.period_year, december.period_month), (2026, 12))
        self.assertEqual(december.person_monthly_capacities[0].monthly_workable_hours, 199)
        self.assertEqual((january.period_year, january.period_month), (2027, 1))
        self.assertEqual(january.person_monthly_capacities[0].monthly_workable_hours, 179)


if __name__ == "__main__":
    unittest.main()
