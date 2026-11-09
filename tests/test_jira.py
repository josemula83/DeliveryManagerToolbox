from datetime import date
import json
from urllib.parse import unquote

import pytest

from delivery_manager_toolbox.core.registry import discover_modules
from delivery_manager_toolbox.modules.jira import MODULE
from delivery_manager_toolbox.modules.jira.bookmarklet import (
    build_bookmarklet,
    build_installer_html,
    load_extractor_source,
    render_extractor_source,
)
from delivery_manager_toolbox.modules.jira.constants import (
    BOOKMARKLET_NAME,
    DATE_COLUMN_INDEX,
    DEFAULT_PROJECTS,
    JIRA_DASHBOARD_URL,
    MODAL_ID,
    REQUEST_BODY,
    REQUEST_HEADERS,
    TIMESHEET_ENDPOINT,
)
from delivery_manager_toolbox.modules.jira.models import (
    DateBlock,
    ExtractionProgress,
    QueryFailure,
)
from delivery_manager_toolbox.modules.jira.service import (
    CSV_PREFIX,
    JiraExtractionValidationError,
    build_request_plan,
    build_request_spec,
    consolidate_timesheets,
    default_dates,
    export_filename,
    parse_timesheet_response,
    serialize_csv,
    split_monthly_range,
    validate_selection,
)


def synthetic_row(period: object) -> list[object]:
    row: list[object] = [f"value-{index}" for index in range(DATE_COLUMN_INDEX + 1)]
    row[DATE_COLUMN_INDEX] = period
    return row


def test_default_projects_match_the_required_order() -> None:
    assert DEFAULT_PROJECTS == (
        "BBVACS",
        "BBVAALPHA",
        "BBVAVALORE",
        "BBVAGDV",
        "BBVACIB",
        "BBVACIBGM",
    )


def test_default_dates_cover_current_month_to_today() -> None:
    assert default_dates(date(2026, 11, 9)) == (date(2026, 11, 1), date(2026, 11, 9))


def test_single_month_range_is_one_block() -> None:
    assert split_monthly_range(date(2026, 1, 5), date(2026, 1, 20)) == (
        DateBlock(date(2026, 1, 5), date(2026, 1, 20)),
    )


def test_range_is_split_by_natural_months() -> None:
    assert split_monthly_range(date(2026, 1, 15), date(2026, 3, 10)) == (
        DateBlock(date(2026, 1, 15), date(2026, 1, 31)),
        DateBlock(date(2026, 2, 1), date(2026, 2, 28)),
        DateBlock(date(2026, 3, 1), date(2026, 3, 10)),
    )


def test_range_handles_year_change() -> None:
    assert split_monthly_range(date(2026, 12, 20), date(2027, 1, 4)) == (
        DateBlock(date(2026, 12, 20), date(2026, 12, 31)),
        DateBlock(date(2027, 1, 1), date(2027, 1, 4)),
    )


def test_range_handles_leap_year() -> None:
    assert split_monthly_range(date(2028, 2, 1), date(2028, 3, 2))[0] == DateBlock(
        date(2028, 2, 1), date(2028, 2, 29)
    )


def test_invalid_range_is_rejected() -> None:
    with pytest.raises(JiraExtractionValidationError, match="fecha inicial"):
        split_monthly_range(date(2026, 2, 1), date(2026, 1, 1))


def test_empty_and_unknown_project_selections_are_rejected() -> None:
    with pytest.raises(JiraExtractionValidationError, match="al menos un proyecto"):
        validate_selection(date(2026, 1, 1), date(2026, 1, 2), ())
    with pytest.raises(JiraExtractionValidationError, match="no permitido"):
        validate_selection(date(2026, 1, 1), date(2026, 1, 2), ("UNKNOWN",))


def test_request_spec_contains_endpoint_headers_and_empty_users_body() -> None:
    block = DateBlock(date(2026, 1, 1), date(2026, 1, 31))
    request = build_request_spec("BBVACS", block)

    assert request.url == (
        f"{TIMESHEET_ENDPOINT}?pkey=BBVACS&startDate=2026-01-01&endDate=2026-01-31"
    )
    assert request.method == "POST"
    assert request.headers == REQUEST_HEADERS
    assert request.body == REQUEST_BODY


def test_request_plan_preserves_project_then_chronological_block_order() -> None:
    plan = build_request_plan(
        date(2026, 1, 15),
        date(2026, 2, 2),
        ("BBVACS", "BBVAALPHA"),
    )

    assert [(item.project, item.date_block.start) for item in plan] == [
        ("BBVACS", date(2026, 1, 15)),
        ("BBVACS", date(2026, 2, 1)),
        ("BBVAALPHA", date(2026, 1, 15)),
        ("BBVAALPHA", date(2026, 2, 1)),
    ]


def test_response_uses_first_row_as_header_and_adds_period_columns() -> None:
    header = [f"column-{index}" for index in range(DATE_COLUMN_INDEX + 1)]
    report = parse_timesheet_response(
        "BBVACS",
        {"data": [header, synthetic_row("2026-04-18")]},
    )

    assert report.header == (*CSV_PREFIX, *header)
    assert report.rows[0][:4] == ("BBVACS", "2026", "04", "2026-04")
    assert report.rows[0][4:] == tuple(synthetic_row("2026-04-18"))


