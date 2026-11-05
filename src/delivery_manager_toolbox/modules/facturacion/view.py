"""Interfaz Streamlit del módulo de Facturación."""

from __future__ import annotations

from collections.abc import Iterable, MutableMapping
from hashlib import sha256
import json

import pandas as pd
import streamlit as st
from streamlit.typing import UploadedFile

from delivery_manager_toolbox.core.logging import get_logger
from delivery_manager_toolbox.modules.facturacion.business import (
    calculate_sheet_management,
    calculate_sheet_rate_optimization,
    calculate_workbook_management,
)
from delivery_manager_toolbox.modules.facturacion.configuration import (
    BillingConfiguration,
    add_management_person_account_exception,
    add_management_rate_block,
    add_no_management_account,
    available_values,
    available_management_rate_accounts,
    available_no_management_accounts,
    available_exception_accounts,
    available_exception_people,
    add_fixed_management_account,
    add_management_person,
    configuration_from_json,
    configuration_to_json,
    load_configuration,
    reconcile_management_person_account_exceptions_for_sheets,
    remove_management_person_account_exception,
    remove_management_rate_block,
    replace_management_rate_block,
    replace_fixed_management_accounts,
    replace_management_people,
    replace_no_management_accounts,
    save_configuration,
    with_source_sheet,
)
from delivery_manager_toolbox.modules.facturacion.excel_reader import (
    WorkbookInspectionError,
    inspect_workbook,
    parse_hierarchical_worksheet,
)
from delivery_manager_toolbox.modules.facturacion.models import (
    AccountCatalogItem,
    AllocationStatus,
    CalculationType,
    ManagementAllocationResult,
    RateOptimizationResult,
    SheetAnalysis,
    SheetCalculationResult,
    SkippedSheet,
    WorkbookAnalysis,
    WorkbookCalculationResult,
)
from delivery_manager_toolbox.modules.facturacion.validation import ConfigurationValidationError


LOGGER = get_logger(__name__)
_CONFIG_KEY = "facturacion_configuration"
_NOTICE_KEY = "facturacion_notice"
_RESULT_KEY = "facturacion_phase_one_result"
_RESULT_CONTEXT_KEY = "facturacion_phase_one_context"
_WORKBOOK_RESULT_KEY = "facturacion_all_sheets_result"
_WORKBOOK_RESULT_CONTEXT_KEY = "facturacion_all_sheets_context"


@st.cache_data(show_spinner=False, max_entries=10)
def _inspect_uploaded_workbook(contents: bytes, filename: str) -> WorkbookAnalysis:
    return inspect_workbook(contents, filename)


@st.cache_data(show_spinner=False, max_entries=20)
def _parse_uploaded_sheet(contents: bytes, sheet: str) -> SheetAnalysis:
    return parse_hierarchical_worksheet(contents, sheet)


def _parse_workbook_sheets(
    contents: bytes,
    sheet_names: Iterable[str],
) -> tuple[tuple[SheetAnalysis, ...], tuple[SkippedSheet, ...]]:
    analyses: list[SheetAnalysis] = []
    skipped: list[SkippedSheet] = []
    for sheet_name in sheet_names:
        try:
            analyses.append(_parse_uploaded_sheet(contents, sheet_name))
        except WorkbookInspectionError as exc:
            skipped.append(SkippedSheet(sheet_name, str(exc)))
    return tuple(analyses), tuple(skipped)


def render_page() -> None:
    configuration = _configuration()
    st.title("Facturación")
    st.caption("Revisa el Excel jerárquico, calcula la gestión y repártela entre las Personas en Gestión.")
    _show_notice()

    uploaded_file = _render_upload()
    workbook_info, analysis = _render_workbook(uploaded_file, configuration)
    if (
        uploaded_file is not None
        and workbook_info is not None
        and _configuration().management_person_account_exceptions
    ):
        sheet_analyses, _ = _parse_workbook_sheets(
            uploaded_file.getvalue(),
            workbook_info.sheet_names,
        )
        reconciled, removed = reconcile_management_person_account_exceptions_for_sheets(
            _configuration(),
            sheet_analyses,
        )
        if removed:
            st.session_state[_CONFIG_KEY] = reconciled
            st.warning(
                f"Se eliminaron {removed} excepciones persona-cuenta que ya no existen "
                "en ninguna hoja del libro o ya no corresponden a Personas en Gestión."
            )
    _render_configuration(_configuration(), analysis)
    _render_persistence(_configuration())

    calculation_context = _calculation_context(uploaded_file, analysis, _configuration())
    workbook_calculation_context = _workbook_calculation_context(uploaded_file, _configuration())
    _discard_stale_calculation_result(st.session_state, calculation_context, analysis)
    _discard_stale_workbook_calculation_result(
        st.session_state,
        workbook_calculation_context,
    )
    individual_column, workbook_column = st.columns(2)
    with individual_column:
        calculate_individual = st.button(
            "CALCULAR FACTURACIÓN",
            type="primary",
            icon=":material/calculate:",
            disabled=analysis is None,
            width="stretch",
            help="Calcula exclusivamente la hoja seleccionada.",
            key="calculate_billing",
        )
    with workbook_column:
        calculate_workbook = st.button(
            "CALCULAR TODOS LOS MESES",
            icon=":material/calendar_month:",
            disabled=uploaded_file is None or workbook_info is None,
            width="stretch",
            help="Calcula cada hoja compatible de forma independiente.",
            key="calculate_all_billing",
        )
    if calculate_individual and analysis is not None:
        result = calculate_sheet_management(analysis, _configuration())
        st.session_state[_RESULT_KEY] = calculate_sheet_rate_optimization(
            result,
            analysis,
            _configuration(),
        )
        st.session_state[_RESULT_CONTEXT_KEY] = calculation_context
        st.session_state.pop(_WORKBOOK_RESULT_KEY, None)
        st.session_state.pop(_WORKBOOK_RESULT_CONTEXT_KEY, None)
    if calculate_workbook and uploaded_file is not None and workbook_info is not None:
        analyses, parse_skipped = _parse_workbook_sheets(
            uploaded_file.getvalue(),
            workbook_info.sheet_names,
        )
        st.session_state[_WORKBOOK_RESULT_KEY] = calculate_workbook_management(
            analyses,
            _configuration(),
            parse_skipped,
        )
        st.session_state[_WORKBOOK_RESULT_CONTEXT_KEY] = workbook_calculation_context
        st.session_state.pop(_RESULT_KEY, None)
        st.session_state.pop(_RESULT_CONTEXT_KEY, None)
    result = st.session_state.get(_RESULT_KEY)
    workbook_result = st.session_state.get(_WORKBOOK_RESULT_KEY)
    has_result = result is not None or workbook_result is not None
    st.caption(
        "Cálculo, reparto y tarifas completados; el Excel final sigue pendiente."
        if has_result else
        "Excel preparado para calcular." if workbook_info else
        "Carga un Excel para iniciar el análisis."
    )
    if isinstance(workbook_result, WorkbookCalculationResult):
        _render_workbook_calculation_outputs(workbook_result)
    else:
        _render_calculation_outputs(result if isinstance(result, SheetCalculationResult) else None)


