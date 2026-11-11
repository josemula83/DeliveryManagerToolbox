"""Acceso delegado de solo lectura a SharePoint mediante Microsoft Graph."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
import json
from pathlib import Path
from typing import Callable, Mapping, Protocol
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

from delivery_manager_toolbox.core.config import People360Settings
from delivery_manager_toolbox.modules.people_360.models import People360Dataset
from delivery_manager_toolbox.modules.people_360.repository import (
    ExcelPeople360Repository,
    LocalPeople360Repository,
    People360Repository,
)


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
ProgressCallback = Callable[[str], None]


class People360SourceError(RuntimeError):
    """Error de fuente traducible a un mensaje seguro de interfaz."""


class AuthenticationConfigurationError(People360SourceError):
    pass


class GraphAuthenticationError(People360SourceError):
    pass


class GraphPermissionError(People360SourceError):
    def __init__(self, required_scope: str):
        self.required_scope = required_scope
        super().__init__(f"Permiso delegado requerido: {required_scope}")


class GraphFileNotFoundError(People360SourceError):
    pass


class GraphTemporaryError(People360SourceError):
    pass


@dataclass(frozen=True, slots=True)
class HttpResult:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


class AccessTokenProvider(Protocol):
    def get_token(self, scope: str) -> str: ...


class HttpTransport(Protocol):
    def get(self, url: str, headers: Mapping[str, str] | None = None) -> HttpResult: ...


class UrlLibHttpTransport:
    """Transporte pequeño para Graph; nunca registra cabeceras ni tokens."""

    def __init__(self, timeout_seconds: int = 30):
        self._timeout_seconds = timeout_seconds

    def get(self, url: str, headers: Mapping[str, str] | None = None) -> HttpResult:
        request = Request(url, headers=dict(headers or {}), method="GET")
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                return HttpResult(response.status, response.read(), dict(response.headers.items()))
        except HTTPError as error:
            return HttpResult(error.code, error.read(), dict(error.headers.items()))
        except (URLError, TimeoutError, OSError) as error:
            raise GraphTemporaryError("No se ha podido conectar temporalmente con Microsoft Graph") from error


class AzureIdentityTokenProvider:
    """Autenticación interactiva con caché cifrada por el sistema operativo."""

    def __init__(self, client_id: str, tenant_id: str):
        if not client_id:
            raise AuthenticationConfigurationError(
                "Falta PEOPLE360_GRAPH_CLIENT_ID para la aplicación pública de Microsoft Entra"
            )
        self._client_id = client_id
        self._tenant_id = tenant_id or "organizations"
        self._credential = None

    def get_token(self, scope: str) -> str:
        try:
            if self._credential is None:
                from azure.identity import InteractiveBrowserCredential, TokenCachePersistenceOptions

                cache = TokenCachePersistenceOptions(
                    name="delivery-manager-toolbox-people360",
                    allow_unencrypted_storage=False,
                )
                self._credential = InteractiveBrowserCredential(
                    client_id=self._client_id,
                    tenant_id=self._tenant_id,
                    cache_persistence_options=cache,
                    timeout=300,
                )
            return self._credential.get_token(scope).token
        except AuthenticationConfigurationError:
            raise
        except Exception as error:
            raise GraphAuthenticationError("No se ha podido completar la autenticación corporativa") from error


class MicrosoftGraphPeople360Client:
    """Localiza site, biblioteca predeterminada y fichero, y devuelve sus bytes."""

    def __init__(
        self,
        token_provider: AccessTokenProvider,
        transport: HttpTransport,
        scope: str,
        progress: ProgressCallback | None = None,
    ):
        self._token_provider = token_provider
        self._transport = transport
        self._scope = scope
        self._progress = progress or (lambda _message: None)

    def download_file(
        self, hostname: str, site_path: str, library: str, folder: str, file_name: str
    ) -> bytes:
        self._progress("Autenticando con Microsoft 365...")
        token = self._token_provider.get_token(self._scope)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        self._progress("Buscando el sitio de SharePoint...")
        encoded_host = quote(hostname, safe=".")
        encoded_site = quote(site_path.strip("/"), safe="/")
        site = self._get_json(f"{GRAPH_ROOT}/sites/{encoded_host}:/{encoded_site}?$select=id", headers, "sitio")

        self._progress("Localizando la biblioteca de documentos...")
        site_id = quote(str(site.get("id", "")), safe=",")
        drives = self._get_json(
            f"{GRAPH_ROOT}/sites/{site_id}/drives?$select=id,name,webUrl", headers, "biblioteca"
        )
        drive = next(
            (
                item for item in drives.get("value", [])
                if isinstance(item, dict) and _drive_matches(item, library)
            ),
            None,
        )
        if drive is None:
            raise GraphFileNotFoundError("No se ha encontrado la biblioteca configurada en SharePoint")

        self._progress("Buscando empleados.xlsx...")
        drive_id = quote(str(drive.get("id", "")), safe="")
        item_path = "/".join(part.strip("/") for part in (folder, file_name) if part.strip("/"))
        encoded_item = quote(item_path, safe="/")
        item = self._get_json(
            f"{GRAPH_ROOT}/drives/{drive_id}/root:/{encoded_item}",
            headers,
            "fichero",
        )
        download_url = item.get("@microsoft.graph.downloadUrl")
        if not isinstance(download_url, str) or not download_url:
            raise GraphFileNotFoundError("Graph no devolvió una descarga para el fichero configurado")

        self._progress("Descargando y procesando el fichero...")
        # La URL es preautenticada; no se reenvía el token Graph al host de descarga.
        result = self._transport.get(download_url)
        self._raise_for_status(result.status_code, "descarga")
        return result.body

    def _get_json(self, url: str, headers: Mapping[str, str], resource: str) -> dict:
        result = self._transport.get(url, headers)
        self._raise_for_status(result.status_code, resource)
        try:
            value = json.loads(result.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GraphTemporaryError("Microsoft Graph devolvió una respuesta no válida") from error
        if not isinstance(value, dict):
            raise GraphTemporaryError("Microsoft Graph devolvió una respuesta inesperada")
        return value

    def _raise_for_status(self, status_code: int, resource: str) -> None:
        if 200 <= status_code < 300:
            return
        if status_code == 401:
            raise GraphAuthenticationError("La sesión Microsoft 365 no es válida o ha caducado")
        if status_code == 403:
            raise GraphPermissionError(self._scope)
        if status_code == 404:
            raise GraphFileNotFoundError(f"No se ha encontrado el {resource} configurado en SharePoint")
        if status_code == 429 or status_code >= 500:
            raise GraphTemporaryError("Microsoft Graph no está disponible temporalmente")
        raise People360SourceError(f"Microsoft Graph devolvió el estado HTTP {status_code}")


class SharePointPeople360Repository:
    def __init__(
        self,
        client: MicrosoftGraphPeople360Client,
        hostname: str,
        site_path: str,
        library: str,
        folder: str,
        file_name: str,
    ):
        self._client = client
        self._hostname = hostname
        self._site_path = site_path
        self._library = library
        self._folder = folder
        self._file_name = file_name

    def load(self) -> People360Dataset:
        payload = self._client.download_file(
            self._hostname, self._site_path, self._library, self._folder, self._file_name
        )
        return ExcelPeople360Repository(BytesIO(payload), self._file_name).load()


@lru_cache(maxsize=8)
def _token_provider(client_id: str, tenant_id: str) -> AzureIdentityTokenProvider:
    return AzureIdentityTokenProvider(client_id, tenant_id)


def create_people360_repository(
    settings: People360Settings,
    progress: ProgressCallback | None = None,
    graph_client: MicrosoftGraphPeople360Client | None = None,
) -> People360Repository:
    """Selecciona SharePoint por defecto; local solo mediante configuración explícita."""
    if settings.source == "local":
        if settings.local_file is None:
            raise AuthenticationConfigurationError("PEOPLE360_LOCAL_FILE no está configurado")
        return LocalPeople360Repository(Path(settings.local_file))
    if settings.source != "sharepoint":
        raise AuthenticationConfigurationError(f"Fuente People 360 no soportada: {settings.source}")

    client = graph_client or MicrosoftGraphPeople360Client(
        _token_provider(settings.graph_client_id, settings.graph_tenant_id),
        UrlLibHttpTransport(),
        settings.graph_scope,
        progress,
    )
    return SharePointPeople360Repository(
        client,
        settings.sharepoint_hostname,
        settings.sharepoint_site_path,
        settings.sharepoint_library,
        settings.sharepoint_folder,
        settings.file_name,
    )


def authenticate_people360(
    settings: People360Settings,
    token_provider: AccessTokenProvider | None = None,
) -> None:
    """Obtiene el consentimiento delegado antes de permitir el acceso al fichero."""
    if settings.source == "local":
        return
    if settings.source != "sharepoint":
        raise AuthenticationConfigurationError(f"Fuente People 360 no soportada: {settings.source}")
    provider = token_provider or _token_provider(
        settings.graph_client_id, settings.graph_tenant_id
    )
    provider.get_token(settings.graph_scope)


def _normalize(value: object) -> str:
    text = " ".join(str(value or "").strip().casefold().split())
    return "".join(
        character for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )


def _drive_matches(item: dict, library: str) -> bool:
    if _normalize(item.get("name")) == _normalize(library):
        return True
    web_url = item.get("webUrl")
    if not isinstance(web_url, str):
        return False
    path_name = unquote(urlparse(web_url).path.rstrip("/").rsplit("/", 1)[-1])
    return _normalize(path_name) == _normalize(library)
