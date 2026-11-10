"""Carga e inyección segura del extractor versionado de JIRA."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import json

from delivery_manager_toolbox.modules.jira.constants import (
    DATE_COLUMN_INDEX,
    DEFAULT_PROJECTS,
    EXTRACTOR_VERSION,
    JIRA_ALLOWED_ORIGIN,
    JIRA_DASHBOARD_URL,
    MODAL_ID,
    REQUEST_DELAY_MS,
    REQUEST_HEADERS,
    TIMESHEET_ENDPOINT,
)


_CONFIG_TOKEN = "__DMT_CONFIG__"


@lru_cache(maxsize=1)
def load_extractor_source() -> str:
    source = files("delivery_manager_toolbox.modules.jira").joinpath("assets/extractor.js")
    return source.read_text(encoding="utf-8")


def render_extractor_source() -> str:
    """Inserta exclusivamente configuración pública en el JavaScript legible."""
    template = load_extractor_source()
    if template.count(_CONFIG_TOKEN) != 1:
        raise ValueError("La plantilla del extractor no contiene un único marcador de configuración.")
    configuration = {
        "allowedOrigin": JIRA_ALLOWED_ORIGIN,
        "dashboardUrl": JIRA_DASHBOARD_URL,
        "dateColumnIndex": DATE_COLUMN_INDEX,
        "delayMs": REQUEST_DELAY_MS,
        "endpoint": TIMESHEET_ENDPOINT,
        "headers": REQUEST_HEADERS,
        "modalId": MODAL_ID,
        "projects": DEFAULT_PROJECTS,
        "version": EXTRACTOR_VERSION,
    }
    return template.replace(
        _CONFIG_TOKEN,
        json.dumps(configuration, ensure_ascii=False, separators=(",", ":")),
    )


def build_injection_script() -> str:
    """Evita duplicados y ejecuta el extractor en el documento JIRA ya cargado."""
    modal_id = json.dumps(MODAL_ID)
    return (
        "(() => {"
        f"if (document.getElementById({modal_id})) return;"
        f"{render_extractor_source()}"
        "})();"
    )


__all__ = ["build_injection_script", "load_extractor_source", "render_extractor_source"]