def _configuration() -> BillingConfiguration:
    if _CONFIG_KEY not in st.session_state:
        try:
            st.session_state[_CONFIG_KEY] = load_configuration()
        except ConfigurationValidationError as exc:
            LOGGER.warning("Configuración de Facturación no válida: %s", exc)
            st.session_state[_CONFIG_KEY] = BillingConfiguration()
            st.session_state[_NOTICE_KEY] = ("error", f"No se pudo cargar la configuración guardada: {exc}")
    return st.session_state[_CONFIG_KEY]


def _set_configuration(configuration: BillingConfiguration, message: str) -> None:
    st.session_state[_CONFIG_KEY] = configuration
    st.session_state.pop(_RESULT_KEY, None)
    st.session_state.pop(_RESULT_CONTEXT_KEY, None)
    st.session_state.pop(_WORKBOOK_RESULT_KEY, None)
    st.session_state.pop(_WORKBOOK_RESULT_CONTEXT_KEY, None)
    st.session_state[_NOTICE_KEY] = ("success", message)
    st.rerun()


def _run_action(action, message: str) -> None:
    try:
        _set_configuration(action(), message)
    except ConfigurationValidationError as exc:
        st.error(str(exc))


def _show_notice() -> None:
    notice = st.session_state.pop(_NOTICE_KEY, None)
    if notice:
        level, message = notice
        getattr(st, level)(message)


def _render_upload() -> UploadedFile | None:
    with st.container(border=True):
        st.subheader("Archivo de entrada")
        return st.file_uploader(
            "Selecciona o arrastra un archivo Excel",
            type=["xlsx"],
            key="facturacion_input_file",
            help="Se analiza en memoria. El contenido del Excel no se guarda en la configuración.",
        )


def _render_workbook(
    uploaded_file: UploadedFile | None,
    configuration: BillingConfiguration,
) -> tuple[WorkbookAnalysis | None, SheetAnalysis | None]:
    if uploaded_file is None:
        st.info("Carga un `.xlsx` para detectar personas, cuentas, asignaciones y tarifas.")
        return None, None
    try:
        contents = uploaded_file.getvalue()
        information = _inspect_uploaded_workbook(contents, uploaded_file.name)
        selected_sheet = configuration.source_sheet if configuration.source_sheet in information.sheet_names else information.suggested_sheet
        sheet_index = information.sheet_names.index(selected_sheet) if selected_sheet in information.sheet_names else 0
        sheet = st.selectbox(
            "Hoja de datos",
            information.sheet_names,
            index=sheet_index,
            key=f"billing_source_sheet_{uploaded_file.name}_{len(contents)}",
        )
        if sheet != configuration.source_sheet:
            st.session_state[_CONFIG_KEY] = with_source_sheet(configuration, sheet)
        analysis = _parse_uploaded_sheet(contents, sheet)
    except WorkbookInspectionError as exc:
        st.error(str(exc), icon=":material/error:")
        return None, None

    file_metrics = st.columns(3)
    file_metrics[0].metric("Fichero", information.filename, border=True, height="stretch")
    file_metrics[1].metric("Hojas", information.sheet_count, border=True, height="stretch")
    file_metrics[2].metric(
        "Filas hoja activa",
        information.worksheet(sheet).approximate_records,
        border=True,
        height="stretch",
    )

    detection_metrics = st.columns(4)
    detection_metrics[0].metric("Personas detectadas", len(analysis.people), border=True, height="stretch")
    detection_metrics[1].metric("Cuentas únicas", len(analysis.accounts), border=True, height="stretch")
    detection_metrics[2].metric("Asignaciones", len(analysis.assignments), border=True, height="stretch")
    detection_metrics[3].metric("Tarifas detectadas", len(analysis.rates), border=True, height="stretch")
    _render_preview(analysis)
    return information, analysis


