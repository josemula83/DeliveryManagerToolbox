"""Contratos puros para el navegador JIRA administrado por la aplicación."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path
from urllib.parse import urlparse

from delivery_manager_toolbox.modules.jira.constants import (
    JIRA_ALLOWED_ORIGIN,
    JIRA_DASHBOARD_URL,
)


class JiraBrowserState(StrEnum):
    STARTING = "starting"
    LOADING = "loading"
    LOGIN_REQUIRED = "login_required"
    LOGIN_IN_PROGRESS = "login_in_progress"
    AUTHENTICATED = "authenticated"
    EXTRACTOR_INJECTED = "extractor_injected"
    WINDOW_CLOSED = "window_closed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class NavigationDecision:
    state: JiraBrowserState
    should_inject: bool = False


@dataclass(frozen=True, slots=True)
class BrowserConfiguration:
    start_url: str
    allowed_origin: str
    profile_path: Path
    renderer: str = "edgechromium"
    persistent_session: bool = True
    allow_downloads: bool = True


def default_profile_path(environment: dict[str, str] | None = None) -> Path:
    """Devuelve un perfil local persistente, deliberadamente fuera del repositorio."""
    values = os.environ if environment is None else environment
    local_data = values.get("LOCALAPPDATA")
    base = Path(local_data) if local_data else Path.home() / "AppData" / "Local"
    return base / "DeliveryManagerToolbox" / "WebView2"


def build_browser_configuration(
    environment: dict[str, str] | None = None,
) -> BrowserConfiguration:
    return BrowserConfiguration(
        start_url=JIRA_DASHBOARD_URL,
        allowed_origin=JIRA_ALLOWED_ORIGIN,
        profile_path=default_profile_path(environment),
    )


def decide_navigation(url: str | None) -> NavigationDecision:
    """Clasifica una navegación sin inspeccionar cookies, DOM ni datos de sesión."""
    if not url or url == "about:blank":
        return NavigationDecision(JiraBrowserState.LOADING)

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return NavigationDecision(JiraBrowserState.ERROR)

    origin = f"{parsed.scheme}://{parsed.netloc}".casefold()
    if origin != JIRA_ALLOWED_ORIGIN.casefold():
        return NavigationDecision(JiraBrowserState.LOGIN_IN_PROGRESS)

    login_markers = ("login", "authenticate", "authenticator", "logout")
    target = f"{parsed.path}?{parsed.query}".casefold()
    if any(marker in target for marker in login_markers):
        return NavigationDecision(JiraBrowserState.LOGIN_REQUIRED)

    return NavigationDecision(JiraBrowserState.AUTHENTICATED, should_inject=True)


__all__ = [
    "BrowserConfiguration",
    "JiraBrowserState",
    "NavigationDecision",
    "build_browser_configuration",
    "decide_navigation",
    "default_profile_path",
]
