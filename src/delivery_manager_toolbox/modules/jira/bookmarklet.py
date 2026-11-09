"""Generación reproducible del marcador y su instalador local."""

from __future__ import annotations

from functools import lru_cache
import html
from importlib.resources import files
import json
from urllib.parse import quote

from delivery_manager_toolbox.modules.jira.constants import (
    BOOKMARKLET_NAME,
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


def build_bookmarklet() -> str:
    """Codifica el fuente legible como una URL JavaScript de una sola línea."""
    return "javascript:" + quote(render_extractor_source(), safe="()")


def build_installer_html() -> str:
    bookmarklet = html.escape(build_bookmarklet(), quote=True)
    name = html.escape(BOOKMARKLET_NAME)
    return f"""<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><title>Instalar {name}</title></head>
<body style="font-family:Arial,sans-serif;padding:24px;color:#172b4d">
  <h1 style="font-size:22px">Instalar {name}</h1>
  <p>Arrastra el siguiente botón a la barra de favoritos del navegador:</p>
  <p><a href="{bookmarklet}" style="display:inline-block;background:#1464a5;color:#fff;text-decoration:none;padding:11px 18px;border-radius:5px;font-weight:bold">{name}</a></p>
  <p>Después abre JIRA, inicia sesión y pulsa el favorito.</p>
</body>
</html>"""


__all__ = [
    "build_bookmarklet",
    "build_installer_html",
    "load_extractor_source",
    "render_extractor_source",
]
