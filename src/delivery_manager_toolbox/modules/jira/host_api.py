"""API explícita JavaScript→Python y almacén volátil de la extracción JIRA."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
import json
import threading

from delivery_manager_toolbox.modules.jira.extraction_result import (
    ExtractionResult,
    ExtractionResultError,
    extraction_from_webview,
)
from delivery_manager_toolbox.modules.jira.people_config import (
    PeopleConfiguration,
    PeopleConfigurationError,
    PeopleConfigurationRepository,
    merge_detected_people,
)
from delivery_manager_toolbox.modules.jira.result_exporter import (
    export_result_csv,
    result_csv_filename,
)


class ExtractionStore:
    """Conserva únicamente la extracción actual en memoria del proceso de escritorio."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revision = 0
        self._result: ExtractionResult | None = None

    def replace(self, result: ExtractionResult) -> int:
        with self._lock:
            self._revision += 1
            self._result = result
            return self._revision

    def clear(self) -> int:
        with self._lock:
            self._revision += 1
            self._result = None
            return self._revision

    def snapshot(self) -> tuple[int, ExtractionResult | None]:
        with self._lock:
            return self._revision, self._result


class JiraHostApi:
    def __init__(
        self,
        store: ExtractionStore,
        people_repository: PeopleConfigurationRepository,
        open_analysis: Callable[[], None],
    ) -> None:
        self._store = store
        self._people_repository = people_repository
        self._open_analysis = open_analysis

    def clear_jira_extraction(self) -> dict[str, object]:
        return {"ok": True, "revision": self._store.clear()}

    def receive_jira_extraction(self, message: object) -> dict[str, object]:
        try:
            decoded = json.loads(message) if isinstance(message, str) else message
            if not isinstance(decoded, Mapping):
                raise ExtractionResultError("El mensaje recibido no es un objeto.")
            result = extraction_from_webview(decoded)
            warning = ""
            try:
                _, added = self._people_repository.merge_and_save(result.people)
            except PeopleConfigurationError:
                recovered, added = merge_detected_people(PeopleConfiguration(), result.people)
                self._people_repository.save(recovered)
                warning = "La configuración de personas dañada se ha reconstruido."
            revision = self._store.replace(result)
            return {
                "ok": True,
                "revision": revision,
                "records": len(result.rows),
                "newPeople": list(added),
                "warning": warning,
            }
        except (ExtractionResultError, ValueError, OSError) as error:
            return {"ok": False, "error": str(error)}

    def get_jira_csv(self) -> dict[str, object]:
        _, result = self._store.snapshot()
        if result is None:
            return {"ok": False, "error": "No existe una extracción disponible."}
        return {
            "ok": True,
            "filename": result_csv_filename(result),
            "contents": base64.b64encode(export_result_csv(result)).decode("ascii"),
        }

    def open_aplantillado(self) -> dict[str, object]:
        _, result = self._store.snapshot()
        if result is None:
            return {"ok": False, "error": "No existe una extracción disponible."}
        self._open_analysis()
        return {"ok": True}


__all__ = ["ExtractionStore", "JiraHostApi"]