def _render_preview(analysis: SheetAnalysis) -> None:
    st.markdown("#### Previsualización")
    people_tab, assignments_tab = st.tabs(["Personas", "Cuentas / asignaciones"])
    with people_tab:
        people_data = pd.DataFrame([
            {"Número empleado": item.employee_id, "Persona": item.name, "Nº cuentas": item.account_count}
            for item in analysis.person_summaries
        ])
        st.dataframe(people_data, hide_index=True, width="stretch", alt="Personas detectadas y número de cuentas")
    with assignments_tab:
        assignment_data = pd.DataFrame([
            {
                "Persona": item.person.name,
                "Cuenta": item.account_code or "Sin código EXT",
                "Servicio": item.service_name,
                "Tarifa": item.rate,
            }
            for item in analysis.assignments
        ])
        st.dataframe(
            assignment_data,
            hide_index=True,
            width="stretch",
            alt="Asignaciones de personas a cuentas",
            column_config={"Tarifa": st.column_config.NumberColumn(format="%.2f EUR")},
        )
    with st.expander("Ver Excel original"):
        raw_data = pd.DataFrame(analysis.raw_rows, columns=analysis.raw_columns).map(
            lambda value: "" if value is None else str(value)
        )
        st.dataframe(
            raw_data,
            hide_index=True,
            width="stretch",
            alt="Muestra de las filas originales del Excel",
        )


def _render_configuration(
    configuration: BillingConfiguration,
    analysis: SheetAnalysis | None,
) -> None:
    st.subheader("Configuración")
    detected_people = tuple(person.name for person in analysis.people) if analysis else ()
    catalog = analysis.accounts if analysis else ()
    left, right = st.columns(2)
    with left:
        _render_people_section(configuration, detected_people)
        _render_fixed_accounts_section(_configuration(), catalog)
    with right:
        _render_no_management_section(_configuration(), catalog)
        _render_management_rate_blocks_section(_configuration(), catalog)
    _render_management_exceptions_section(_configuration(), analysis)


def _render_people_section(configuration: BillingConfiguration, detected: tuple[str, ...]) -> None:
    available_people = available_values(detected, (item.name for item in configuration.management_people))
    with st.container(border=True):
        with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
            st.markdown("#### Personas en gestión")
            if st.button("Añadir persona", icon=":material/add:", key="open_add_person"):
                _add_person_dialog(configuration, available_people)
        data = pd.DataFrame([
            {"Persona": item.name, "Capacidad libre (%)": item.free_capacity_percentage, "Eliminar": False}
            for item in configuration.management_people
        ])
        if data.empty:
            st.caption("No hay personas configuradas.")
        else:
            edited = st.data_editor(
                data,
                hide_index=True,
                width="stretch",
                alt="Personas en gestión configuradas",
                disabled=["Persona"],
                key="people_editor",
                column_config={
                    "Capacidad libre (%)": st.column_config.NumberColumn(min_value=0.0, max_value=100.0),
                    "Eliminar": st.column_config.CheckboxColumn(width="small"),
                },
            )
            if st.button("Aplicar cambios", key="apply_people", type="secondary"):
                rows = [(row["Persona"], row["Capacidad libre (%)"]) for _, row in edited.iterrows() if not row["Eliminar"]]
                updated = replace_management_people(configuration, rows)
                removed = (
                    len(configuration.management_person_account_exceptions)
                    - len(updated.management_person_account_exceptions)
                )
                message = "Personas actualizadas."
                if removed:
                    message += f" Se eliminaron {removed} excepciones asociadas."
                _run_action(lambda: updated, message)


def _render_no_management_section(configuration: BillingConfiguration, catalog: tuple[AccountCatalogItem, ...]) -> None:
    labels = _account_labels(catalog)
    available_codes = available_no_management_accounts(labels.values(), configuration)
    available_labels = _labels_for_codes(labels, available_codes)
    with st.container(border=True):
        with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
            st.markdown("#### Cuentas sin gestión")
            if st.button("Añadir cuenta", icon=":material/add:", key="open_add_no_management"):
                _add_no_management_dialog(configuration, available_labels)
        st.caption("Cuentas excluidas del reparto de horas de gestión.")
        data = pd.DataFrame([
            {"Cuenta": _display_account(item, labels), "Código interno": item, "Eliminar": False}
            for item in configuration.no_management_accounts
        ])
        if data.empty:
            st.caption("No hay cuentas configuradas.")
        else:
            edited = st.data_editor(
                data,
                hide_index=True,
                width="stretch",
                alt="Cuentas sin gestión configuradas",
                disabled=["Cuenta", "Código interno"],
                column_config={"Código interno": None, "Eliminar": st.column_config.CheckboxColumn(width="small")},
                key="no_management_editor",
            )
            if st.button("Aplicar cambios", key="apply_no_management"):
                rows = [row["Código interno"] for _, row in edited.iterrows() if not row["Eliminar"]]
                _run_action(lambda: replace_no_management_accounts(configuration, rows), "Cuentas actualizadas.")


def _render_fixed_accounts_section(configuration: BillingConfiguration, catalog: tuple[AccountCatalogItem, ...]) -> None:
    labels = _account_labels(catalog)
    available_labels = _labels_for_codes(
        labels,
        available_values(
            labels.values(),
            (item.account for item in configuration.fixed_management_accounts),
        ),
    )
    with st.container(border=True):
        with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
            st.markdown("#### Cuentas con gestión fija")
            if st.button("Añadir cuenta", icon=":material/add:", key="open_add_fixed"):
                _add_fixed_account_dialog(configuration, available_labels)
        data = pd.DataFrame([
            {"Cuenta": _display_account(item.account, labels), "Código interno": item.account, "Horas fijas": item.fixed_hours, "Eliminar": False}
            for item in configuration.fixed_management_accounts
        ])
        if data.empty:
            st.caption("No hay cuentas configuradas.")
        else:
            edited = st.data_editor(
                data,
                hide_index=True,
                width="stretch",
                alt="Cuentas con horas de gestión fija",
                disabled=["Cuenta", "Código interno"],
                column_config={
                    "Código interno": None,
                    "Horas fijas": st.column_config.NumberColumn(min_value=0.0, step=0.5),
                    "Eliminar": st.column_config.CheckboxColumn(width="small"),
                },
                key="fixed_editor",
            )
            if st.button("Aplicar cambios", key="apply_fixed"):
                rows = [(row["Código interno"], row["Horas fijas"]) for _, row in edited.iterrows() if not row["Eliminar"]]
                _run_action(lambda: replace_fixed_management_accounts(configuration, rows), "Horas fijas actualizadas.")


