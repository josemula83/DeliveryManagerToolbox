from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest

from delivery_manager_toolbox.modules.jira.aplantillado import (
    DayCategory,
    analyse_person,
    category_dates,
    classify_day,
    group_rows_by_date,
    group_rows_by_person,
    is_missing_workday,
)
from delivery_manager_toolbox.modules.jira.aplantillado_view import EMPTY_STATE_MESSAGE
from delivery_manager_toolbox.modules.jira.extraction_result import (
    ExtractionResult,
    ExtractionRow,
    extraction_from_transport,
    extraction_from_webview,
)
from delivery_manager_toolbox.modules.jira.host_api import ExtractionStore, JiraHostApi
from delivery_manager_toolbox.modules.jira.people_config import (
    ConfiguredPerson,
    PeopleConfiguration,
    PeopleConfigurationError,
    PeopleConfigurationRepository,
    dumps_people_configuration,
    loads_people_configuration,
    merge_detected_people,
    restore_detected_people,
    set_person_enabled,
)
from delivery_manager_toolbox.modules.jira.result_exporter import export_result_csv


RAW_HEADER = (
    "Name",
    "Sub-Task Summary",
    "Hours",
    "Comment",
    "Issue",
    "Type",
    "Field 6",
    "Field 7",
    "Field 8",
    "Field 9",
    "Field 10",
    "Field 11",
    "Field 12",
    "Field 13",
    "Field 14",
    "Date",
)


def raw_record(
    name: str,
    work_date: str,
    hours: object,
    task: str,
    comment: str = "",
    pkey: str = "SYNTH",
) -> list[object]:
    raw: list[object] = [name, task, hours, comment, "SYNTH-1", "Task"] + [""] * 9 + [work_date]
    return [pkey, work_date[:4], work_date[5:7], work_date[:7], *raw]


def webview_message(records: list[list[object]], pkeys: list[str] | None = None) -> dict[str, object]:
    return {
        "type": "jira_extraction_completed",
        "payload": {
            "from": "2026-01-01",
            "to": "2026-12-31",
            "pkeys": pkeys or ["SYNTH"],
            "header": list(RAW_HEADER),
            "records": records,
        },
    }


def synthetic_result() -> ExtractionResult:
    return extraction_from_webview(
        webview_message(
            [
                raw_record("  Ana   García  ", "2026-01-05", "7,5", "Vacation", "Descanso"),
                raw_record("ana garcía", "2026-01-06", 3, "Task alpha", "Comentario"),
                raw_record("Pedro López", "2026-01-05", 8, "Staff Augmentation"),
            ]
        )
    )


def test_extraction_normalizes_real_legacy_columns_without_losing_original_row() -> None:
    result = synthetic_result()
    first = result.rows[0]

    assert result.date_from == date(2026, 1, 1)
    assert result.pkeys == ("SYNTH",)
    assert first.person == "Ana García"
    assert first.work_date == date(2026, 1, 5)
    assert first.hours == Decimal("7.5")
    assert first.task == "Vacation"
    assert first.comment == "Descanso"
    assert first.pkey == "SYNTH"
    assert first.original_values == tuple(raw_record("  Ana   García  ", "2026-01-05", "7,5", "Vacation", "Descanso"))


def test_transport_round_trip_preserves_the_shared_result() -> None:
    original = synthetic_result()
    restored = extraction_from_transport(original.to_transport())
    assert restored == original


def test_csv_is_generated_from_the_same_internal_result() -> None:
    result = synthetic_result()
    csv_contents = export_result_csv(result).decode("utf-8-sig")
    assert csv_contents.startswith('"PKEY";"Año";"Mes";"Año-Mes";"Name"')
    assert '"  Ana   García  "' in csv_contents
    assert '"Vacation"' in csv_contents


def test_unique_people_are_trimmed_and_case_insensitive() -> None:
    assert synthetic_result().people == ("Ana García", "Pedro López")


def test_people_union_adds_new_names_enabled_without_removing_old() -> None:
    initial = PeopleConfiguration((ConfiguredPerson("Ana García", False), ConfiguredPerson("Juan", True)))
    merged, added = merge_detected_people(initial, (" ana garcía ", "María", "CARLOS", "maría"))

    assert added == ("María", "CARLOS")
    assert merged.people[0] == ConfiguredPerson("Ana García", False)
    assert tuple(person.name for person in merged.people) == ("Ana García", "Juan", "María", "CARLOS")


def test_people_can_be_enabled_and_detected_names_restored() -> None:
    initial = PeopleConfiguration((ConfiguredPerson("Ana", False), ConfiguredPerson("Pedro", False)))
    enabled = set_person_enabled(initial, " ANA ", True)
    restored = restore_detected_people(initial, ("pedro", "María"))

    assert enabled.people[0].enabled is True
    assert restored.people == (
        ConfiguredPerson("Ana", False),
        ConfiguredPerson("Pedro", True),
        ConfiguredPerson("María", True),
    )


