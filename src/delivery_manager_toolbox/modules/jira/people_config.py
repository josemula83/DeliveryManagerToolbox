"""Configuración persistente y versionada de personas de Aplantillado."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, Mapping

from delivery_manager_toolbox.modules.jira.extraction_result import normalize_name


PEOPLE_CONFIG_VERSION = 1


class PeopleConfigurationError(ValueError):
    """Configuración de personas no válida."""


@dataclass(frozen=True, slots=True)
class ConfiguredPerson:
    name: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class PeopleConfiguration:
    people: tuple[ConfiguredPerson, ...] = ()
    version: int = PEOPLE_CONFIG_VERSION

    @property
    def enabled_names(self) -> tuple[str, ...]:
        return tuple(person.name for person in self.people if person.enabled)


def merge_detected_people(
    configuration: PeopleConfiguration,
    detected_names: Iterable[str],
) -> tuple[PeopleConfiguration, tuple[str, ...]]:
    people = list(configuration.people)
    known = {normalize_name(person.name) for person in people}
    added: list[str] = []
    for raw_name in detected_names:
        name = " ".join(str(raw_name).split())
        key = normalize_name(name)
        if key and key not in known:
            people.append(ConfiguredPerson(name, True))
            known.add(key)
            added.append(name)
    return PeopleConfiguration(tuple(people)), tuple(added)


def restore_detected_people(
    configuration: PeopleConfiguration,
    detected_names: Iterable[str],
) -> PeopleConfiguration:
    names = tuple(detected_names)
    detected = {normalize_name(name) for name in names if normalize_name(name)}
    restored = tuple(
        ConfiguredPerson(person.name, True if normalize_name(person.name) in detected else person.enabled)
        for person in configuration.people
    )
    merged, _ = merge_detected_people(PeopleConfiguration(restored), names)
    return merged


def set_person_enabled(
    configuration: PeopleConfiguration,
    name: str,
    enabled: bool,
) -> PeopleConfiguration:
    key = normalize_name(name)
    if not key:
        raise PeopleConfigurationError("El nombre no puede estar vacío.")
    found = False
    people: list[ConfiguredPerson] = []
    for person in configuration.people:
        if normalize_name(person.name) == key:
            people.append(ConfiguredPerson(person.name, enabled))
            found = True
        else:
            people.append(person)
    if not found:
        raise PeopleConfigurationError("La persona no existe en la configuración.")
    return PeopleConfiguration(tuple(people))


def parse_people_configuration(value: object) -> PeopleConfiguration:
    if not isinstance(value, Mapping):
        raise PeopleConfigurationError("La configuración debe ser un objeto JSON.")
    if value.get("version") != PEOPLE_CONFIG_VERSION:
        raise PeopleConfigurationError("Versión de configuración no soportada.")
    raw_people = value.get("people")
    if not isinstance(raw_people, list):
        raise PeopleConfigurationError("La configuración no contiene una lista de personas.")
    people: list[ConfiguredPerson] = []
    known: set[str] = set()
    for raw_person in raw_people:
        if not isinstance(raw_person, Mapping):
            raise PeopleConfigurationError("Cada persona debe ser un objeto.")
        raw_name = raw_person.get("name")
        if not isinstance(raw_name, str):
            raise PeopleConfigurationError("Los nombres deben ser texto.")
        name = " ".join(raw_name.split())
        key = normalize_name(name)
        if not key:
            raise PeopleConfigurationError("Los nombres no pueden estar vacíos.")
        if key in known:
            raise PeopleConfigurationError("La configuración contiene personas duplicadas.")
        enabled = raw_person.get("enabled")
        if not isinstance(enabled, bool):
            raise PeopleConfigurationError("El estado enabled debe ser booleano.")
        people.append(ConfiguredPerson(name, enabled))
        known.add(key)
    return PeopleConfiguration(tuple(people))


def loads_people_configuration(contents: str | bytes) -> PeopleConfiguration:
    try:
        return parse_people_configuration(json.loads(contents))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PeopleConfigurationError("El JSON de personas no es válido.") from error


def dumps_people_configuration(configuration: PeopleConfiguration) -> bytes:
    payload = {
        "version": PEOPLE_CONFIG_VERSION,
        "people": [
            {"name": person.name, "enabled": person.enabled}
            for person in configuration.people
        ],
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class PeopleConfigurationRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> PeopleConfiguration:
        if not self.path.exists():
            return PeopleConfiguration()
        return loads_people_configuration(self.path.read_bytes())

    def save(self, configuration: PeopleConfiguration) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes(dumps_people_configuration(configuration))
        temporary.replace(self.path)

    def merge_and_save(self, detected_names: Iterable[str]) -> tuple[PeopleConfiguration, tuple[str, ...]]:
        configuration, added = merge_detected_people(self.load(), detected_names)
        if added:
            self.save(configuration)
        return configuration, added


__all__ = [
    "ConfiguredPerson",
    "PEOPLE_CONFIG_VERSION",
    "PeopleConfiguration",
    "PeopleConfigurationError",
    "PeopleConfigurationRepository",
    "dumps_people_configuration",
    "loads_people_configuration",
    "merge_detected_people",
    "parse_people_configuration",
    "restore_detected_people",
    "set_person_enabled",
]
