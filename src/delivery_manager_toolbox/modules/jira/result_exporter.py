"""Exportación bajo demanda del resultado normalizado de JIRA."""

from __future__ import annotations

from delivery_manager_toolbox.modules.jira.extraction_result import ExtractionResult
from delivery_manager_toolbox.modules.jira.models import ParsedTimesheet
from delivery_manager_toolbox.modules.jira.service import export_filename, serialize_csv


def export_result_csv(result: ExtractionResult) -> bytes:
    report = ParsedTimesheet(
        header=result.header,
        rows=tuple(row.original_values for row in result.rows),
    )
    return serialize_csv(report)


def result_csv_filename(result: ExtractionResult) -> str:
    return export_filename(result.date_from, result.date_to)


__all__ = ["export_result_csv", "result_csv_filename"]
