"""Contratos independientes de la interfaz para el informe de incurridos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True, slots=True)
class DateBlock:
    start: date
    end: date


@dataclass(frozen=True, slots=True)
class JiraRequestSpec:
    project: str
    date_block: DateBlock
    url: str
    method: str
    headers: dict[str, str]
    body: dict[str, list[Any]]


@dataclass(frozen=True, slots=True)
class ParsedTimesheet:
    header: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]


@dataclass(frozen=True, slots=True)
class ExtractionProgress:
    completed_queries: int
    total_queries: int
    recovered_records: int
    error_count: int

    @property
    def percentage(self) -> int:
        if self.total_queries <= 0:
            return 0
        return round(self.completed_queries / self.total_queries * 100)


@dataclass(frozen=True, slots=True)
class QueryFailure:
    project: str
    date_block: DateBlock
    message: str

    @property
    def display_message(self) -> str:
        return (
            f"{self.project} · {self.date_block.start.isoformat()} → "
            f"{self.date_block.end.isoformat()} · {self.message}"
        )

