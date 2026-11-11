"""Interfaz ejecutiva del módulo People 360."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from delivery_manager_toolbox.core.config import People360Settings, load_settings
from delivery_manager_toolbox.core.logging import get_logger
from delivery_manager_toolbox.modules.people_360.export import export_tracking
from delivery_manager_toolbox.modules.people_360.filters import filter_employees
from delivery_manager_toolbox.modules.people_360.models import JournalKind, People360Dashboard, Priority
from delivery_manager_toolbox.modules.people_360.repository import People360Repository
from delivery_manager_toolbox.modules.people_360.service import build_dashboard
from delivery_manager_toolbox.modules.people_360.sharepoint import (
    AuthenticationConfigurationError,
    GraphAuthenticationError,
    GraphFileNotFoundError,
    GraphPermissionError,
    GraphTemporaryError,
    People360SourceError,
    create_people360_repository,
)


LOGGER = get_logger(__name__)
STATE_DASHBOARD = "people360_dashboard"


def render() -> None:
    settings = load_settings().people360
    st.title("People 360")
    st.caption("Seguimiento operativo de supervisores, personas, bitácoras y reuniones")

    refresh = st.button("Actualizar datos", icon=":material/refresh:", width="content")
    if refresh:
        st.session_state.pop(STATE_DASHBOARD, None)

    if STATE_DASHBOARD not in st.session_state:
        status = st.status("Conectando con SharePoint...", expanded=True)
        try:
            repository = create_people360_repository(settings, progress=status.write)
            st.session_state[STATE_DASHBOARD] = load_people360_dashboard(repository, settings)
            status.update(label="Datos de People 360 disponibles", state="complete", expanded=False)
        except People360SourceError as exc:
            LOGGER.warning("No se pudo cargar People 360: %s", type(exc).__name__)
            status.update(label="No se han podido actualizar los datos", state="error", expanded=False)
            st.error(_friendly_source_error(exc), title="Error de acceso a People 360")
            return
        except (OSError, ValueError) as exc:
            LOGGER.warning("No se pudo procesar People 360: %s", type(exc).__name__)
            status.update(label="No se han podido procesar los datos", state="error", expanded=False)
            st.error("El fichero de People 360 no tiene el formato esperado o no está disponible.", title="Error de datos")
            return

    dashboard: People360Dashboard = st.session_state[STATE_DASHBOARD]
    st.caption(f"Última actualización: {dashboard.dataset.loaded_at:%d/%m/%Y %H:%M}")
    _render_metrics(dashboard)

    search = st.text_input("Buscar empleado o supervisor", placeholder="Escribe un nombre")
    priority_options = ["Todas", "Crítica", "Media", "OK", "Sin datos"]
    priority_label = st.selectbox("Prioridad", priority_options, index=0)
    selected_priority = None if priority_label == "Todas" else Priority(priority_label)

    summaries = [
        item for item in dashboard.supervisors
        if selected_priority is None or item.priority is selected_priority
    ]
    query = " ".join(search.strip().casefold().split())
    if query:
        employee_supervisors = {
            item.employee.supervisor_n1 for item in dashboard.employees
            if query in " ".join(item.employee.name.strip().casefold().split())
        }
        summaries = [item for item in summaries if query in " ".join(item.supervisor.strip().casefold().split()) or item.supervisor in employee_supervisors]

    st.subheader("Supervisores N1")
    supervisor_names = [item.supervisor for item in summaries]
    if not supervisor_names:
        st.info("No hay supervisores que coincidan con los filtros.")
        return
    st.dataframe(_supervisor_frame(summaries).style.map(_priority_color, subset=["Prioridad"]), width="stretch", hide_index=True)
    selected_supervisor = st.selectbox("Seleccionar Supervisor N1", supervisor_names)
    mode = st.segmented_control(
        "Filtro del equipo",
        ("Todos", "Críticos", ">60 días", "Sin bitácora", "Reunión pendiente", "Cobertura incompleta"),
        default="Todos",
    ) or "Todos"
    filtered = filter_employees(dashboard, selected_supervisor, selected_priority, mode, search)

    priorities = {item.supervisor: item.priority for item in dashboard.supervisors}
    st.download_button(
        "Exportar seguimiento",
        data=export_tracking(filtered, priorities),
        file_name=settings.export_file_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=not filtered,
        icon=":material/download:",
    )

    st.subheader(f"Equipo de {selected_supervisor}")
    st.dataframe(_employee_frame(filtered), width="stretch", hide_index=True)
    if not filtered:
        st.info("No hay empleados que coincidan con el filtro actual.")
        return

    selected_name = st.selectbox("Abrir ficha de empleado", [item.employee.name for item in filtered])
    selected = next(item for item in filtered if item.employee.name == selected_name)
    _render_employee_card(dashboard, selected)

    if dashboard.dataset.issues:
        with st.expander(f"Calidad de datos · {len(dashboard.dataset.issues)} incidencias"):
            st.caption("Las filas afectadas se han aislado sin interrumpir la carga.")
            st.dataframe(
                pd.DataFrame([{"Hoja": issue.sheet, "Fila": issue.row, "Incidencia": issue.message} for issue in dashboard.dataset.issues]),
                width="stretch", hide_index=True,
            )


def load_people360_dashboard(
    repository: People360Repository, settings: People360Settings
) -> People360Dashboard:
    """Recarga siempre el repositorio recibido; el estado de sesión se gestiona en la UI."""
    dataset = repository.load()
    return build_dashboard(
        dataset,
        threshold_days=settings.alert_threshold_days,
        medium_min=settings.priority_medium_min,
        ok_above=settings.priority_ok_above,
    )


def _friendly_source_error(error: People360SourceError) -> str:
    if isinstance(error, AuthenticationConfigurationError):
        return "La autenticación corporativa no está configurada. Define PEOPLE360_GRAPH_CLIENT_ID con el identificador de la aplicación pública registrada en Microsoft Entra."
    if isinstance(error, GraphAuthenticationError):
        return "No se ha podido completar la autenticación Microsoft 365. Vuelve a intentarlo y finaliza el inicio de sesión corporativo."
    if isinstance(error, GraphPermissionError):
        return f"No se ha podido acceder al fichero. La aplicación necesita el permiso delegado `{error.required_scope}` y acceso del usuario al sitio."
    if isinstance(error, GraphFileNotFoundError):
        return "No se ha encontrado el site, la biblioteca o el fichero de People 360 configurado en SharePoint."
    if isinstance(error, GraphTemporaryError):
        return "Microsoft 365 no está disponible temporalmente. Pulsa Actualizar datos para volver a intentarlo."
    return "No se ha podido acceder al fichero de People 360. Comprueba la configuración y tus permisos."


def _render_metrics(dashboard: People360Dashboard) -> None:
    metrics = dashboard.metrics
    first = st.columns(5)
    values = (
        ("Empleados", metrics.total_employees), ("Supervisores N1", metrics.total_supervisors),
        ("Cobertura empleado", _percent(metrics.average_employee_coverage)),
        ("Cobertura supervisor", _percent(metrics.average_supervisor_coverage)),
        (f"> {load_settings().people360.alert_threshold_days} días", metrics.overdue_people),
    )
    for column, (label, value) in zip(first, values, strict=True):
        column.metric(label, value)
    second = st.columns(4)
    for column, (label, value) in zip(second, (
        ("Supervisores críticos", metrics.critical_supervisors), ("Prioridad media", metrics.medium_supervisors),
        ("Supervisores OK", metrics.ok_supervisors), ("Reuniones pendientes", metrics.pending_meetings),
    ), strict=True):
        column.metric(label, value)


def _supervisor_frame(items) -> pd.DataFrame:
    return pd.DataFrame([{
        "Supervisor N1": item.supervisor, "Empleados": item.employee_count,
        "Cob. empleado": _percent(item.employee_coverage), "Cob. supervisor": _percent(item.supervisor_coverage),
        ">60 días": item.overdue_count, "Reuniones realizadas": item.meetings_completed,
        "Reuniones pendientes": item.meetings_pending, "Prioridad": item.priority.value,
        "Última actualización": item.last_update.strftime("%d/%m/%Y") if item.last_update else "Sin datos",
    } for item in items])


def _employee_frame(items) -> pd.DataFrame:
    return pd.DataFrame([{
        "Empleado": item.employee.name,
        "Última bitácora empleado": _date(item.employee_journal_date), "Estado empleado": item.employee_journal_state,
        "Última bitácora supervisor": _date(item.supervisor_journal_date), "Estado supervisor": item.supervisor_journal_state,
        "Días": item.days_since_supervisor_journal if item.days_since_supervisor_journal is not None else "Sin fecha",
        "Alerta >60d": "Sí" if item.overdue else "No", "Reuniones realizadas": item.meetings_completed,
        "Reuniones pendientes": item.meetings_pending,
        "Cobertura": f"{((item.coverage_employee or 0) + (item.coverage_supervisor or 0)) / 2:.0f}%",
        "Observaciones": item.observations,
    } for item in items])


def _render_employee_card(dashboard, item) -> None:
    st.subheader(item.employee.name)
    basic, tracking, meetings, history = st.tabs(("Datos básicos", "Seguimiento", "Reuniones", "Histórico"))
    with basic:
        st.table(pd.DataFrame({"Campo": ["Supervisor N1", "Supervisor N2", "Estado", "Tecnología", "Ubicación", "Job role", "Área", "Cliente", "Servicio", "Equipo"], "Valor": [item.employee.supervisor_n1, item.employee.supervisor_n2, item.employee.status, item.employee.technology, item.employee.location, item.employee.job_role, item.employee.area, item.employee.client, item.employee.service, item.employee.team]}).fillna("Sin datos"))
    with tracking:
        st.write({
            "Bitácora empleado": item.employee_journal_state,
            "Última bitácora empleado": _date(item.employee_journal_date),
            "Bitácora supervisor": item.supervisor_journal_state,
            "Última bitácora supervisor": _date(item.supervisor_journal_date),
            "Días desde seguimiento supervisor": item.days_since_supervisor_journal,
            "Alerta": item.overdue,
        })
    with meetings:
        rows = [meeting for meeting in dashboard.dataset.meetings if meeting.employee_id == item.employee.employee_id and meeting.is_valid]
        st.dataframe(pd.DataFrame([{"Solicitud": _date(m.requested_at), "Estado": m.status, "Gestión": _date(m.managed_at), "Responsable": m.managed_by or "Sin datos"} for m in rows]), width="stretch", hide_index=True)
    with history:
        rows = [journal for journal in dashboard.dataset.journals if journal.employee_id == item.employee.employee_id]
        rows.sort(key=lambda journal: journal.occurred_at or pd.Timestamp.min.to_pydatetime(), reverse=True)
        st.dataframe(pd.DataFrame([{"Fecha": _date(j.occurred_at), "Tipo": j.kind.value if j.kind else "Desconocido", "Origen": "Empleado" if j.kind is JournalKind.EMPLOYEE else "Supervisor", "Válida": "Sí" if j.is_valid else "No", "Observación": j.content or j.issue or ""} for j in rows]), width="stretch", hide_index=True)


def _priority_color(value: str) -> str:
    return {"Crítica": "background-color: #fee2e2; color: #991b1b", "Media": "background-color: #fef3c7; color: #92400e", "OK": "background-color: #dcfce7; color: #166534"}.get(value, "")


def _percent(value: float | None) -> str:
    return "Sin datos" if value is None else f"{value:.1f}%"


def _date(value) -> str:
    return value.strftime("%d/%m/%Y") if value else "Sin datos"
