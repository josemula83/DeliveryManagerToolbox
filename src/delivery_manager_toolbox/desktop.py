"""Contenedor nativo de Streamlit y JIRA basado en Edge/WebView2."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

from delivery_manager_toolbox.core.config import load_settings
from delivery_manager_toolbox.core.logging import get_logger
from delivery_manager_toolbox.modules.jira.browser import (
    BrowserConfiguration,
    JiraBrowserState,
    build_browser_configuration,
    decide_navigation,
)
from delivery_manager_toolbox.modules.jira.desktop_bridge import (
    BRIDGE_TOKEN_ENV,
    BRIDGE_URL_ENV,
)
from delivery_manager_toolbox.modules.jira.extractor import build_injection_script
from delivery_manager_toolbox.modules.jira.host_api import ExtractionStore, JiraHostApi
from delivery_manager_toolbox.modules.jira.people_config import PeopleConfigurationRepository


LOGGER = get_logger(__name__)


class JiraWindowController:
    def __init__(self, store: ExtractionStore | None = None) -> None:
        self.window: object | None = None
        self.main_window: object | None = None
        self.store = store or ExtractionStore()
        self.state = JiraBrowserState.STARTING
        self._lock = threading.Lock()
        self._shutting_down = False
        self._analysis_requested = False

    def attach(self, window: object) -> None:
        self.window = window
        self._set_state(JiraBrowserState.LOADING)

    def attach_main(self, window: object) -> None:
        self.main_window = window

    def open(self) -> JiraBrowserState:
        window = self.window
        if window is None:
            return JiraBrowserState.STARTING
        try:
            window.show()  # type: ignore[attr-defined]
            window.restore()  # type: ignore[attr-defined]
        except Exception:
            LOGGER.exception("No se pudo mostrar la ventana JIRA")
            self._set_state(JiraBrowserState.ERROR)
        return self.state

    def on_loaded(self) -> None:
        window = self.window
        if window is None:
            return
        try:
            decision = decide_navigation(window.get_current_url())  # type: ignore[attr-defined]
            self._set_state(decision.state)
            if decision.should_inject:
                window.run_js(build_injection_script())  # type: ignore[attr-defined]
                self._set_state(JiraBrowserState.EXTRACTOR_INJECTED)
        except Exception:
            LOGGER.exception("Fallo al preparar el extractor JIRA")
            self._set_state(JiraBrowserState.ERROR)

    def on_closed(self) -> None:
        self._set_state(JiraBrowserState.WINDOW_CLOSED)

    def open_analysis(self) -> None:
        with self._lock:
            self._analysis_requested = True
        if self.main_window is not None:
            self.main_window.show()  # type: ignore[attr-defined]
            self.main_window.restore()  # type: ignore[attr-defined]
        if self.window is not None:
            self.window.hide()  # type: ignore[attr-defined]

    def analysis_requested(self) -> bool:
        with self._lock:
            return self._analysis_requested

    def acknowledge_analysis(self) -> None:
        with self._lock:
            self._analysis_requested = False

    def on_closing(self) -> bool:
        """Oculta la ventana para poder reutilizar la sesión al volver al módulo."""
        if self._shutting_down:
            return True
        if self.window is not None:
            self.window.hide()  # type: ignore[attr-defined]
        return False

    def shutdown(self) -> None:
        self._shutting_down = True
        if self.window is not None:
            self.window.destroy()  # type: ignore[attr-defined]

    def _set_state(self, state: JiraBrowserState) -> None:
        with self._lock:
            if state != self.state:
                LOGGER.info("Estado del navegador JIRA: %s", state.value)
            self.state = state


def _bridge_handler(
    controller: JiraWindowController,
    token: str,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _authorized(self) -> bool:
            return self.headers.get("Authorization") == f"Bearer {token}"

        def _send_json(self, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                self.send_error(404)
                return
            revision, result = controller.store.snapshot()
            if self.path == "/jira/status":
                self._send_json(
                    {
                        "state": controller.state.value,
                        "revision": revision,
                        "hasExtraction": result is not None,
                        "analysisRequested": controller.analysis_requested(),
                    }
                )
                return
            if self.path == "/jira/extraction" and result is not None:
                self._send_json(result.to_transport())
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                self.send_error(404)
                return
            if self.path == "/jira/open":
                self._send_json({"state": controller.open().value})
                return
            if self.path == "/jira/analysis/ack":
                controller.acknowledge_analysis()
                self._send_json({"ok": True})
                return
            self.send_error(404)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for_streamlit(url: str, process: subprocess.Popen[bytes], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Streamlit terminó antes de iniciar la interfaz.")
        try:
            with urlopen(f"{url}/_stcore/health", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError("Streamlit no respondió dentro del tiempo esperado.")


def run_desktop(
    configuration: BrowserConfiguration | None = None,
    app_path: Path | None = None,
) -> None:
    if sys.platform != "win32":
        raise RuntimeError("El contenedor integrado requiere Windows y Edge WebView2.")

    import webview

    config = configuration or build_browser_configuration()
    root = Path.cwd()
    streamlit_app = app_path or root / "app.py"
    streamlit_port = _free_port()
    bridge_port = _free_port()
    streamlit_url = f"http://127.0.0.1:{streamlit_port}"
    token = secrets.token_urlsafe(32)
    environment = os.environ.copy()
    environment[BRIDGE_URL_ENV] = f"http://127.0.0.1:{bridge_port}"
    environment[BRIDGE_TOKEN_ENV] = token

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(streamlit_app),
            "--server.headless=true",
            f"--server.port={streamlit_port}",
            "--browser.gatherUsageStats=false",
        ],
        cwd=root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    controller = JiraWindowController()
    people_repository = PeopleConfigurationRepository(load_settings().jira.people_file)
    host_api = JiraHostApi(controller.store, people_repository, controller.open_analysis)
    bridge = ThreadingHTTPServer(("127.0.0.1", bridge_port), _bridge_handler(controller, token))
    bridge_thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    bridge_thread.start()

    try:
        _wait_for_streamlit(streamlit_url, process)
        config.profile_path.mkdir(parents=True, exist_ok=True)
        webview.settings["ALLOW_DOWNLOADS"] = config.allow_downloads
        main_window = webview.create_window(
            "Delivery Manager Toolbox", streamlit_url, width=1440, height=900
        )
        jira_window = webview.create_window(
            "Delivery Manager Toolbox · JIRA",
            config.start_url,
            width=1440,
            height=900,
            hidden=True,
            js_api=host_api,
        )
        controller.attach_main(main_window)
        controller.attach(jira_window)
        jira_window.events.loaded += controller.on_loaded
        jira_window.events.closing += controller.on_closing
        jira_window.events.closed += controller.on_closed
        main_window.events.closing += controller.shutdown
        webview.start(
            gui=config.renderer,
            private_mode=not config.persistent_session,
            storage_path=str(config.profile_path),
        )
    finally:
        bridge.shutdown()
        bridge.server_close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


__all__ = ["JiraWindowController", "run_desktop"]