def test_people_configuration_persists_and_exports_versioned_json() -> None:
    path = Path(".tmp-jira-people-persistence.json")
    path.unlink(missing_ok=True)
    repository = PeopleConfigurationRepository(path)
    configuration = PeopleConfiguration((ConfiguredPerson("Persona Sintética", True),))
    try:
        repository.save(configuration)
        assert repository.load() == configuration
        payload = json.loads(dumps_people_configuration(configuration))
        assert payload == {
            "version": 1,
            "people": [{"name": "Persona Sintética", "enabled": True}],
        }
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "contents",
    (
        "{broken",
        '{"version":99,"people":[]}',
        '{"version":1,"people":[{"name":"","enabled":true}]}',
        '{"version":1,"people":[{"name":"Ana","enabled":true},{"name":" ana ","enabled":false}]}',
    ),
)
def test_invalid_people_json_is_rejected(contents: str) -> None:
    with pytest.raises(PeopleConfigurationError):
        loads_people_configuration(contents)


def test_rows_are_grouped_by_active_person_and_date_without_fuzzy_matching() -> None:
    result = synthetic_result()
    grouped = group_rows_by_person(result, ("ANA GARCÍA", "Pedro López", "Ana Garza"))
    by_date = group_rows_by_date(grouped["ANA GARCÍA"])

    assert len(grouped["ANA GARCÍA"]) == 2
    assert grouped["Ana Garza"] == ()
    assert len(by_date[date(2026, 1, 5)]) == 1


def test_day_categories_follow_legacy_priority() -> None:
    vacation = ExtractionRow("SYNTH", "Ana", date(2026, 1, 5), Decimal("1"), "Vacaciones", "", ())
    staff = ExtractionRow("SYNTH", "Ana", date(2026, 1, 5), Decimal("1"), "Staff", "", ())
    assert classify_day(date(2026, 1, 5), (staff, vacation)) is DayCategory.VACATION
    assert classify_day(date(2026, 1, 6), (staff,)) is DayCategory.STAFF
    assert classify_day(date(2026, 1, 7), ()) is DayCategory.MISSING
    assert classify_day(date(2026, 1, 10), ()) is DayCategory.WEEKEND


def test_missing_workday_is_independent_and_excludes_weekends() -> None:
    assert is_missing_workday(date(2026, 1, 7), ()) is True
    assert is_missing_workday(date(2026, 1, 10), ()) is False
    assert is_missing_workday(date(2026, 1, 7), synthetic_result().rows[:1]) is False


def test_person_metrics_sum_hours_categories_missing_days_and_available_years() -> None:
    result = synthetic_result()
    analysis = analyse_person(result, "ANA GARCÍA", 2026)
    weekdays = sum(
        1
        for month in range(1, 13)
        for day in range(1, calendar.monthrange(2026, month)[1] + 1)
        if date(2026, month, day).weekday() < 5
    )

    assert result.years == (2026,)
    assert analysis.metrics.total_hours == Decimal("10.5")
    assert analysis.metrics.vacation_days == 1
    assert analysis.metrics.other_days == 1
    assert analysis.metrics.missing_days == weekdays - 2
    assert category_dates(analysis, DayCategory.VACATION) == (date(2026, 1, 5),)


def test_host_api_replaces_previous_extraction_and_both_actions_share_it() -> None:
    path = Path(".tmp-jira-people-host.json")
    path.unlink(missing_ok=True)
    store = ExtractionStore()
    repository = PeopleConfigurationRepository(path)
    opened: list[bool] = []
    api = JiraHostApi(store, repository, lambda: opened.append(True))

    try:
        first = api.receive_jira_extraction(webview_message([raw_record("Ana", "2026-01-05", 8, "Task")]))
        second = api.receive_jira_extraction(webview_message([raw_record("María", "2026-02-02", 4, "Festive")]))

        revision, current = store.snapshot()
        assert first["ok"] is True
        assert second["ok"] is True
        assert revision == 2
        assert current is not None and current.people == ("María",)
        assert api.get_jira_csv()["ok"] is True
        assert api.open_aplantillado()["ok"] is True
        assert opened == [True]
        assert tuple(person.name for person in repository.load().people) == ("Ana", "María")
    finally:
        path.unlink(missing_ok=True)


def test_corrupt_people_configuration_does_not_block_a_new_extraction() -> None:
    path = Path(".tmp-jira-people-corrupt.json")
    path.write_text("{broken", encoding="utf-8")
    repository = PeopleConfigurationRepository(path)
    api = JiraHostApi(ExtractionStore(), repository, lambda: None)
    try:
        response = api.receive_jira_extraction(
            webview_message([raw_record("Persona Sintética", "2026-03-02", 8, "Task")])
        )
        assert response["ok"] is True
        assert response["warning"]
        assert repository.load().people == (ConfiguredPerson("Persona Sintética", True),)
    finally:
        path.unlink(missing_ok=True)


def test_aplantillado_without_extraction_has_a_friendly_empty_state() -> None:
    assert EMPTY_STATE_MESSAGE == "Realiza primero una extracción de JIRA para cargar los datos."
