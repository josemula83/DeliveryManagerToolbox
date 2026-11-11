"""Análisis puro de imputaciones agrupadas por persona y fecha."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Iterable, Mapping

from delivery_manager_toolbox.modules.jira.extraction_result import (
    ExtractionResult,
    ExtractionRow,
    normalize_name,
)


class DayCategory(StrEnum):
    VACATION = "vacation"
    FESTIVE = "festive"
    COMPENSATED = "compensated"
    STAFF = "staff"
    OTHER = "other"
    MISSING = "missing"
    WEEKEND = "weekend"


@dataclass(frozen=True, slots=True)
class PersonMetrics:
    total_hours: Decimal
    vacation_days: int
    festive_days: int
    compensated_days: int
    staff_days: int
    other_days: int
    missing_days: int


@dataclass(frozen=True, slots=True)
class PersonAnalysis:
    person: str
    year: int
    entries_by_date: Mapping[date, tuple[ExtractionRow, ...]]
    categories_by_date: Mapping[date, DayCategory]
    metrics: PersonMetrics


def group_rows_by_person(
    result: ExtractionResult,
    active_names: Iterable[str],
) -> dict[str, tuple[ExtractionRow, ...]]:
    configured = {normalize_name(name): " ".join(name.split()) for name in active_names}
    grouped: dict[str, list[ExtractionRow]] = {name: [] for name in configured.values()}
    for row in result.rows:
        display_name = configured.get(normalize_name(row.person))
        if display_name is not None:
            grouped[display_name].append(row)
    return {name: tuple(rows) for name, rows in grouped.items()}


def group_rows_by_date(rows: Iterable[ExtractionRow]) -> dict[date, tuple[ExtractionRow, ...]]:
    grouped: dict[date, list[ExtractionRow]] = {}
    for row in rows:
        if row.work_date is not None:
            grouped.setdefault(row.work_date, []).append(row)
    return {work_date: tuple(values) for work_date, values in grouped.items()}


def is_missing_workday(work_date: date, entries: Iterable[ExtractionRow]) -> bool:
    return work_date.weekday() < 5 and not tuple(entries)


def classify_day(work_date: date, entries: Iterable[ExtractionRow]) -> DayCategory:
    values = tuple(entries)
    if not values:
        return DayCategory.MISSING if is_missing_workday(work_date, values) else DayCategory.WEEKEND
    tasks = tuple(row.task.casefold() for row in values)
    if any("vacation" in task or "vacaciones" in task for task in tasks):
        return DayCategory.VACATION
    if any("festive" in task or "festivo" in task for task in tasks):
        return DayCategory.FESTIVE
    if any("compensated" in task or "compensado" in task for task in tasks):
        return DayCategory.COMPENSATED
    if any("staff" in task or "augmentation" in task for task in tasks):
        return DayCategory.STAFF
    return DayCategory.OTHER


def analyse_person(result: ExtractionResult, person: str, year: int) -> PersonAnalysis:
    rows = tuple(
        row
        for row in result.rows
        if normalize_name(row.person) == normalize_name(person)
        and row.work_date is not None
        and row.work_date.year == year
    )
    entries_by_date = group_rows_by_date(rows)
    categories: dict[date, DayCategory] = {}
    totals = {category: 0 for category in DayCategory}
    total_hours = Decimal("0")
    for month in range(1, 13):
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            work_date = date(year, month, day)
            entries = entries_by_date.get(work_date, ())
            category = classify_day(work_date, entries)
            categories[work_date] = category
            totals[category] += 1
            total_hours += sum(
                (row.hours for row in entries if row.hours is not None),
                Decimal("0"),
            )
    metrics = PersonMetrics(
        total_hours=total_hours,
        vacation_days=totals[DayCategory.VACATION],
        festive_days=totals[DayCategory.FESTIVE],
        compensated_days=totals[DayCategory.COMPENSATED],
        staff_days=totals[DayCategory.STAFF],
        other_days=totals[DayCategory.OTHER],
        missing_days=totals[DayCategory.MISSING],
    )
    return PersonAnalysis(person, year, entries_by_date, categories, metrics)


def category_dates(analysis: PersonAnalysis, category: DayCategory) -> tuple[date, ...]:
    return tuple(
        work_date
        for work_date, current in analysis.categories_by_date.items()
        if current is category
    )


__all__ = [
    "DayCategory",
    "PersonAnalysis",
    "PersonMetrics",
    "analyse_person",
    "category_dates",
    "classify_day",
    "group_rows_by_date",
    "group_rows_by_person",
    "is_missing_workday",
]
