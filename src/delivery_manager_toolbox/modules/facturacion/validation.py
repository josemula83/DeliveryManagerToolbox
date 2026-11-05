"""Validaciones de dominio independientes de la interfaz."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable


class ConfigurationValidationError(ValueError):
    """La configuración de Facturación incumple una regla de dominio."""


def clean_display_value(value: object, field_name: str) -> str:
    """Limpia espacios conservando mayúsculas y acentos para presentación."""
    if value is None:
        raise ConfigurationValidationError(f"{field_name} no puede estar vacío.")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    cleaned = re.sub(r"\s+", " ", str(value).strip())
    if not cleaned:
        raise ConfigurationValidationError(f"{field_name} no puede estar vacío.")
    return cleaned


def normalize_identifier(value: object) -> str:
    """Normaliza espacios, mayúsculas y acentos para comparaciones internas."""
    cleaned = clean_display_value(value, "El identificador")
    decomposed = unicodedata.normalize("NFKD", cleaned.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def validate_percentage(value: object) -> float:
    percentage = _finite_number(value, "El porcentaje")
    if percentage < 0 or percentage > 100:
        raise ConfigurationValidationError("El porcentaje debe estar entre 0 y 100.")
    return percentage


def validate_fixed_hours(value: object) -> float:
    hours = _finite_number(value, "Las horas de gestión")
    if hours < 0:
        raise ConfigurationValidationError("Las horas de gestión no pueden ser negativas.")
    if not math.isclose(hours * 2, round(hours * 2), abs_tol=1e-9):
        raise ConfigurationValidationError("Las horas de gestión deben usar incrementos de 0,5 h.")
    return hours


def validate_management_rate(value: object) -> float:
    rate = _finite_number(value, "La tarifa de gestión")
    if rate < 0:
        raise ConfigurationValidationError("La tarifa de gestión no puede ser negativa.")
    return rate


def validate_unique(values: Iterable[object], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        key = normalize_identifier(value)
        if key in seen:
            raise ConfigurationValidationError(f"Hay un registro duplicado en {label}: {value}.")
        seen.add(key)


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise ConfigurationValidationError(f"{field_name} debe ser numérico.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationValidationError(f"{field_name} debe ser numérico.") from exc
    if not math.isfinite(number):
        raise ConfigurationValidationError(f"{field_name} debe ser un número finito.")
    return number

