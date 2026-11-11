"""Presentación Streamlit de Aplantillado y configuración de personas."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st

from delivery_manager_toolbox.modules.jira.aplantillado import (
    DayCategory,
    PersonAnalysis,
    analyse_person,
    category_dates,
)
from delivery_manager_toolbox.modules.jira.extraction_result import ExtractionResult
from delivery_manager_toolbox.modules.jira.people_config import (
    PeopleConfiguration,
    PeopleConfigurationError,
    PeopleConfigurationRepository,
    dumps_people_configuration,
    loads_people_configuration,
    parse_people_configuration,
    restore_detected_people,
)


_CATEGORY_LABELS = {
    DayCategory.VACATION: "Vacation",
    DayCategory.FESTIVE: "Festive",
    DayCategory.COMPENSATED: "Compensated",
    DayCategory.STAFF: "Staff Augmentation",
    DayCategory.OTHER: "Otros",
    DayCategory.MISSING: "Faltantes L-V",
    DayCategory.WEEKEND: "Fin de semana",
}
_CATEGORY_COLORS = {
    DayCategory.VACATION: "violet",
    DayCategory.FESTIVE: "orange",
    DayCategory.COMPENSATED: "blue",
    DayCategory.STAFF: "gray",
    DayCategory.OTHER: "green",
    DayCategory.MISSING: "red",
    DayCategory.WEEKEND: "gray",
}
_MONTHS = (
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
)
EMPTY_STATE_MESSAGE = "Realiza primero una extracción de JIRA para cargar los datos."


def render_aplantillado(
    result: ExtractionResult | None,
    repository: PeopleConfigurationRepository,
) -> None:
    st.header("Aplantillado")
    configuration = _safe_load(repository)
    detected = result.people if result else ()

    with st.expander("Configuración de personas", expanded=result is None):
        render_people_configuration(repository, configuration, detected)

    if result is None:
        st.info(
            EMPTY_STATE_MESSAGE,
            title="Sin datos de extracción",
        )
        return

    configuration = _safe_load(repository)
    active_people = configuration.enabled_names
    if not active_people:
        st.warning("Activa al menos una persona en la configuración para mostrar el análisis.")
        return
    if not result.years:
        st.warning("La extracción no contiene fechas válidas para construir el calendario.")
        return

    controls = st.columns(3, vertical_alignment="bottom")
    person = controls[0].selectbox("Persona", active_people, key="jira_template_person")
    year = controls[1].selectbox("Año", result.years, key="jira_template_year")
    view_mode = controls[2].segmented_control(
        "Vista",
        ("Vista mensual", "Año completo"),
        default="Vista mensual",
        key="jira_template_view",
    )
    analysis = analyse_person(result, person, int(year))
    _render_metrics(analysis)
    _render_legend()
    if view_mode == "Año completo":
        _render_full_year(analysis)
    else:
        months = sorted({row.work_date.month for row in result.rows if row.work_date and row.work_date.year == year})
        default_month = months[0] if months else 1
        month = st.selectbox(
            "Mes",
            tuple(range(1, 13)),
            index=default_month - 1,
            format_func=lambda value: _MONTHS[value - 1],
            key="jira_template_month",
        )
        _render_month(analysis, month, compact=False)


def render_people_configuration(
    repository: PeopleConfigurationRepository,
    configuration: PeopleConfiguration,
    detected_names: tuple[str, ...],
) -> None:
    table = pd.DataFrame(
        ({"name": person.name, "enabled": person.enabled} for person in configuration.people),
        columns=("name", "enabled"),
    )
    edited = st.data_editor(
        table,
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "name": st.column_config.TextColumn("Persona", required=True),
            "enabled": st.column_config.CheckboxColumn("Activa", default=True),
        },
        key=f"jira_people_editor_{hash(dumps_people_configuration(configuration))}",
    )
    with st.container(horizontal=True):
        if st.button("Guardar configuración", icon=":material/save:"):
            try:
                people = [
                    {"name": str(row["name"]), "enabled": bool(row["enabled"])}
                    for row in edited.to_dict("records")
                ]
                updated = parse_people_configuration({"version": 1, "people": people})
                repository.save(updated)
                st.success("Configuración guardada.")
                st.rerun()
            except (PeopleConfigurationError, OSError) as error:
                st.error(str(error), title="No se pudo guardar")
        if st.button(
            "Restaurar detectadas",
            icon=":material/restore:",
            disabled=not detected_names,
        ):
            repository.save(restore_detected_people(configuration, detected_names))
            st.rerun()
        st.download_button(
            "Exportar configuración",
            data=dumps_people_configuration(configuration),
            file_name="jira_people.json",
            mime="application/json",
            icon=":material/download:",
        )

    uploaded = st.file_uploader(
        "Importar configuración",
        type=("json",),
        key="jira_people_import",
    )
    if st.button("Importar JSON", disabled=uploaded is None, icon=":material/upload:"):
        try:
            imported = loads_people_configuration(uploaded.getvalue())
            repository.save(imported)
            st.success("Configuración importada.")
            st.rerun()
        except (PeopleConfigurationError, OSError) as error:
            st.error(str(error), title="JSON no válido")


def _safe_load(repository: PeopleConfigurationRepository) -> PeopleConfiguration:
    try:
        return repository.load()
    except (PeopleConfigurationError, OSError) as error:
        st.error(str(error), title="Configuración de personas no válida")
        return PeopleConfiguration()


def _render_metrics(analysis: PersonAnalysis) -> None:
    metrics = analysis.metrics
    definitions = (
        ("Horas totales", _format_hours(metrics.total_hours), None),
        ("Vacation", metrics.vacation_days, DayCategory.VACATION),
        ("Festive", metrics.festive_days, DayCategory.FESTIVE),
        ("Compensated", metrics.compensated_days, DayCategory.COMPENSATED),
        ("Staff Augmentation", metrics.staff_days, DayCategory.STAFF),
        ("Otros", metrics.other_days, DayCategory.OTHER),
        ("Faltantes L-V", metrics.missing_days, DayCategory.MISSING),
    )
    for start, count in ((0, 4), (4, 3)):
        columns = st.columns(count)
        for column, (label, value, category) in zip(columns, definitions[start:start + count]):
            with column.container(border=True, height="stretch"):
                st.metric(label, value)
                if category is not None:
                    dates = category_dates(analysis, category)
                    with st.popover("Ver días", icon=":material/visibility:"):
                        if dates:
                            for month in range(1, 13):
                                days = [item.day for item in dates if item.month == month]
                                if days:
                                    st.write(f"{_MONTHS[month - 1]}: {', '.join(map(str, days))}")
                        else:
                            st.caption("No hay días de esta categoría.")


def _render_full_year(analysis: PersonAnalysis) -> None:
    st.subheader(str(analysis.year))
    for row_start in range(1, 13, 2):
        columns = st.columns(2)
        for column, month in zip(columns, range(row_start, row_start + 2)):
            with column.container(border=True, height="stretch"):
                _render_month(analysis, month, compact=True)


def _render_legend() -> None:
    with st.container(horizontal=True):
        for category in (
            DayCategory.VACATION,
            DayCategory.FESTIVE,
            DayCategory.COMPENSATED,
            DayCategory.STAFF,
            DayCategory.OTHER,
            DayCategory.MISSING,
        ):
            st.badge(_CATEGORY_LABELS[category], color=_CATEGORY_COLORS[category])


def _render_month(analysis: PersonAnalysis, month: int, compact: bool) -> None:
    st.markdown(f"#### {_MONTHS[month - 1]} {analysis.year}")
    header = st.columns(7, gap="small", wrap=False)
    for column, label in zip(header, ("L", "M", "X", "J", "V", "S", "D")):
        column.caption(label)
    weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(analysis.year, month)
    for week in weeks:
        columns = st.columns(7, gap="small", wrap=False)
        for column, day in zip(columns, week):
            if day == 0:
                column.write("")
                continue
            work_date = date(analysis.year, month, day)
            entries = analysis.entries_by_date.get(work_date, ())
            category = analysis.categories_by_date[work_date]
            hours = sum((entry.hours for entry in entries if entry.hours is not None), Decimal("0"))
            with column.container(border=True, height="stretch"):
                st.badge(str(day), color=_CATEGORY_COLORS[category])
                if entries:
                    st.caption(f"{_format_hours(hours)} h")
                    if not compact:
                        for entry in entries:
                            st.text(entry.task)
                        with st.popover("Detalle", icon=":material/info:"):
                            for entry in entries:
                                st.write(f"{entry.task} · {_format_hours(entry.hours or Decimal('0'))} h")
                                if entry.comment:
                                    st.caption(entry.comment)
                elif category is DayCategory.MISSING:
                    st.caption("Sin imputar")


def _format_hours(value: Decimal) -> str:
    return format(value.normalize(), "f") if value else "0"


__all__ = ["EMPTY_STATE_MESSAGE", "render_aplantillado", "render_people_configuration"]
