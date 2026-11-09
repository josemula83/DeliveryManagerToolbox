"""Herramienta asistida para extraer incurridos desde una sesión JIRA activa."""

from delivery_manager_toolbox.core.models import ModuleDefinition


def render() -> None:
    from delivery_manager_toolbox.modules.jira.view import render_page

    render_page()


MODULE = ModuleDefinition(
    "jira",
    "JIRA · Incurridos",
    "Extracción consolidada de Worklogs desde JIRA",
    30,
    render,
)

