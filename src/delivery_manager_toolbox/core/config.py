"""Carga tipada de configuración no sensible."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.toml"


@dataclass(frozen=True, slots=True)
class ApplicationSettings:
    name: str
    subtitle: str


@dataclass(frozen=True, slots=True)
class LoggingSettings:
    level: str
    file: Path


@dataclass(frozen=True, slots=True)
class JiraSettings:
    people_file: Path


@dataclass(frozen=True, slots=True)
class People360Settings:
    source: str
    sharepoint_site: str
    sharepoint_folder: str
    file_name: str
    local_file: Path | None
    alert_threshold_days: int
    priority_medium_min: float
    priority_ok_above: float
    export_file_name: str


@dataclass(frozen=True, slots=True)
class Settings:
    application: ApplicationSettings
    logging: LoggingSettings
    jira: JiraSettings
    people360: People360Settings


def load_settings(path: Path | None = None) -> Settings:
    """Carga la configuración desde TOML y resuelve rutas desde el proyecto."""
    configured_path = os.getenv("DMT_CONFIG_PATH")
    config_path = path or (Path(configured_path) if configured_path else DEFAULT_CONFIG_PATH)

    with config_path.open("rb") as config_file:
        raw = tomllib.load(config_file)

    app = raw["application"]
    logging = raw["logging"]
    jira = raw["jira"]
    people360 = raw["people360"]
    log_path = Path(logging["file"])
    if not log_path.is_absolute():
        log_path = PROJECT_ROOT / log_path
    people_path = Path(jira["people_file"])
    if not people_path.is_absolute():
        people_path = PROJECT_ROOT / people_path
    local_file_value = os.getenv("PEOPLE360_LOCAL_FILE", people360.get("local_file", "")).strip()
    local_file = Path(local_file_value) if local_file_value else None
    if local_file is not None and not local_file.is_absolute():
        local_file = PROJECT_ROOT / local_file

    return Settings(
        application=ApplicationSettings(name=app["name"], subtitle=app["subtitle"]),
        logging=LoggingSettings(level=logging["level"].upper(), file=log_path),
        jira=JiraSettings(people_file=people_path),
        people360=People360Settings(
            source=os.getenv("PEOPLE360_SOURCE", people360["source"]).strip().casefold(),
            sharepoint_site=people360["sharepoint_site"],
            sharepoint_folder=people360["sharepoint_folder"],
            file_name=people360["file_name"],
            local_file=local_file,
            alert_threshold_days=int(people360["alert_threshold_days"]),
            priority_medium_min=float(people360["priority_medium_min"]),
            priority_ok_above=float(people360["priority_ok_above"]),
            export_file_name=people360["export_file_name"],
        ),
    )

