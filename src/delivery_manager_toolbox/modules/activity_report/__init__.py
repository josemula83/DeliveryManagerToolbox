from delivery_manager_toolbox.core.models import ModuleDefinition


def render() -> None:
    import streamlit as st

    st.title("Activity Report")
    st.info("Módulo preparado para su futura implementación.")


MODULE = ModuleDefinition(
    "activity_report", "Activity Report", "Generación de informes de actividad", 40, render
)

