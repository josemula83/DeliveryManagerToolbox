from __future__ import annotations

from io import BytesIO
import json
import inspect
from pathlib import Path
import unittest

from openpyxl import Workbook
from delivery_manager_toolbox.core.config import People360Settings
from delivery_manager_toolbox.modules.people_360.models import People360Dataset
from delivery_manager_toolbox.modules.people_360.sharepoint import (
    GRAPH_ROOT,
    GraphAuthenticationError,
    GraphFileNotFoundError,
    GraphPermissionError,
    GraphTemporaryError,
    HttpResult,
    LocalPeople360Repository,
    MicrosoftGraphPeople360Client,
    SharePointPeople360Repository,
    create_people360_repository,
)
from delivery_manager_toolbox.modules.people_360.ui import load_people360_dashboard


def workbook_bytes(employee_count: int = 1) -> bytes:
    workbook = Workbook()
    employees = workbook.active
    employees.title = "Empleados"
    employees.append(["numeroEmpleado", "nombre", "supervisor_n1", "estado"])
    for index in range(employee_count):
        employees.append([f"E{index + 1:02}", f"Persona {index + 1}", "Supervisora", "ACTIVO"])
    journals = workbook.create_sheet("Bitacoras")
    journals.append(["numeroEmpleado", "nombre", "tipo", "fecha", "bitacora_empleado", "bitacora_supervisor"])
    meetings = workbook.create_sheet("Historico reuniones")
    meetings.append(["numeroEmpleado", "nombre", "fechaSolicitud", "estado", "fechaGestion", "gestionadaPor"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class FakeTokenProvider:
    def __init__(self):
        self.scopes: list[str] = []

    def get_token(self, scope: str) -> str:
        self.scopes.append(scope)
        return "synthetic-token"


class FakeTransport:
    def __init__(self, results):
        self.results = list(results)
        self.requests: list[tuple[str, dict[str, str]]] = []

    def get(self, url, headers=None):
        self.requests.append((url, dict(headers or {})))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def json_result(value: dict, status: int = 200) -> HttpResult:
    return HttpResult(status, json.dumps(value).encode("utf-8"), {})


def graph_results(payload: bytes | None = None):
    return [
        json_result({"id": "tenant,site,web"}),
        json_result({"value": [{"id": "drive-1", "name": "Documentos compartidos"}]}),
        json_result({"id": "item-1", "name": "empleados.xlsx", "file": {}, "@microsoft.graph.downloadUrl": "https://download.invalid/file"}),
        HttpResult(200, payload or workbook_bytes(), {}),
    ]


def settings(source: str = "sharepoint", local_file: Path | None = None) -> People360Settings:
    return People360Settings(
        source=source,
        sharepoint_hostname="tenant.example.invalid",
        sharepoint_site_path="/sites/People",
        sharepoint_library="Documentos compartidos",
        sharepoint_folder="General/People360",
        file_name="empleados.xlsx",
        local_file=local_file,
        graph_tenant_id="organizations",
        graph_client_id="client-id",
        graph_scope="https://graph.microsoft.com/Sites.Read.All",
        alert_threshold_days=60,
        priority_medium_min=60,
        priority_ok_above=80,
        export_file_name="people360.xlsx",
    )


class SharePointRepositoryTests(unittest.TestCase):
    def test_found_file_is_downloaded_without_forwarding_graph_token(self) -> None:
        token = FakeTokenProvider()
        transport = FakeTransport(graph_results(b"xlsx-content"))
        client = MicrosoftGraphPeople360Client(token, transport, settings().graph_scope)

        content = client.download_file(
            "tenant.example.invalid", "/sites/People", "Documentos compartidos",
            "General/People360", "empleados.xlsx",
        )

        self.assertEqual(content, b"xlsx-content")
        self.assertEqual(token.scopes, [settings().graph_scope])
        self.assertIn("Authorization", transport.requests[0][1])
        self.assertEqual(transport.requests[-1][1], {})
        self.assertTrue(transport.requests[0][0].startswith(f"{GRAPH_ROOT}/sites/"))

    def test_not_found_is_reported(self) -> None:
        transport = FakeTransport(graph_results()[:2] + [HttpResult(404, b"{}", {})])
        client = MicrosoftGraphPeople360Client(FakeTokenProvider(), transport, settings().graph_scope)
        with self.assertRaises(GraphFileNotFoundError):
            client.download_file("tenant", "/sites/People", "Documentos compartidos", "folder", "missing.xlsx")

    def test_401_is_authentication_error(self) -> None:
        client = MicrosoftGraphPeople360Client(
            FakeTokenProvider(), FakeTransport([HttpResult(401, b"{}", {})]), settings().graph_scope
        )
        with self.assertRaises(GraphAuthenticationError):
            client.download_file("tenant", "/sites/People", "Documentos compartidos", "folder", "file.xlsx")

    def test_403_identifies_required_scope(self) -> None:
        client = MicrosoftGraphPeople360Client(
            FakeTokenProvider(), FakeTransport([HttpResult(403, b"{}", {})]), settings().graph_scope
        )
        with self.assertRaises(GraphPermissionError) as caught:
            client.download_file("tenant", "/sites/People", "Documentos compartidos", "folder", "file.xlsx")
        self.assertEqual(caught.exception.required_scope, settings().graph_scope)

    def test_network_error_is_preserved_as_temporary(self) -> None:
        client = MicrosoftGraphPeople360Client(
            FakeTokenProvider(), FakeTransport([GraphTemporaryError("network")]), settings().graph_scope
        )
        with self.assertRaises(GraphTemporaryError):
            client.download_file("tenant", "/sites/People", "Documentos compartidos", "folder", "file.xlsx")

    def test_sharepoint_repository_parses_xlsx_bytes(self) -> None:
        client = MicrosoftGraphPeople360Client(
            FakeTokenProvider(), FakeTransport(graph_results(workbook_bytes(2))), settings().graph_scope
        )
        repository = SharePointPeople360Repository(
            client, "tenant", "/sites/People", "Documentos compartidos", "folder", "empleados.xlsx"
        )
        self.assertEqual(len(repository.load().employees), 2)

    def test_local_repository_is_only_selected_explicitly(self) -> None:
        path = Path("synthetic-people360.xlsx")
        local = create_people360_repository(settings("local", path))
        self.assertIsInstance(local, LocalPeople360Repository)

        fake_client = MicrosoftGraphPeople360Client(
            FakeTokenProvider(), FakeTransport(graph_results()), settings().graph_scope
        )
        sharepoint = create_people360_repository(settings("sharepoint", path), graph_client=fake_client)
        self.assertIsInstance(sharepoint, SharePointPeople360Repository)

    def test_refresh_loads_repository_again(self) -> None:
        class ChangingRepository:
            def __init__(self):
                self.calls = 0

            def load(self) -> People360Dataset:
                self.calls += 1
                return SharePointPeople360Repository(
                    MicrosoftGraphPeople360Client(
                        FakeTokenProvider(), FakeTransport(graph_results(workbook_bytes(self.calls))), settings().graph_scope
                    ),
                    "tenant", "/sites/People", "Documentos compartidos", "folder", "empleados.xlsx",
                ).load()

        repository = ChangingRepository()
        first = load_people360_dashboard(repository, settings())
        second = load_people360_dashboard(repository, settings())
        self.assertEqual(repository.calls, 2)
        self.assertEqual(first.metrics.total_employees, 1)
        self.assertEqual(second.metrics.total_employees, 2)

    def test_production_ui_has_no_manual_uploader(self) -> None:
        from delivery_manager_toolbox.modules.people_360 import ui

        source = inspect.getsource(ui.render)
        self.assertNotIn("file_uploader", source)
        self.assertIn("Actualizar datos", source)


if __name__ == "__main__":
    unittest.main()