def _render_management_rate_blocks_section(
    configuration: BillingConfiguration,
    catalog: tuple[AccountCatalogItem, ...],
) -> None:
    labels = _account_labels(catalog)
    with st.container(border=True):
        with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
            st.markdown("#### Bloques de tarifa de gestión")
            if st.button("Añadir bloque", icon=":material/add:", key="open_add_rate_block"):
                _management_rate_block_dialog(configuration, labels)
        if not configuration.management_rate_blocks:
            st.caption("No hay bloques configurados.")
        for block in configuration.management_rate_blocks:
            with st.container(border=True, gap=None):
                st.markdown(f"**{block.name}**")
                formatted_rate = f"{block.management_rate:.2f}".replace(".", ",")
                account_label = "cuenta" if len(block.accounts) == 1 else "cuentas"
                st.caption(f"{formatted_rate} €/h · {len(block.accounts)} {account_label}")
                with st.container(horizontal=True):
                    if st.button("Editar", icon=":material/edit:", key=f"edit_rate_block_{block.name}"):
                        _management_rate_block_dialog(configuration, labels, block.name)
                    if st.button("Eliminar", icon=":material/delete:", key=f"delete_rate_block_{block.name}"):
                        _run_action(
                            lambda name=block.name: remove_management_rate_block(configuration, name),
                            "Bloque eliminado.",
                        )


def _render_management_exceptions_section(
    configuration: BillingConfiguration,
    analysis: SheetAnalysis | None,
) -> None:
    service_by_account = {
        item.account_code: item.service_name
        for item in analysis.accounts
    } if analysis else {}
    available_people = available_exception_people(configuration, analysis) if analysis else ()
    with st.container(border=True):
        with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
            st.markdown("#### Excepciones persona-cuenta de gestión")
            if st.button(
                "Añadir excepción",
                icon=":material/add:",
                key="open_add_management_exception",
                disabled=analysis is None or not available_people,
            ):
                _add_management_exception_dialog(configuration, analysis)
        st.caption(
            "Combinaciones de personas de gestión y cuentas cuyas horas originales deben "
            "conservarse durante el cálculo."
        )
        data = pd.DataFrame([
            {
                "Persona": exception.person_name,
                "Cuenta": exception.account_code,
                "Servicio": service_by_account.get(exception.account_code, ""),
                "Identificador": exception.employee_id,
                "Código interno": exception.account_code,
                "Eliminar": False,
            }
            for exception in configuration.management_person_account_exceptions
        ])
        if data.empty:
            st.caption("No hay excepciones configuradas.")
        else:
            edited = st.data_editor(
                data,
                hide_index=True,
                width="stretch",
                alt="Excepciones persona-cuenta de gestión configuradas",
                disabled=["Persona", "Cuenta", "Servicio", "Identificador", "Código interno"],
                column_config={
                    "Identificador": None,
                    "Código interno": None,
                    "Eliminar": st.column_config.CheckboxColumn(width="small"),
                },
                key="management_exceptions_editor",
            )
            if st.button("Aplicar cambios", key="apply_management_exceptions"):
                removals = [
                    (row["Identificador"], row["Código interno"])
                    for _, row in edited.iterrows()
                    if row["Eliminar"]
                ]

                def remove_selected_exceptions() -> BillingConfiguration:
                    updated = configuration
                    for employee_id, account_code in removals:
                        updated = remove_management_person_account_exception(
                            updated,
                            employee_id,
                            account_code,
                        )
                    return updated

                _run_action(remove_selected_exceptions, "Excepciones actualizadas.")
        if analysis is None:
            st.caption("Carga un Excel para añadir y validar excepciones.")


@st.dialog("Añadir persona")
def _add_person_dialog(configuration: BillingConfiguration, detected: tuple[str, ...]) -> None:
    with st.form("add_management_person", border=False):
        name = st.selectbox(
            "Persona", detected, index=None, accept_new_options=True,
            placeholder="Busca o escribe una persona", key="new_management_person",
        )
        percentage = st.number_input("Capacidad libre (%)", min_value=0.0, max_value=100.0, value=0.0, step=0.1)
        if st.form_submit_button("Añadir", icon=":material/add:", type="primary", width="stretch"):
            _run_action(lambda: add_management_person(configuration, name, percentage), "Persona añadida.")


@st.dialog("Añadir cuenta sin gestión")
def _add_no_management_dialog(configuration: BillingConfiguration, labels: dict[str, str]) -> None:
    with st.form("add_no_management", border=False):
        selected = st.selectbox(
            "Cuenta", tuple(labels), index=None, accept_new_options=True,
            placeholder="Busca o escribe una cuenta", key="new_no_management",
        )
        if st.form_submit_button("Añadir", icon=":material/add:", type="primary", width="stretch"):
            _run_action(
                lambda: add_no_management_account(configuration, _account_value(selected, labels)),
                "Cuenta añadida.",
            )


