"""Vista Streamlit coordinada del navegador JIRA y Aplantillado."""

from __future__ import annotations

import streamlit as st

from delivery_manager_toolbox.core.config import load_settings
from delivery_manager_toolbox.modules.jira.aplantillado_view import render_aplantillado
from delivery_manager_toolbox.modules.jira.constants import DEFAULT_PROJECTS, EXTRACTOR_VERSION
from delivery_manager_toolbox.modules.jira.desktop_bridge import (
    acknowledge_analysis_request,
    request_jira_extraction,
    request_jira_snapshot,
    request_jira_window,
)
from delivery_manager_toolbox.modules.jira.extraction_result import (
    ExtractionResult,
    ExtractionResultError,
    extraction_from_transport,
)
from delivery_manager_toolbox.modules.jira.people_config import PeopleConfigurationRepository
from delivery_manager_toolbox.modules.jira.result_exporter import (
    export_result_csv,
    result_csv_filename,
)


_RESULT_KEY = "jira_extraction_result"
_REVISION_KEY = "jira_extraction_revision"
_VIEW_KEY = "jira_workspace_view"
_PENDING_VIEW_KEY = "jira_workspace_pending_view"


def render_page() -> None:
    st.session_state.setdefault(_RESULT_KEY, None)
    st.session_state.setdefault(_REVISION_KEY, 0)
    st.session_state.setdefault(_VIEW_KEY, "JIRA")
    pending_view = st.session_state.pop(_PENDING_VIEW_KEY, None)
    if pending_view is not None:
        st.session_state[_VIEW_KEY] = pending_view
    _sync_desktop_state()

    st.title("JIRA · Informe de Incurridos BBVA")
    st.caption("Extracción corporativa y análisis Aplantillado sobre un único resultado en memoria.")
    selected_view = st.segmented_control(
        "Área",
        ("JIRA", "Aplantillado"),
        key=_VIEW_KEY,
        label_visibility="collapsed",
    )

    result = st.session_state.get(_RESULT_KEY)
    if selected_view == "Aplantillado":
        repository = PeopleConfigurationRepository(load_settings().jira.people_file)
        render_aplantillado(result, repository)
    else:
        _render_jira_workspace(result)


@st.fragment(run_every=1)
def _sync_desktop_state() -> None:
    snapshot = request_jira_snapshot()
    if not snapshot.available:
        return
    current_revision = int(st.session_state.get(_REVISION_KEY, 0))
    changed = snapshot.revision != current_revision
    if changed:
        st.session_state[_REVISION_KEY] = snapshot.revision
        if snapshot.has_extraction:
            payload = request_jira_extraction()
            try:
                st.session_state[_RESULT_KEY] = (
                    extraction_from_transport(payload) if payload is not None else None
                )
            except ExtractionResultError:
                st.session_state[_RESULT_KEY] = None
        else:
            st.session_state[_RESULT_KEY] = None
    if snapshot.analysis_requested:
        st.session_state[_PENDING_VIEW_KEY] = "Aplantillado"
        acknowledge_analysis_request()
        changed = True
    if changed:
        st.rerun()


def _render_jira_workspace(result: ExtractionResult | None) -> None:
    status = request_jira_window()
    if status.available:
        st.success(
            "La ventana JIRA está preparada. Inicia sesión allí si se solicita; "
            "el formulario del informe aparecerá automáticamente.",
            title="JIRA abierto",
        )
    else:
        st.error(status.detail, title="Contenedor de escritorio no disponible")
        st.code("python desktop.py", language="powershell")

    if result is not None:
        st.success(
            f"Extracción disponible: {len(result.rows)} registros · "
            f"{result.date_from.isoformat()} → {result.date_to.isoformat()}.",
            title="Resultado recibido",
        )
        with st.container(horizontal=True):
            st.download_button(
                "Descargar CSV",
                data=export_result_csv(result),
                file_name=result_csv_filename(result),
                mime="text/csv",
                icon=":material/download:",
            )
            if st.button("Aplantillado", icon=":material/calendar_month:"):
                st.session_state[_PENDING_VIEW_KEY] = "Aplantillado"
                st.rerun()

    st.info(
        "La autenticación se realiza íntegramente dentro de Edge WebView2. La Toolbox "
        "no lee, copia ni almacena credenciales, cookies o tokens de JIRA."
    )
    with st.expander("Proyectos y funcionamiento"):
        st.write("Los proyectos incluidos inicialmente son:")
        st.write(", ".join(DEFAULT_PROJECTS))
        st.write(
            "Los periodos se dividen por meses naturales y se consultan secuencialmente. "
            "El resultado completo se transfiere en memoria a la Toolbox; CSV y Aplantillado "
            "consumen ese mismo modelo normalizado."
        )
    with st.expander("Sesión y privacidad"):
        st.write(
            "El perfil del navegador se conserva fuera del repositorio. Los registros de la "
            "extracción actual solo viven en memoria y se sustituyen al iniciar otra extracción."
        )
    st.caption(f"Extractor {EXTRACTOR_VERSION}")


__all__ = ["render_page"]