def test_invalid_period_value_leaves_derived_columns_empty() -> None:
    header = ["column"] * (DATE_COLUMN_INDEX + 1)
    report = parse_timesheet_response("BBVACS", {"data": [header, synthetic_row("April")]})
    assert report.rows[0][:4] == ("BBVACS", "", "", "")


def test_missing_or_invalid_header_disables_a_report() -> None:
    with pytest.raises(JiraExtractionValidationError, match="cabecera válida"):
        parse_timesheet_response("BBVACS", {"data": []})
    with pytest.raises(JiraExtractionValidationError, match="Respuesta sin datos"):
        parse_timesheet_response("BBVACS", {"result": []})


def test_successful_reports_are_consolidated_after_an_independent_failure() -> None:
    header = ["column"] * (DATE_COLUMN_INDEX + 1)
    first = parse_timesheet_response("BBVACS", {"data": [header, synthetic_row("2026-01")]})
    failure = QueryFailure(
        "BBVAALPHA",
        DateBlock(date(2026, 1, 1), date(2026, 1, 31)),
        "HTTP 500",
    )
    second = parse_timesheet_response("BBVAGDV", {"data": [header, synthetic_row("2026-02")]})

    consolidated = consolidate_timesheets((first, second))

    assert consolidated is not None
    assert len(consolidated.rows) == 2
    assert consolidated.rows[0][0] == "BBVACS"
    assert consolidated.rows[1][0] == "BBVAGDV"
    assert failure.display_message == "BBVAALPHA · 2026-01-01 → 2026-01-31 · HTTP 500"


def test_incompatible_response_headers_are_rejected() -> None:
    first = parse_timesheet_response("BBVACS", {"data": [["A"], ["value"]]})
    second = parse_timesheet_response("BBVAALPHA", {"data": [["B"], ["value"]]})
    with pytest.raises(JiraExtractionValidationError, match="cabeceras incompatibles"):
        consolidate_timesheets((first, second))


def test_progress_percentage_uses_completed_over_total() -> None:
    assert ExtractionProgress(3, 8, 25, 1).percentage == 38
    assert ExtractionProgress(0, 0, 0, 0).percentage == 0


def test_csv_uses_bom_semicolon_quotes_and_escaped_quotes() -> None:
    report = parse_timesheet_response(
        "BBVACS",
        {"data": [["Descripción"], ['Texto "citado"']]},
    )

    contents = serialize_csv(report)

    assert contents.startswith(b"\xef\xbb\xbf")
    text = contents.decode("utf-8-sig")
    assert text.startswith('"PKEY";"Año";"Mes";"Año-Mes";"Descripción"\n')
    assert '"Texto ""citado"""' in text


def test_export_filename_contains_requested_dates() -> None:
    assert export_filename(date(2026, 1, 1), date(2026, 1, 31)) == (
        "Incurridos_BBVA_2026-01-01_2026-01-31.csv"
    )


def test_bookmarklet_is_generated_from_versioned_readable_source() -> None:
    source = load_extractor_source()
    rendered = render_extractor_source()
    bookmarklet = build_bookmarklet()

    assert "__DMT_CONFIG__" in source
    assert "__DMT_CONFIG__" not in rendered
    assert bookmarklet.startswith("javascript:")
    assert "\n" not in bookmarklet
    assert unquote(bookmarklet.removeprefix("javascript:")) == rendered
    assert json.dumps(list(DEFAULT_PROJECTS), ensure_ascii=False, separators=(",", ":")) in rendered


def test_extractor_removes_previous_modal_and_renders_errors_as_text() -> None:
    source = render_extractor_source()

    assert f'getElementById(CONFIG.modalId)' in source
    assert "previousModal.remove()" in source
    assert "textContent" in source
    assert "innerHTML" not in source
    assert "URL.revokeObjectURL" in source
    assert "credentials: \"same-origin\"" in source
    assert MODAL_ID in source


def test_download_is_only_enabled_after_a_valid_header() -> None:
    source = render_extractor_source()
    no_header_check = source.index("if (!header)")
    enable_download = source.index('downloadButton.style.display = "inline-block"')

    assert no_header_check < enable_download
    assert TIMESHEET_ENDPOINT in source


def test_installer_contains_draggable_bookmarklet_without_credentials() -> None:
    installer = build_installer_html()

    assert BOOKMARKLET_NAME in installer
    assert "javascript:" in installer
    assert "cookie" not in installer.casefold()
    assert "password" not in installer.casefold()


def test_jira_module_is_available_in_main_navigation() -> None:
    discovered = {module.key: module for module in discover_modules()}
    assert discovered["jira"] == MODULE
    assert discovered["jira"].title == "JIRA · Incurridos"
    assert JIRA_DASHBOARD_URL.startswith("https://umane.emeal.nttdata.com/")
