from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO
import unittest

from openpyxl import Workbook, load_workbook

from delivery_manager_toolbox.modules.people_360.export import EXPORT_HEADERS, export_tracking
from delivery_manager_toolbox.modules.people_360.filters import filter_employees
from delivery_manager_toolbox.modules.people_360.models import Priority
from delivery_manager_toolbox.modules.people_360.repository import ExcelPeople360Repository
from delivery_manager_toolbox.modules.people_360.service import (
    build_dashboard,
    calculate_supervisor_priority,
    is_active_employee,
    is_bitacora_overdue,
)


REFERENCE = datetime(2026, 5, 15)


def make_workbook() -> bytes:
    workbook = Workbook()
    employees = workbook.active
    employees.title = "Empleados"
    employees.append(["numeroEmpleado", "nombre", "email", "tecnologia", "tecnologiaCliente", "ubicacion", "jobRole", "area", "cliente", "servicio", "equipo", "supervisor_n1", "supervisor_n2", "estado", "fecha_ultima_modificacion"])
    employees.append(["E01", "Ana Ejemplo", "ana@example.invalid", "Python", "", "Madrid", "Dev", "A", "Cliente", "Servicio", "Equipo", "Supervisora Uno", "", "ACTIVO", "2026-05-01"])
    employees.append(["E02", "Bruno Ejemplo", "bruno@example.invalid", "Java", "", "Sevilla", "Dev", "A", "Cliente", "Servicio", "Equipo", "Supervisora Uno", "", "ACTIVO", "2026-05-01"])
    employees.append(["E03", "Carla Ejemplo", "carla@example.invalid", "Data", "", "Bilbao", "Lead", "B", "Cliente", "Servicio", "Equipo", "Supervisora Dos", "", "ACTIVO", "2026-05-01"])
    employees.append(["E03", "Carla Ejemplo", "carla@example.invalid", "Data", "", "Bilbao", "Lead", "B", "Cliente", "Servicio", "Equipo", "Supervisora Dos", "", "ACTIVO", "2026-05-02"])
    employees.append(["E04", "Diego Ejemplo", "diego@example.invalid", "", "", "", "", "", "", "", "", "", "", "ACTIVO", "bad-date"])
    employees.append(["E05", "Elena Inactiva", "elena@example.invalid", "", "", "", "", "", "", "", "", "Supervisora Uno", "", "INACTIVO", "2026-05-03"])
    employees.append(["E06", "Fabio Baja", "fabio@example.invalid", "", "", "", "", "", "", "", "", "Supervisora Tres", "", "BAJA", "2026-05-03"])

    journals = workbook.create_sheet("Bitacoras")
    journals.append(["numeroEmpleado", "nombre", "tipo", "fecha", "bitacora_empleado", "bitacora_supervisor", "objetivos", "desempeno", "satisfaccion", "compromiso"])
    journals.append(["E01", "Ana Ejemplo", "empleado", "2026-05-01", "Seguimiento", None, None, None, None, None])
    journals.append(["E01", "Ana Ejemplo", "supervisor", "2026-03-15", None, "", None, None, None, None])
    journals.append(["E02", "Bruno Ejemplo", "empleado", "2026-05-02", "Seguimiento", None, None, None, None, None])
    journals.append(["E02", "Bruno Ejemplo", "supervisor", "2026-01-01", None, "Seguimiento", None, None, None, None])
    journals.append(["E03", "Carla Ejemplo", "empleado", "fecha incorrecta", "Texto", None, None, None, None, None])
    journals.append(["E03", "Carla Ejemplo", "supervisor", "2026-05-10", None, "Seguimiento", None, None, None, None])
    journals.append(["E05", "Elena Inactiva", "empleado", "2026-05-10", "Seguimiento", None, None, None, None, None])
    journals.append(["E05", "Elena Inactiva", "supervisor", "2026-05-10", None, "Seguimiento", None, None, None, None])
    journals.append(["E06", "Fabio Baja", "supervisor", "2026-05-10", None, "Seguimiento", None, None, None, None])

    meetings = workbook.create_sheet("Historico reuniones")
    meetings.append(["numeroEmpleado", "nombre", "fechaSolicitud", "estado", "fechaGestion", "gestionadaPor"])
    meetings.append(["E01", "Ana Ejemplo", "2026-05-03", "GESTIONADA", "2026-05-04", "Responsable"])
    meetings.append(["E02", "Bruno Ejemplo", "2026-05-05", "PENDIENTE", "", ""])
    meetings.append(["E03", "Carla Ejemplo", "fecha incorrecta", "PENDIENTE", "", ""])
    meetings.append(["E05", "Elena Inactiva", "2026-05-08", "PENDIENTE", "", ""])
    meetings.append(["E06", "Fabio Baja", "2026-05-08", "PENDIENTE", "", ""])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class PriorityTests(unittest.TestCase):
    def test_only_explicit_active_status_is_eligible(self) -> None:
        self.assertTrue(is_active_employee(" ACTIVO "))
        self.assertTrue(is_active_employee("activo"))
        self.assertFalse(is_active_employee("INACTIVO"))
        self.assertFalse(is_active_employee("BAJA"))
        self.assertFalse(is_active_employee(None))

    def test_exact_priority_boundaries_and_null(self) -> None:
        self.assertEqual(calculate_supervisor_priority(80.01), Priority.OK)
        self.assertEqual(calculate_supervisor_priority(80), Priority.MEDIUM)
        self.assertEqual(calculate_supervisor_priority(60), Priority.MEDIUM)
        self.assertEqual(calculate_supervisor_priority(59.99), Priority.CRITICAL)
        self.assertEqual(calculate_supervisor_priority(None), Priority.NO_DATA)

    def test_overdue_exact_days_and_missing_date(self) -> None:
        self.assertFalse(is_bitacora_overdue(REFERENCE - timedelta(days=59), REFERENCE))
        self.assertFalse(is_bitacora_overdue(REFERENCE - timedelta(days=60), REFERENCE))
        self.assertTrue(is_bitacora_overdue(REFERENCE - timedelta(days=61), REFERENCE))
        self.assertFalse(is_bitacora_overdue(None, REFERENCE))


