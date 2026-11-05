"""Contratos mínimos compartidos por los módulos."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModuleDefinition:
    key: str
    title: str
    description: str
    order: int
    render: Callable[[], None]