@st.dialog("Añadir cuenta con gestión fija")
def _add_fixed_account_dialog(configuration: BillingConfiguration, labels: dict[str, str]) -> None:
    with st.form("add_fixed_account", border=False):
        selected = st.selectbox(
            "Cuenta", tuple(labels), index=None, accept_new_options=True,
            placeholder="Busca o escribe una cuenta", key="new_fixed_account",
        )
        hours = st.number_input("Horas fijas", min_value=0.0, value=0.0, step=0.5)
        if st.form_submit_button("Añadir", icon=":material/add:", type="primary", width="stretch"):
            _run_action(lambda: add_fixed_management_account(configuration, _account_value(selected, labels), hours), "Cuenta añadida.")


@st.dialog("Añadir excepción persona-cuenta")
def _add_management_exception_dialog(
    configuration: BillingConfiguration,
    analysis: SheetAnalysis,
) -> None:
    people = available_exception_people(configuration, analysis)
    selected_person = st.selectbox(
        "Persona de gestión",
        people,
        index=None,
        format_func=lambda person: f"{person.name} ({person.employee_id})",
        placeholder="Selecciona una persona",
        key="new_management_exception_person",
    )
    accounts = (
        available_exception_accounts(
            configuration,
            analysis,
            selected_person.employee_id,
            selected_person.name,
        )
        if selected_person else ()
    )
    selected_account = st.selectbox(
        "Cuenta",
        accounts,
        index=None,
        format_func=lambda assignment: (
            f"{assignment.account_code} — {assignment.service_name}"
            if assignment.service_name else str(assignment.account_code)
        ),
        placeholder="Selecciona una cuenta",
        disabled=selected_person is None,
        key="new_management_exception_account",
    )
    if selected_person is not None and not accounts:
        st.caption("Esta persona no tiene más cuentas disponibles en el Excel activo.")
    if st.button(
        "Añadir excepción",
        icon=":material/add:",
        type="primary",
        width="stretch",
        disabled=selected_person is None or selected_account is None,
        key="add_management_exception",
    ):
        _run_action(
            lambda: add_management_person_account_exception(
                configuration,
                selected_person.employee_id,
                selected_person.name,
                selected_account.account_code,
                analysis.assignments,
            ),
            "Excepción añadida.",
        )


@st.dialog("Bloque de tarifa de gestión", width="large")
def _management_rate_block_dialog(
    configuration: BillingConfiguration,
    labels: dict[str, str],
    original_name: str | None = None,
) -> None:
    existing = next(
        (block for block in configuration.management_rate_blocks if block.name == original_name),
        None,
    )
    available_codes = available_management_rate_accounts(labels.values(), configuration, original_name)
    selectable_labels = _labels_for_codes(labels, available_codes)
    reverse_labels = {value: label for label, value in labels.items()}
    default_accounts = [reverse_labels.get(account, account) for account in existing.accounts] if existing else []
    options = tuple(dict.fromkeys((*selectable_labels, *default_accounts)))
    with st.form(f"rate_block_form_{original_name or 'new'}", border=False):
        name = st.text_input("Nombre del bloque", value=existing.name if existing else "")
        selected_accounts = st.multiselect(
            "Cuentas", options, default=default_accounts, accept_new_options=True,
            placeholder="Selecciona o escribe cuentas",
        )
        management_rate = st.number_input(
            "Tarifa de gestión (€/h)",
            min_value=0.0,
            value=existing.management_rate if existing else 0.0,
            step=0.01,
            format="%.2f",
        )
        if st.form_submit_button("Guardar bloque", icon=":material/save:", type="primary", width="stretch"):
            accounts = [_account_value(item, labels) for item in selected_accounts]
            if existing:
                action = lambda: replace_management_rate_block(
                    configuration, existing.name, name, accounts, management_rate
                )
            else:
                action = lambda: add_management_rate_block(configuration, name, accounts, management_rate)
            _run_action(action, "Bloque guardado.")


def _account_labels(catalog: tuple[AccountCatalogItem, ...]) -> dict[str, str]:
    return {item.display_name: item.account_code for item in catalog}


def _labels_for_codes(labels: dict[str, str], codes: Iterable[str]) -> dict[str, str]:
    available_codes = set(codes)
    return {label: code for label, code in labels.items() if code in available_codes}


def _account_value(selected: object, labels: dict[str, str]) -> object:
    return labels.get(str(selected), selected)


def _display_account(account: str, labels: dict[str, str]) -> str:
    return next((label for label, value in labels.items() if value == account), account)


def _render_persistence(configuration: BillingConfiguration) -> None:
    st.markdown("#### Guardado e intercambio")
    with st.container(horizontal=True):
        if st.button("Guardar configuración", icon=":material/save:"):
            try:
                save_configuration(configuration)
                st.success("Configuración guardada en `config/facturacion.json`.")
            except (ConfigurationValidationError, OSError) as exc:
                st.error(f"No se pudo guardar: {exc}")
        st.download_button(
            "Exportar JSON", data=configuration_to_json(configuration), file_name="facturacion-config.json",
            mime="application/json", icon=":material/download:",
        )
    imported = st.file_uploader("Importar configuración JSON", type=["json"], key="billing_config_import")
    if imported is not None and st.button("Validar e importar", key="import_config"):
        try:
            imported_configuration = configuration_from_json(imported.getvalue())
            save_configuration(imported_configuration)
            _set_configuration(imported_configuration, "Configuración importada y guardada.")
        except (ConfigurationValidationError, OSError) as exc:
            st.error(f"No se pudo importar: {exc}")


