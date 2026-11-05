from delivery_manager_toolbox.core.models import ModuleDefinition


def render() -> None:
    import streamlit as st

    st.title("Jira")
    st.info("Módulo preparado para su futura implementación.")


MODULE = ModuleDefinition("jira", "Jira", "Automatizaciones relacionadas con Jira", 30, render)

