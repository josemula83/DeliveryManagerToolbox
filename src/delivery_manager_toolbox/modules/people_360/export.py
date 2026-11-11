"""Exportación del seguimiento filtrado, preparada para un futuro canal Teams."""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from delivery_manager_toolbox.modules.people_360.models import EmployeeTracking, Priority


EXPORT_HEADERS = (
    "Supervisor N1", "Empleado", "Cobertura empleado", "Cobertura supervisor",
    "Prioridad", "Última bitácora", "Días desde última bitácora", "Alerta", "Reunión pendiente",
)


def export_tracking(items: tuple[EmployeeTracking, ...], priorities: dict[str, Priority]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Seguimiento"
    sheet.append(EXPORT_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="334155")
    for item in items:
        last_date = item.supervisor_journal_date
        sheet.append((
            item.employee.supervisor_n1 or "Sin asignar", item.employee.name,
            item.coverage_employee, item.coverage_supervisor,
            priorities.get(item.employee.supervisor_n1 or "", Priority.NO_DATA).value,
            last_date, item.days_since_supervisor_journal,
            "Sí" if item.overdue else "No", "Sí" if item.meetings_pending else "No",
        ))
    for row in sheet.iter_rows(min_row=2):
        row[2].number_format = '0.00"%"'
        row[3].number_format = '0.00"%"'
        row[5].number_format = "dd/mm/yyyy"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = (24, 30, 20, 22, 14, 20, 27, 12, 20)
    for column, width in zip(sheet.columns, widths, strict=True):
        sheet.column_dimensions[column[0].column_letter].width = width
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()