def _calculation_context(
    uploaded_file: UploadedFile | None,
    analysis: SheetAnalysis | None,
    configuration: BillingConfiguration,
) -> str | None:
    if uploaded_file is None or analysis is None:
        return None
    return _calculation_context_key(uploaded_file.getvalue(), analysis.sheet_name, configuration)


def _calculation_context_key(
    workbook_contents: bytes,
    sheet_name: str,
    configuration: BillingConfiguration,
) -> str:
    workbook_digest = sha256(workbook_contents).hexdigest()
    configuration_digest = sha256(configuration_to_json(configuration).encode()).hexdigest()
    return f"{workbook_digest}:{sheet_name}:{configuration_digest}"


def _workbook_calculation_context(
    uploaded_file: UploadedFile | None,
    configuration: BillingConfiguration,
) -> str | None:
    if uploaded_file is None:
        return None
    return _workbook_calculation_context_key(uploaded_file.getvalue(), configuration)


def _workbook_calculation_context_key(
    workbook_contents: bytes,
    configuration: BillingConfiguration,
) -> str:
    workbook_digest = sha256(workbook_contents).hexdigest()
    configuration_payload = json.loads(configuration_to_json(configuration))
    configuration_payload.pop("source_sheet", None)
    configuration_payload.pop("column_mapping", None)
    serialized_configuration = json.dumps(
        configuration_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    configuration_digest = sha256(serialized_configuration.encode()).hexdigest()
    return f"{workbook_digest}:{configuration_digest}"


def _discard_stale_calculation_result(
    state: MutableMapping[str, object],
    calculation_context: str | None,
    analysis: SheetAnalysis | None,
) -> None:
    """Elimina resultados que no pertenecen al fichero, hoja o configuración actuales."""
    result = state.get(_RESULT_KEY)
    context_changed = state.get(_RESULT_CONTEXT_KEY) != calculation_context
    sheet_changed = (
        result is not None
        and (
            not isinstance(result, SheetCalculationResult)
            or analysis is None
            or result.sheet_name != analysis.sheet_name
        )
    )
    if context_changed or sheet_changed:
        state.pop(_RESULT_KEY, None)
        state.pop(_RESULT_CONTEXT_KEY, None)


def _discard_stale_workbook_calculation_result(
    state: MutableMapping[str, object],
    calculation_context: str | None,
) -> None:
    """Elimina el resultado multihoja si cambia el libro o una regla de cálculo."""
    result = state.get(_WORKBOOK_RESULT_KEY)
    context_changed = state.get(_WORKBOOK_RESULT_CONTEXT_KEY) != calculation_context
    invalid_result = result is not None and not isinstance(result, WorkbookCalculationResult)
    if context_changed or invalid_result:
        state.pop(_WORKBOOK_RESULT_KEY, None)
        state.pop(_WORKBOOK_RESULT_CONTEXT_KEY, None)


def _render_calculation_outputs(result: SheetCalculationResult | None) -> None:
    if result is None:
        st.info("Ejecuta el cálculo para obtener el modelo limpio y las horas de gestión por cuenta.")
        return

    st.subheader("Resultado de facturación")
    _render_sheet_calculation_output(result)


def _render_workbook_calculation_outputs(result: WorkbookCalculationResult) -> None:
    st.subheader("Resultado de facturación")
    metrics = st.columns(3)
    metrics[0].metric("Hojas detectadas", result.detected_sheet_count, border=True, height="stretch")
    metrics[1].metric("Meses calculados", result.calculated_sheet_count, border=True, height="stretch")
    metrics[2].metric("Hojas omitidas", len(result.skipped_sheets), border=True, height="stretch")
    if result.skipped_sheets:
        details = "\n".join(
            f"- **{item.sheet_name}:** {item.reason}"
            for item in result.skipped_sheets
        )
        st.warning(f"Se omitieron hojas no compatibles:\n{details}")
    if not result.results_by_sheet:
        st.info("No se encontró ninguna hoja compatible para calcular.")
        return

    sheet_results = tuple(result.results_by_sheet.values())
    tabs = st.tabs([item.sheet_name for item in sheet_results])
    for index, (tab, sheet_result) in enumerate(zip(tabs, sheet_results, strict=True)):
        with tab:
            _render_sheet_calculation_output(sheet_result, key_suffix=f"_all_{index}")


def _render_sheet_calculation_output(
    result: SheetCalculationResult,
    *,
    key_suffix: str = "",
) -> None:
    summary = result.summary
    st.markdown(f"**Periodo:** {result.sheet_name}")
    st.markdown("#### Resumen del modelo")
    cleaning_metrics = st.columns(4)
    cleaning_metrics[0].metric(
        "Personas gestión detectadas",
        summary.management_people_detected,
        border=True,
        height="stretch",
    )
    cleaning_metrics[1].metric(
        "Asignaciones eliminadas",
        summary.assignments_removed,
        border=True,
        height="stretch",
    )
    cleaning_metrics[2].metric(
        "Horas eliminadas",
        _format_hours(summary.hours_removed),
        border=True,
        height="stretch",
    )
    cleaning_metrics[3].metric(
        "Excepciones conservadas",
        summary.exceptions_preserved,
        border=True,
        height="stretch",
    )

    st.markdown(f"#### Cuentas · {result.sheet_name}")
    account_metrics = st.columns(3)
    account_metrics[0].metric("Cuentas analizadas", summary.total_accounts, border=True, height="stretch")
    account_metrics[1].metric("Horas base", _format_hours(summary.total_base_hours), border=True, height="stretch")
    account_metrics[2].metric(
        "Horas de gestión",
        _format_hours(summary.total_management_hours),
        border=True,
        height="stretch",
    )
    type_metrics = st.columns(3)
    type_metrics[0].metric("Sin gestión", summary.accounts_without_management, border=True, height="stretch")
    type_metrics[1].metric("Gestión fija", summary.accounts_with_fixed_management, border=True, height="stretch")
    type_metrics[2].metric("Gestión calculada al 5%", summary.accounts_percentage_based, border=True, height="stretch")

    rows = pd.DataFrame(
        [
            {
                "Cuenta": item.account_code,
                "Servicio": item.service_name,
                "Horas base": item.base_hours,
                "Tipo cálculo": item.calculation_type.value,
                "5% teórico": item.raw_management_hours,
                "Horas gestión": item.management_hours,
            }
            for item in result.account_calculations
        ],
        columns=["Cuenta", "Servicio", "Horas base", "Tipo cálculo", "5% teórico", "Horas gestión"],
    )
    with st.container(horizontal=True, vertical_alignment="bottom"):
        search = st.text_input(
            "Filtrar cuentas o servicios",
            type="search",
            placeholder="Código o servicio",
            key=f"billing_result_search{key_suffix}",
        )
        selected_types = st.multiselect(
            "Tipo de cálculo",
            [item.value for item in CalculationType],
            placeholder="Todos",
            key=f"billing_result_types{key_suffix}",
        )
    filtered = rows
    if search:
        normalized_search = search.casefold()
        filtered = filtered[
            filtered["Cuenta"].str.casefold().str.contains(normalized_search, regex=False)
            | filtered["Servicio"].str.casefold().str.contains(normalized_search, regex=False)
        ]
    if selected_types:
        filtered = filtered[filtered["Tipo cálculo"].isin(selected_types)]
    st.dataframe(
        filtered,
        hide_index=True,
        width="stretch",
        alt="Cálculo de horas de gestión por cuenta",
        column_config={
            "Horas base": st.column_config.NumberColumn(format="%.2f h"),
            "5% teórico": st.column_config.NumberColumn(format="%.2f h"),
            "Horas gestión": st.column_config.NumberColumn(format="%.2f h"),
        },
    )

    _render_management_allocation(result.allocation_result, result.incidents, key_suffix)
    _render_rate_optimization(result.rate_optimization_result, key_suffix)

    with st.expander("Ver limpieza del modelo"):
        st.markdown("##### Asignaciones eliminadas")
        st.dataframe(
            _audit_dataframe(result.clean_model.removed_assignments),
            hide_index=True,
            width="stretch",
            alt="Asignaciones eliminadas del modelo original",
            column_config={"Horas": st.column_config.NumberColumn(format="%.2f h")},
        )
        st.markdown("##### Excepciones conservadas")
        st.dataframe(
            _audit_dataframe(result.clean_model.preserved_exceptions),
            hide_index=True,
            width="stretch",
            alt="Excepciones persona-cuenta conservadas",
            column_config={"Horas": st.column_config.NumberColumn(format="%.2f h")},
        )

    st.caption("La generación del Excel final sigue pendiente.")


def _render_management_allocation(
    allocation: ManagementAllocationResult | None,
    incidents: tuple[str, ...],
    key_suffix: str,
) -> None:
    st.markdown("#### Reparto entre Personas en Gestión")
    if allocation is None:
        message = incidents[0] if incidents else "No hay horas de gestión que repartir."
        st.info(message)
        return

    metrics = st.columns(5)
    metrics[0].metric(
        "Horas gestión necesarias",
        _format_hours(allocation.total_management_hours),
        border=True,
        height="stretch",
    )
    metrics[1].metric(
        "Objetivo base total gestores",
        _format_hours(allocation.total_base_target_hours),
        border=True,
        height="stretch",
    )
    metrics[2].metric(
        "Excedente de gestión",
        _format_hours(allocation.overflow_hours),
        border=True,
        height="stretch",
    )
    metrics[3].metric(
        "Horas repartidas",
        _format_hours(allocation.total_assigned_hours),
        border=True,
        height="stretch",
    )
    metrics[4].metric(
        "Diferencia",
        _format_hours(allocation.allocation_difference),
        border=True,
        height="stretch",
    )
    if allocation.overflow_hours:
        st.info(
            "La gestión supera los objetivos base en "
            f"{_format_hours(allocation.overflow_hours)}; el excedente se reparte equitativamente."
        )
    if allocation.incidents:
        st.warning("\n".join(f"- {incident}" for incident in allocation.incidents))

    summary_rows = pd.DataFrame([
        {
            "Gestor": item.manager_name,
            "Capacidad %": item.capacity_percentage,
            "Horas laborables mes": item.monthly_workable_hours,
            "Objetivo base": item.base_target_hours,
            "Rango preferente": f"{item.preferred_min_hours:.2f}–{item.preferred_max_hours:.2f} h",
            "Horas gestión asignadas": item.assigned_hours,
            "Excedente asignado": item.overflow_hours_assigned,
            "Nº cuentas": item.accounts_assigned,
            "Estado": item.status.value,
        }
        for item in allocation.manager_summaries
    ])
    st.dataframe(
        summary_rows,
        hide_index=True,
        width="stretch",
        alt="Resumen del reparto de gestión por persona",
        column_config={
            "Capacidad %": st.column_config.NumberColumn(format="%.1f %%"),
            "Horas laborables mes": st.column_config.NumberColumn(format="%.2f h"),
            "Objetivo base": st.column_config.NumberColumn(format="%.2f h"),
            "Horas gestión asignadas": st.column_config.NumberColumn(format="%.2f h"),
            "Excedente asignado": st.column_config.NumberColumn(format="%.2f h"),
        },
    )

    with st.expander("Ver cálculo del objetivo base"):
        capacity_rows = pd.DataFrame([
            {
                "Gestor": item.manager_name,
                "Hoja": allocation.sheet_name,
                "Horas laborables": item.monthly_workable_hours,
                "Capacidad %": item.capacity_percentage,
                "Objetivo base": item.base_target_hours,
                "Origen del dato": item.capacity_source.value if item.capacity_source is not None else "NO ENCONTRADO",
            }
            for item in allocation.manager_summaries
        ])
        st.dataframe(
            capacity_rows,
            hide_index=True,
            width="stretch",
            alt="Origen y cálculo del objetivo base mensual por gestor",
            column_config={
                "Horas laborables": st.column_config.NumberColumn(format="%.2f h"),
                "Capacidad %": st.column_config.NumberColumn(format="%.1f %%"),
                "Objetivo base": st.column_config.NumberColumn(format="%.2f h"),
            },
        )

    st.markdown("##### Detalle del reparto")
    manager_options = tuple(item.manager_name for item in allocation.manager_summaries)
    with st.container(horizontal=True, vertical_alignment="bottom"):
        selected_managers = st.multiselect(
            "Filtrar por gestor",
            manager_options,
            placeholder="Todos",
            key=f"allocation_managers{key_suffix}",
        )
        account_search = st.text_input(
            "Filtrar por cuenta",
            type="search",
            placeholder="Código de cuenta",
            key=f"allocation_account{key_suffix}",
        )
    detail_rows = pd.DataFrame([
        {
            "Gestor": item.manager_name,
            "Cuenta": item.account_code,
            "Servicio": item.service_name,
            "Horas gestión": item.management_hours,
        }
        for item in allocation.assignments
    ], columns=["Gestor", "Cuenta", "Servicio", "Horas gestión"])
    if selected_managers:
        detail_rows = detail_rows[detail_rows["Gestor"].isin(selected_managers)]
    if account_search:
        detail_rows = detail_rows[
            detail_rows["Cuenta"].str.casefold().str.contains(account_search.casefold(), regex=False)
        ]
    st.dataframe(
        detail_rows,
        hide_index=True,
        width="stretch",
        alt="Asignaciones de horas de gestión por persona y cuenta",
        column_config={"Horas gestión": st.column_config.NumberColumn(format="%.2f h")},
    )


def _render_rate_optimization(
    optimization: RateOptimizationResult | None,
    key_suffix: str,
) -> None:
    st.markdown("#### Optimización de tarifas")
    if optimization is None:
        st.info("El reparto debe completarse antes de aplicar tarifas de gestión.")
        return

    summary = optimization.billing_summary
    metrics = st.columns(5)
    metrics[0].metric(
        "Tarifa media",
        f"{summary.weighted_average_rate:.2f} €/h" if summary.weighted_average_rate is not None else "Sin tarifa",
        border=True,
        height="stretch",
    )
    metrics[1].metric(
        "Importe total gestión",
        f"{summary.total_management_amount:.2f} €",
        border=True,
        height="stretch",
    )
    metrics[2].metric(
        "Cuentas con bloque",
        summary.configured_block_accounts,
        border=True,
        height="stretch",
    )
    metrics[3].metric(
        "Cuentas optimizadas",
        summary.automatically_optimized_accounts,
        border=True,
        height="stretch",
    )
    metrics[4].metric(
        "Incidencias",
        len(optimization.incidents),
        border=True,
        height="stretch",
    )
    if optimization.incidents:
        details = "\n".join(
            f"- **{item.code.value}:** {item.message}"
            for item in optimization.incidents
        )
        st.warning(f"Incidencias de tarifas:\n{details}")

    rows = pd.DataFrame([
        {
            "Gestor": item.manager_name,
            "Cuenta": item.account_code,
            "Horas": item.management_hours,
            "Tarifa": item.management_rate,
            "Importe": item.amount,
            "Origen tarifa": item.rate_source.value if item.rate_source is not None else "SIN TARIFA",
            "Estado": item.rate_status,
        }
        for item in optimization.assignments
    ], columns=["Gestor", "Cuenta", "Horas", "Tarifa", "Importe", "Origen tarifa", "Estado"])
    manager_options = tuple(item.manager_name for item in optimization.manager_summaries)
    with st.container(horizontal=True, vertical_alignment="bottom"):
        selected_managers = st.multiselect(
            "Filtrar tarifas por gestor",
            manager_options,
            placeholder="Todos",
            key=f"rate_managers{key_suffix}",
        )
        account_search = st.text_input(
            "Filtrar tarifas por cuenta",
            type="search",
            placeholder="Código de cuenta",
            key=f"rate_account{key_suffix}",
        )
    if selected_managers:
        rows = rows[rows["Gestor"].isin(selected_managers)]
    if account_search:
        rows = rows[rows["Cuenta"].str.casefold().str.contains(account_search.casefold(), regex=False)]
    st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        alt="Tarifas e importes de las asignaciones de gestión",
        column_config={
            "Horas": st.column_config.NumberColumn(format="%.2f h"),
            "Tarifa": st.column_config.NumberColumn(format="%.2f €/h"),
            "Importe": st.column_config.NumberColumn(format="%.2f €"),
        },
    )


def _audit_dataframe(records: Iterable) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Persona": record.person_name,
            "Cuenta": record.account_code,
            "Servicio": record.service_name,
            "Horas": record.hours,
        }
        for record in records
    ], columns=["Persona", "Cuenta", "Servicio", "Horas"])


def _format_hours(value: float) -> str:
    return f"{value:,.2f} h".replace(",", "X").replace(".", ",").replace("X", ".")
