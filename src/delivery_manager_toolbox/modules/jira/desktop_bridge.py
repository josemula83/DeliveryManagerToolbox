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


@dataclass(frozen=True, slots=True)
class BridgeSnapshot:
    available: bool
    state: JiraBrowserState
    revision: int = 0
    has_extraction: bool = False
    analysis_requested: bool = False
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


def request_jira_snapshot(
    environment: dict[str, str] | None = None,
    opener: Callable[..., object] = urlopen,
) -> BridgeSnapshot:
    payload = _bridge_get("/jira/status", environment, opener)
    if payload is None:
        return BridgeSnapshot(False, JiraBrowserState.ERROR, detail="El contenedor no responde.")
    try:
        return BridgeSnapshot(
            True,
            JiraBrowserState(payload["state"]),
            int(payload["revision"]),
            bool(payload["hasExtraction"]),
            bool(payload["analysisRequested"]),
        )
    except (KeyError, TypeError, ValueError):
        return BridgeSnapshot(False, JiraBrowserState.ERROR, detail="Respuesta local no válida.")


def request_jira_extraction(
    environment: dict[str, str] | None = None,
    opener: Callable[..., object] = urlopen,
) -> dict[str, object] | None:
    return _bridge_get("/jira/extraction", environment, opener)


def acknowledge_analysis_request(
    environment: dict[str, str] | None = None,
    opener: Callable[..., object] = urlopen,
) -> bool:
    return _bridge_post("/jira/analysis/ack", environment, opener) is not None


def _bridge_get(
    path: str,
    environment: dict[str, str] | None,
    opener: Callable[..., object],
) -> dict[str, object] | None:
    values = os.environ if environment is None else environment
    base_url = values.get(BRIDGE_URL_ENV)
    token = values.get(BRIDGE_TOKEN_ENV)
    if not base_url or not token:
        return None
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    return _read_response(request, opener)


def _bridge_post(
    path: str,
    environment: dict[str, str] | None,
    opener: Callable[..., object],
) -> dict[str, object] | None:
    values = os.environ if environment is None else environment
    base_url = values.get(BRIDGE_URL_ENV)
    token = values.get(BRIDGE_TOKEN_ENV)
    if not base_url or not token:
        return None
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=b"{}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    return _read_response(request, opener)


def _read_response(request: Request, opener: Callable[..., object]) -> dict[str, object] | None:
    try:
        response = opener(request, timeout=2)
        value = json.loads(response.read().decode("utf-8"))
        return value if isinstance(value, dict) else None
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
        return None


__all__ = [
    "BRIDGE_TOKEN_ENV",
    "BRIDGE_URL_ENV",
    "BridgeSnapshot",
    "BridgeStatus",
    "acknowledge_analysis_request",
    "request_jira_extraction",
    "request_jira_snapshot",
    "request_jira_window",
]