class People360DataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = ExcelPeople360Repository(BytesIO(make_workbook()), "synthetic.xlsx").load()
        self.dashboard = build_dashboard(self.dataset, REFERENCE)

    def test_repository_tolerates_duplicates_invalid_dates_and_missing_supervisor(self) -> None:
        self.assertEqual(len(self.dataset.employees), 6)
        self.assertGreaterEqual(len(self.dataset.issues), 3)
        self.assertEqual(sum(e.supervisor_n1 is None for e in self.dataset.employees), 1)

    def test_metrics_use_one_normalized_model(self) -> None:
        metrics = self.dashboard.metrics
        self.assertEqual(metrics.total_employees, 4)
        self.assertEqual(metrics.total_supervisors, 2)
        self.assertEqual(metrics.average_employee_coverage, 50.0)
        self.assertEqual(metrics.average_supervisor_coverage, 75.0)
        self.assertEqual(metrics.critical_supervisors, 1)
        self.assertEqual(metrics.medium_supervisors, 0)
        self.assertEqual(metrics.ok_supervisors, 1)
        self.assertEqual(metrics.overdue_people, 1)
        self.assertEqual(metrics.pending_meetings, 1)
        self.assertEqual(
            {item.employee.name for item in self.dashboard.employees},
            {"Ana Ejemplo", "Bruno Ejemplo", "Carla Ejemplo", "Diego Ejemplo"},
        )

    def test_filters_supervisor_priority_alert_pending_and_search(self) -> None:
        self.assertEqual(len(filter_employees(self.dashboard, supervisor="Supervisora Uno")), 2)
        self.assertEqual([x.employee.name for x in filter_employees(self.dashboard, priority=Priority.CRITICAL)], ["Ana Ejemplo", "Bruno Ejemplo"])
        self.assertEqual([x.employee.name for x in filter_employees(self.dashboard, mode=">60 días")], ["Bruno Ejemplo"])
        self.assertEqual([x.employee.name for x in filter_employees(self.dashboard, mode="Reunión pendiente")], ["Bruno Ejemplo"])
        self.assertEqual([x.employee.name for x in filter_employees(self.dashboard, search="  carla  ")], ["Carla Ejemplo"])

    def test_export_has_expected_columns_and_filtered_rows(self) -> None:
        filtered = filter_employees(self.dashboard, supervisor="Supervisora Uno", mode=">60 días")
        priorities = {item.supervisor: item.priority for item in self.dashboard.supervisors}
        payload = export_tracking(filtered, priorities)
        workbook = load_workbook(BytesIO(payload), read_only=True, data_only=True)
        sheet = workbook["Seguimiento"]
        self.assertEqual(tuple(cell.value for cell in sheet[1]), EXPORT_HEADERS)
        self.assertEqual(sheet.max_row, 2)
        self.assertEqual(sheet.cell(2, 2).value, "Bruno Ejemplo")
        self.assertEqual(sheet.cell(2, 5).value, "Crítica")
        workbook.close()


if __name__ == "__main__":
    unittest.main()
