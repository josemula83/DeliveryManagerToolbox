"""Vista Streamlit del flujo asistido para el informe de incurridos."""

from __future__ import annotations

import streamlit as st

from delivery_manager_toolbox.modules.jira.bookmarklet import (
    build_bookmarklet,
    build_installer_html,
)
from delivery_manager_toolbox.modules.jira.constants import (
    BOOKMARKLET_NAME,
    DEFAULT_PROJECTS,
    EXTRACTOR_VERSION,
    JIRA_DASHBOARD_URL,
)


def render_page() -> None:
    st.title("JIRA · Informe de Incurridos BBVA")
    st.caption("Extrae y consolida Worklogs desde tu sesión activa de JIRA.")

    st.info(
        "El informe se ejecuta dentro de JIRA para utilizar tu sesión iniciada. "
        "La Toolbox no guarda credenciales ni cookies."
    )

    st.subheader("1. Instala el marcador")
    st.write(
        f"Descarga y abre el instalador; después arrastra **{BOOKMARKLET_NAME}** "
        "a la barra de favoritos. Solo necesitas repetirlo cuando se publique una nueva versión."
    )
    bookmarklet = build_bookmarklet()

    installer = build_installer_html().encode("utf-8")
    st.download_button(
        "Descargar instalador del marcador",
        installer,
        file_name="Instalar_Incurridos_BBVA.html",
        mime="text/html",
        help="Abre el HTML descargado y arrastra su botón a la barra de favoritos.",
    )

    with st.expander("Copiar manualmente la dirección del marcador"):
        st.write(
            "Crea un favorito nuevo, copia la dirección completa con el botón del bloque "
            "y pégala en el campo URL del favorito."
        )
        st.code(bookmarklet, language=None, wrap_lines=True)

    st.subheader("2. Abre JIRA y genera el informe")
    st.link_button("Abrir JIRA", JIRA_DASHBOARD_URL, type="primary")
    st.markdown(
        """
1. Inicia sesión en JIRA si es necesario.
2. Pulsa el favorito **Incurridos BBVA**.
3. Selecciona las fechas y los proyectos.
4. Pulsa **Generar informe** y, al terminar, **Descargar CSV**.
"""
    )

    with st.expander("Proyectos y funcionamiento"):
        st.write("Los proyectos incluidos inicialmente son:")
        st.write(", ".join(DEFAULT_PROJECTS))
        st.write(
            "Los periodos se dividen por meses naturales y se consultan secuencialmente. "
            "Los errores parciales se muestran sin descartar los registros recuperados. "
            "El CSV usa UTF-8 con BOM y separador de punto y coma para Excel."
        )

    with st.expander("¿Por qué se utiliza un marcador?"):
        st.write(
            "JIRA autentica las consultas con la sesión de su propia pestaña. Los navegadores "
            "no permiten que Streamlit controle otra página ni comparta sus cookies. El marcador "
            "ejecuta el extractor en JIRA sin copiar ni almacenar la sesión."
        )

    st.caption(f"Extractor {EXTRACTOR_VERSION}")


__all__ = ["render_page"]
