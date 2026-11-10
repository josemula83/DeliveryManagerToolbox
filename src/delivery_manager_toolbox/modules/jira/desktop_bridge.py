"""Canal local mínimo entre la vista Streamlit y el contenedor de escritorio."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from delivery_manager_toolbox.modules.jira.browser import JiraBrowserState


BRIDGE_URL_ENV = "DMT_DESKTOP_BRIDGE_URL"
BRIDGE_TOKEN_ENV = "DMT_DESKTOP_BRIDGE_TOKEN"


@dataclass(frozen=True, slots=True)
class BridgeStatus:
    available: bool
    state: JiraBrowserState
    detail: str = ""


def request_jira_window(
    environment: dict[str, str] | None = None,
    opener: Callable[..., object] = urlopen,
) -> BridgeStatus:
    """Solicita abrir JIRA sin intercambiar credenciales, cookies ni URLs visitadas."""
    values = os.environ if environment is None else environment
    base_url = values.get(BRIDGE_URL_ENV)
    token = values.get(BRIDGE_TOKEN_ENV)
    if not base_url or not token:
        return BridgeStatus(False, JiraBrowserState.ERROR, "Inicia la Toolbox con desktop.py.")

    request = Request(
        f"{base_url.rstrip('/')}/jira/open",
        data=b"{}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        response = opener(request, timeout=2)
        payload = json.loads(response.read().decode("utf-8"))
        return BridgeStatus(True, JiraBrowserState(payload["state"]))
    except (HTTPError, URLError, OSError, ValueError, KeyError, json.JSONDecodeError):
        return BridgeStatus(False, JiraBrowserState.ERROR, "El contenedor de escritorio no responde.")


__all__ = [
    "BRIDGE_TOKEN_ENV",
    "BRIDGE_URL_ENV",
    "BridgeStatus",
    "request_jira_window",
]
