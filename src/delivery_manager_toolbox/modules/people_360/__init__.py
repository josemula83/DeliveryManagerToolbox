from delivery_manager_toolbox.core.models import ModuleDefinition


def render() -> None:
    import streamlit as st

    st.title("People 360")
    st.info("Módulo preparado para su futura implementación.")


MODULE = ModuleDefinition("people_360", "People 360", "Visión integral de personas", 20, render)

