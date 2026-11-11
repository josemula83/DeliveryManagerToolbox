from datetime import date
import json
from pathlib import Path
from urllib.error import URLError

import pytest

from delivery_manager_toolbox.core.registry import discover_modules
from delivery_manager_toolbox.desktop import JiraWindowController
from delivery_manager_toolbox.modules.jira import MODULE
from delivery_manager_toolbox.modules.jira.browser import (
    JiraBrowserState,
    build_browser_configuration,
    decide_navigation,
)
from delivery_manager_toolbox.modules.jira.desktop_bridge import (
    BRIDGE_TOKEN_ENV,
    BRIDGE_URL_ENV,
    acknowledge_analysis_request,
    request_jira_extraction,
    request_jira_snapshot,
    request_jira_window,
)
from delivery_manager_toolbox.modules.jira.extractor import (
    build_injection_script,
    load_extractor_source,
    render_extractor_source,
)
from delivery_manager_toolbox.modules.jira.constants import (
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


def test_extractor_is_generated_from_versioned_readable_source() -> None:
    source = load_extractor_source()
    rendered = render_extractor_source()

    assert "__DMT_CONFIG__" in source
    assert "__DMT_CONFIG__" not in rendered
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


def test_extractor_transfers_one_result_to_csv_and_aplantillado_actions() -> None:
    source = render_extractor_source()

    assert 'type: "jira_extraction_completed"' in source
    assert "receive_jira_extraction" in source
    assert 'text: "Descargar CSV"' in source
    assert 'text: "Aplantillado"' in source
    assert "get_jira_csv" in source
    assert "open_aplantillado" in source
    assert "clear_jira_extraction" in source


def test_injection_script_prevents_a_duplicate_modal() -> None:
    script = build_injection_script()

    assert f'document.getElementById("{MODAL_ID}")' in script
    assert "return;" in script
    assert "__DMT_CONFIG__" not in script
    assert TIMESHEET_ENDPOINT in script


@pytest.mark.parametrize(
    ("url", "state", "should_inject"),
    [
        (None, JiraBrowserState.LOADING, False),
        ("about:blank", JiraBrowserState.LOADING, False),
        (
            "https://umane.emeal.nttdata.com/jiraito/login.jsp",
            JiraBrowserState.LOGIN_REQUIRED,
            False,
        ),
        (
            "https://login.example.test/sso",
            JiraBrowserState.LOGIN_IN_PROGRESS,
            False,
        ),
        (JIRA_DASHBOARD_URL, JiraBrowserState.AUTHENTICATED, True),
        ("javascript:alert(1)", JiraBrowserState.ERROR, False),
    ],
)
def test_navigation_decisions(
    url: str | None,
    state: JiraBrowserState,
    should_inject: bool,
) -> None:
    decision = decide_navigation(url)
    assert decision.state is state
    assert decision.should_inject is should_inject


def test_browser_configuration_uses_persistent_edge_profile_outside_repository() -> None:
    configuration = build_browser_configuration({"LOCALAPPDATA": "C:/Users/test/AppData/Local"})

    assert configuration.renderer == "edgechromium"
    assert configuration.persistent_session is True
    assert configuration.allow_downloads is True
    assert configuration.profile_path == Path(
        "C:/Users/test/AppData/Local/DeliveryManagerToolbox/WebView2"
    )
    assert "DeliveryMAnagerToolBox" not in str(configuration.profile_path)


class _SyntheticResponse:
    def read(self) -> bytes:
        return b'{"state":"extractor_injected"}'


def test_bridge_requests_the_integrated_window_with_ephemeral_authorization() -> None:
    captured: dict[str, object] = {}

    def opener(request: object, timeout: int) -> _SyntheticResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return _SyntheticResponse()

    status = request_jira_window(
        {BRIDGE_URL_ENV: "http://127.0.0.1:12345", BRIDGE_TOKEN_ENV: "synthetic-token"},
        opener=opener,
    )

    request = captured["request"]
    assert status.available is True
    assert status.state is JiraBrowserState.EXTRACTOR_INJECTED
    assert request.full_url == "http://127.0.0.1:12345/jira/open"
    assert request.get_header("Authorization") == "Bearer synthetic-token"
    assert captured["timeout"] == 2


def test_bridge_reports_unavailable_without_desktop_or_on_connection_error() -> None:
    assert request_jira_window({}).available is False

    def failing_opener(request: object, timeout: int) -> object:
        raise URLError("synthetic failure")

    status = request_jira_window(
        {BRIDGE_URL_ENV: "http://127.0.0.1:12345", BRIDGE_TOKEN_ENV: "synthetic-token"},
        opener=failing_opener,
    )
    assert status.available is False
    assert status.state is JiraBrowserState.ERROR


class _PayloadResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_bridge_reads_status_extraction_and_acknowledges_analysis() -> None:
    environment = {
        BRIDGE_URL_ENV: "http://127.0.0.1:12345",
        BRIDGE_TOKEN_ENV: "synthetic-token",
    }
    calls: list[tuple[str, str]] = []

    def opener(request: object, timeout: int) -> _PayloadResponse:
        calls.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/jira/status"):
            return _PayloadResponse(
                {
                    "state": "extractor_injected",
                    "revision": 3,
                    "hasExtraction": True,
                    "analysisRequested": True,
                }
            )
        if request.full_url.endswith("/jira/extraction"):
            return _PayloadResponse({"from": "2026-01-01", "rows": []})
        return _PayloadResponse({"ok": True})

    snapshot = request_jira_snapshot(environment, opener)
    extraction = request_jira_extraction(environment, opener)
    acknowledged = acknowledge_analysis_request(environment, opener)

    assert snapshot.available is True
    assert snapshot.revision == 3
    assert snapshot.has_extraction is True
    assert snapshot.analysis_requested is True
    assert extraction == {"from": "2026-01-01", "rows": []}
    assert acknowledged is True
    assert calls == [
        ("GET", "http://127.0.0.1:12345/jira/status"),
        ("GET", "http://127.0.0.1:12345/jira/extraction"),
        ("POST", "http://127.0.0.1:12345/jira/analysis/ack"),
    ]


class _SyntheticWindow:
    def __init__(self, url: str) -> None:
        self.url = url
        self.scripts: list[str] = []
        self.was_shown = False
        self.was_restored = False
        self.was_hidden = False
        self.was_destroyed = False

    def get_current_url(self) -> str:
        return self.url

    def run_js(self, script: str) -> None:
        self.scripts.append(script)

    def show(self) -> None:
        self.was_shown = True

    def restore(self) -> None:
        self.was_restored = True

    def hide(self) -> None:
        self.was_hidden = True

    def destroy(self) -> None:
        self.was_destroyed = True


def test_controller_injects_only_after_authenticated_jira_navigation() -> None:
    window = _SyntheticWindow("https://login.example.test/sso")
    controller = JiraWindowController()
    controller.attach(window)

    controller.on_loaded()
    assert controller.state is JiraBrowserState.LOGIN_IN_PROGRESS
    assert window.scripts == []

    window.url = JIRA_DASHBOARD_URL
    controller.on_loaded()
    assert controller.state is JiraBrowserState.EXTRACTOR_INJECTED
    assert len(window.scripts) == 1


def test_controller_reuses_window_and_closes_it_with_main_application() -> None:
    window = _SyntheticWindow(JIRA_DASHBOARD_URL)
    controller = JiraWindowController()
    controller.attach(window)

    assert controller.open() is JiraBrowserState.LOADING
    assert window.was_shown is True
    assert window.was_restored is True
    assert controller.on_closing() is False
    assert window.was_hidden is True

    controller.shutdown()
    assert window.was_destroyed is True
    assert controller.on_closing() is True


def test_controller_focuses_toolbox_when_aplantillado_is_requested() -> None:
    jira_window = _SyntheticWindow(JIRA_DASHBOARD_URL)
    main_window = _SyntheticWindow("http://127.0.0.1:8501")
    controller = JiraWindowController()
    controller.attach(jira_window)
    controller.attach_main(main_window)

    controller.open_analysis()

    assert controller.analysis_requested() is True
    assert main_window.was_shown is True
    assert main_window.was_restored is True
    assert jira_window.was_hidden is True
    controller.acknowledge_analysis()
    assert controller.analysis_requested() is False


def test_jira_module_is_available_in_main_navigation() -> None:
    discovered = {module.key: module for module in discover_modules()}
    assert discovered["jira"] == MODULE
    assert discovered["jira"].title == "JIRA · Incurridos"
    assert JIRA_DASHBOARD_URL.startswith("https://umane.emeal.nttdata.com/")
