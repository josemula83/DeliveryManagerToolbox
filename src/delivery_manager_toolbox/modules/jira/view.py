"""Vista Streamlit del navegador JIRA integrado."""

from __future__ import annotations

import streamlit as st

from delivery_manager_toolbox.modules.jira.constants import (
    DEFAULT_PROJECTS,
    EXTRACTOR_VERSION,
)
from delivery_manager_toolbox.modules.jira.desktop_bridge import request_jira_window


def render_page() -> None:
    st.title("JIRA · Informe de Incurridos BBVA")
    st.caption("Navegador corporativo integrado con extracción automática.")

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

    st.info(
        "La autenticación se realiza íntegramente dentro de Edge WebView2. La Toolbox "
        "no lee, copia ni almacena credenciales, cookies o tokens de JIRA."
    )

    with st.expander("Proyectos y funcionamiento"):
        st.write("Los proyectos incluidos inicialmente son:")
        st.write(", ".join(DEFAULT_PROJECTS))
        st.write(
            "Los periodos se dividen por meses naturales y se consultan secuencialmente. "
            "Los errores parciales se muestran sin descartar los registros recuperados. "
            "El CSV usa UTF-8 con BOM y separador de punto y coma para Excel."
        )

    with st.expander("Sesión y privacidad"):
        st.write(
            "El navegador integrado conserva su perfil en el almacenamiento local de Windows, "
            "fuera del repositorio. Python solo conoce el estado general de navegación y ordena "
            "inyectar el extractor cuando el documento pertenece al origen autorizado."
        )

    st.caption(f"Extractor {EXTRACTOR_VERSION}")


__all__ = ["render_page"]
