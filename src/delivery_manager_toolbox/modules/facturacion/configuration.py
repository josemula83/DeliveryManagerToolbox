"""Modelo, reglas y persistencia de la configuración de Facturación."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path

from delivery_manager_toolbox.modules.facturacion.models import (
    AccountAssignment,
    ColumnMapping,
    Person,
    SheetAnalysis,
)
from delivery_manager_toolbox.modules.facturacion.validation import (
    ConfigurationValidationError,
    clean_display_value,
    normalize_identifier,
    validate_fixed_hours,
    validate_management_rate,
    validate_percentage,
    validate_unique,
)


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIGURATION_PATH = PROJECT_ROOT / "config" / "facturacion.json"
SCHEMA_VERSION = 3


@dataclass(frozen=True, slots=True)
class ManagementPerson:
    name: str
    free_capacity_percentage: float


@dataclass(frozen=True, slots=True)
class FixedManagementAccount:
    account: str
    fixed_hours: float


@dataclass(frozen=True, slots=True)
class ManagementRateBlock:
    name: str
    accounts: tuple[str, ...]
    management_rate: float


@dataclass(frozen=True, slots=True)
class ManagementPersonAccountException:
    employee_id: str
    person_name: str
    account_code: str


@dataclass(frozen=True, slots=True)
class BillingConfiguration:
    management_people: tuple[ManagementPerson, ...] = ()
    no_management_accounts: tuple[str, ...] = ()
    fixed_management_accounts: tuple[FixedManagementAccount, ...] = ()
    management_rate_blocks: tuple[ManagementRateBlock, ...] = ()
    management_person_account_exceptions: tuple[ManagementPersonAccountException, ...] = ()
    source_sheet: str | None = None
    column_mapping: ColumnMapping = ColumnMapping()


def add_management_person(configuration: BillingConfiguration, name: object, percentage: object) -> BillingConfiguration:
    cleaned_name = clean_display_value(name, "El nombre")
    _ensure_absent((person.name for person in configuration.management_people), cleaned_name, "persona")
    people = (*configuration.management_people, ManagementPerson(cleaned_name, validate_percentage(percentage)))
    return _validated(replace(configuration, management_people=people))


def update_management_person_percentage(
    configuration: BillingConfiguration,
    name: object,
    percentage: object,
) -> BillingConfiguration:
    key = normalize_identifier(name)
    found = False
    people: list[ManagementPerson] = []
    for person in configuration.management_people:
        if normalize_identifier(person.name) == key:
            people.append(ManagementPerson(person.name, validate_percentage(percentage)))
            found = True
        else:
            people.append(person)
    if not found:
        raise ConfigurationValidationError(f"No existe la persona: {name}.")
    return _validated(replace(configuration, management_people=tuple(people)))


def replace_management_people(
    configuration: BillingConfiguration,
    people: list[tuple[object, object]],
) -> BillingConfiguration:
    records = tuple(
        ManagementPerson(clean_display_value(name, "El nombre"), validate_percentage(percentage))
        for name, percentage in people
    )
    retained_names = {normalize_identifier(person.name) for person in records}
    exceptions = tuple(
        exception
        for exception in configuration.management_person_account_exceptions
        if normalize_identifier(exception.person_name) in retained_names
    )
    return _validated(replace(
        configuration,
        management_people=records,
        management_person_account_exceptions=exceptions,
    ))


def add_management_person_account_exception(
    configuration: BillingConfiguration,
    employee_id: object,
    person_name: object,
    account_code: object,
    assignments: Iterable[AccountAssignment],
) -> BillingConfiguration:
    exception = ManagementPersonAccountException(
        clean_display_value(employee_id, "El identificador de empleado"),
        clean_display_value(person_name, "El nombre de la persona"),
        clean_display_value(account_code, "El código de cuenta"),
    )
    _validate_exception_against_assignments(configuration, exception, assignments)
    exceptions = (*configuration.management_person_account_exceptions, exception)
    return _validated(replace(configuration, management_person_account_exceptions=exceptions))


def remove_management_person_account_exception(
    configuration: BillingConfiguration,
    employee_id: object,
    account_code: object,
) -> BillingConfiguration:
    key = _exception_key(employee_id, account_code)
    exceptions = tuple(
        exception
        for exception in configuration.management_person_account_exceptions
        if _exception_key(exception.employee_id, exception.account_code) != key
    )
    if len(exceptions) == len(configuration.management_person_account_exceptions):
        raise ConfigurationValidationError("No existe la excepción persona-cuenta indicada.")
    return _validated(replace(configuration, management_person_account_exceptions=exceptions))


def available_exception_people(
    configuration: BillingConfiguration,
    analysis: SheetAnalysis,
) -> tuple[Person, ...]:
    management_names = {normalize_identifier(person.name) for person in configuration.management_people}
    return tuple(
        person for person in analysis.people
        if normalize_identifier(person.name) in management_names
    )


def available_exception_accounts(
    configuration: BillingConfiguration,
    analysis: SheetAnalysis,
    employee_id: object,
    person_name: object,
) -> tuple[AccountAssignment, ...]:
    assignments = _assignments_for_person(analysis.assignments, employee_id, person_name)
    configured_codes = {
        normalize_identifier(exception.account_code)
        for exception in configuration.management_person_account_exceptions
        if normalize_identifier(exception.employee_id) == normalize_identifier(employee_id)
    }
    unique: dict[str, AccountAssignment] = {}
    for assignment in assignments:
        if assignment.account_code is None:
            continue
        code_key = normalize_identifier(assignment.account_code)
        if code_key not in configured_codes:
            unique.setdefault(code_key, assignment)
    return tuple(unique.values())


def reconcile_management_person_account_exceptions(
    configuration: BillingConfiguration,
    analysis: SheetAnalysis,
) -> tuple[BillingConfiguration, int]:
    management_names = {normalize_identifier(person.name) for person in configuration.management_people}
    valid: list[ManagementPersonAccountException] = []
    for exception in configuration.management_person_account_exceptions:
        if normalize_identifier(exception.person_name) not in management_names:
            continue
        assignments = _assignments_for_person(
            analysis.assignments,
            exception.employee_id,
            exception.person_name,
        )
        if any(
            assignment.account_code is not None
            and normalize_identifier(assignment.account_code) == normalize_identifier(exception.account_code)
            for assignment in assignments
        ):
            valid.append(exception)
    removed = len(configuration.management_person_account_exceptions) - len(valid)
    if not removed:
        return configuration, 0
    reconciled = _validated(replace(
        configuration,
        management_person_account_exceptions=tuple(valid),
    ))
    return reconciled, removed


def add_no_management_account(configuration: BillingConfiguration, account: object) -> BillingConfiguration:
    cleaned = clean_display_value(account, "La cuenta")
    _ensure_absent(configuration.no_management_accounts, cleaned, "cuenta sin gestión")
    block = _block_containing_account(configuration.management_rate_blocks, cleaned)
    if block is not None:
        raise ConfigurationValidationError(
            f"La cuenta {cleaned} ya pertenece al bloque {block.name}. "
            "Elimínala del bloque antes de marcarla como cuenta sin gestión."
        )
    return _validated(replace(
        configuration,
        no_management_accounts=(*configuration.no_management_accounts, cleaned),
    ))


def replace_no_management_accounts(
    configuration: BillingConfiguration,
    accounts: list[object],
) -> BillingConfiguration:
    records = tuple(clean_display_value(account, "La cuenta") for account in accounts)
    return _validated(replace(configuration, no_management_accounts=records))


def add_fixed_management_account(
    configuration: BillingConfiguration,
    account: object,
    hours: object,
) -> BillingConfiguration:
    cleaned = clean_display_value(account, "La cuenta")
    _ensure_absent(
        (record.account for record in configuration.fixed_management_accounts),
        cleaned,
        "cuenta con gestión fija",
    )
    records = (*configuration.fixed_management_accounts, FixedManagementAccount(cleaned, validate_fixed_hours(hours)))
    return _validated(replace(configuration, fixed_management_accounts=records))


def replace_fixed_management_accounts(
    configuration: BillingConfiguration,
    accounts: list[tuple[object, object]],
) -> BillingConfiguration:
    records = tuple(
        FixedManagementAccount(clean_display_value(account, "La cuenta"), validate_fixed_hours(hours))
        for account, hours in accounts
    )
    return _validated(replace(configuration, fixed_management_accounts=records))


def add_management_rate_block(
    configuration: BillingConfiguration,
    name: object,
    accounts: list[object] | tuple[object, ...],
    management_rate: object,
) -> BillingConfiguration:
    cleaned_name = clean_display_value(name, "El nombre del bloque")
    _ensure_absent((block.name for block in configuration.management_rate_blocks), cleaned_name, "bloque")
    block = ManagementRateBlock(
        cleaned_name,
        tuple(clean_display_value(account, "La cuenta") for account in accounts),
        validate_management_rate(management_rate),
    )
    return _validated(replace(
        configuration,
        management_rate_blocks=(*configuration.management_rate_blocks, block),
    ))


def replace_management_rate_block(
    configuration: BillingConfiguration,
    original_name: object,
    name: object,
    accounts: list[object] | tuple[object, ...],
    management_rate: object,
) -> BillingConfiguration:
    original_key = normalize_identifier(original_name)
    replacement = ManagementRateBlock(
        clean_display_value(name, "El nombre del bloque"),
        tuple(clean_display_value(account, "La cuenta") for account in accounts),
        validate_management_rate(management_rate),
    )
    found = False
    blocks: list[ManagementRateBlock] = []
    for block in configuration.management_rate_blocks:
        if normalize_identifier(block.name) == original_key:
            blocks.append(replacement)
            found = True
        else:
            blocks.append(block)
    if not found:
        raise ConfigurationValidationError(f"No existe el bloque: {original_name}.")
    return _validated(replace(configuration, management_rate_blocks=tuple(blocks)))


def remove_management_rate_block(configuration: BillingConfiguration, name: object) -> BillingConfiguration:
    key = normalize_identifier(name)
    blocks = tuple(
        block for block in configuration.management_rate_blocks
        if normalize_identifier(block.name) != key
    )
    if len(blocks) == len(configuration.management_rate_blocks):
        raise ConfigurationValidationError(f"No existe el bloque: {name}.")
    return _validated(replace(configuration, management_rate_blocks=blocks))


def with_source_mapping(
    configuration: BillingConfiguration,
    sheet_name: str | None,
    mapping: ColumnMapping,
) -> BillingConfiguration:
    cleaned_sheet = clean_display_value(sheet_name, "La hoja") if sheet_name else None
    cleaned_mapping = ColumnMapping(
        person=_optional_text(mapping.person),
        account=_optional_text(mapping.account),
        hours=_optional_text(mapping.hours),
        rate=_optional_text(mapping.rate),
    )
    return _validated(replace(configuration, source_sheet=cleaned_sheet, column_mapping=cleaned_mapping))


def with_source_sheet(configuration: BillingConfiguration, sheet_name: str | None) -> BillingConfiguration:
    cleaned_sheet = clean_display_value(sheet_name, "La hoja") if sheet_name else None
    return _validated(replace(configuration, source_sheet=cleaned_sheet))


def available_values(candidates: Iterable[str], configured: Iterable[str]) -> tuple[str, ...]:
    configured_keys = {normalize_identifier(value) for value in configured}
    return tuple(value for value in candidates if normalize_identifier(value) not in configured_keys)


def available_management_rate_accounts(
    detected_accounts: Iterable[str],
    configuration: BillingConfiguration,
    editing_block_name: str | None = None,
) -> tuple[str, ...]:
    """Calcula el catálogo permitido para crear o editar un bloque de tarifa."""
    detected = list(detected_accounts)
    editing_key = normalize_identifier(editing_block_name) if editing_block_name else None
    current_accounts: tuple[str, ...] = ()
    blocked_by_other_blocks: list[str] = []
    for block in configuration.management_rate_blocks:
        if editing_key is not None and normalize_identifier(block.name) == editing_key:
            current_accounts = block.accounts
        else:
            blocked_by_other_blocks.extend(block.accounts)
    candidates = tuple(dict.fromkeys((*detected, *current_accounts)))
    excluded = (*configuration.no_management_accounts, *blocked_by_other_blocks)
    return available_values(candidates, excluded)


def available_no_management_accounts(
    detected_accounts: Iterable[str],
    configuration: BillingConfiguration,
) -> tuple[str, ...]:
    block_accounts = (
        account
        for block in configuration.management_rate_blocks
        for account in block.accounts
    )
    excluded = (*configuration.no_management_accounts, *tuple(block_accounts))
    return available_values(detected_accounts, excluded)


def validate_configuration(configuration: BillingConfiguration) -> None:
    validate_unique((person.name for person in configuration.management_people), "personas en gestión")
    for person in configuration.management_people:
        clean_display_value(person.name, "El nombre")
        validate_percentage(person.free_capacity_percentage)

    validate_unique(configuration.no_management_accounts, "cuentas sin gestión")
    validate_unique(
        (record.account for record in configuration.fixed_management_accounts),
        "cuentas con gestión fija",
    )
    for record in configuration.fixed_management_accounts:
        clean_display_value(record.account, "La cuenta")
        validate_fixed_hours(record.fixed_hours)

    validate_unique((block.name for block in configuration.management_rate_blocks), "bloques de tarifa de gestión")
    blocked_accounts: dict[str, str] = {}
    for block in configuration.management_rate_blocks:
        clean_display_value(block.name, "El nombre del bloque")
        if not block.accounts:
            raise ConfigurationValidationError(f"El bloque {block.name} debe contener al menos una cuenta.")
        validate_unique(block.accounts, f"el bloque {block.name}")
        validate_management_rate(block.management_rate)
        for account in block.accounts:
            key = normalize_identifier(account)
            if key in blocked_accounts:
                raise ConfigurationValidationError(
                    f"La cuenta {account} ya pertenece al bloque {blocked_accounts[key]}."
                )
            blocked_accounts[key] = block.name

    no_management_keys = {normalize_identifier(account) for account in configuration.no_management_accounts}
    fixed_keys = {normalize_identifier(record.account) for record in configuration.fixed_management_accounts}
    conflicting_fixed = no_management_keys & fixed_keys
    if conflicting_fixed:
        raise ConfigurationValidationError(
            "Una cuenta sin gestión no puede tener horas de gestión fija: "
            + ", ".join(sorted(conflicting_fixed))
        )
    conflicting_blocks = no_management_keys & set(blocked_accounts)
    if conflicting_blocks:
        account_key = next(iter(conflicting_blocks))
        raise ConfigurationValidationError(
            f"La cuenta {account_key} está configurada como cuenta sin gestión y también pertenece "
            f"al bloque {blocked_accounts[account_key]}."
        )

    exception_keys: set[tuple[str, str]] = set()
    management_names = {normalize_identifier(person.name) for person in configuration.management_people}
    for exception in configuration.management_person_account_exceptions:
        clean_display_value(exception.employee_id, "El identificador de empleado")
        clean_display_value(exception.person_name, "El nombre de la persona")
        clean_display_value(exception.account_code, "El código de cuenta")
        if normalize_identifier(exception.person_name) not in management_names:
            raise ConfigurationValidationError(
                f"La persona {exception.person_name} no está configurada como Persona en Gestión."
            )
        key = _exception_key(exception.employee_id, exception.account_code)
        if key in exception_keys:
            raise ConfigurationValidationError(
                f"Ya existe una excepción para {exception.person_name} y {exception.account_code}."
            )
        exception_keys.add(key)

    mapped_columns = [column for column in asdict(configuration.column_mapping).values() if column is not None]
    validate_unique(mapped_columns, "el mapeo de columnas")


def configuration_to_json(configuration: BillingConfiguration) -> str:
    validate_configuration(configuration)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "management_people": [asdict(person) for person in configuration.management_people],
        "no_management_accounts": list(configuration.no_management_accounts),
        "fixed_management_accounts": [asdict(account) for account in configuration.fixed_management_accounts],
        "management_rate_blocks": [
            {**asdict(block), "accounts": list(block.accounts)}
            for block in configuration.management_rate_blocks
        ],
        "management_person_account_exceptions": [
            asdict(exception)
            for exception in configuration.management_person_account_exceptions
        ],
        "source_sheet": configuration.source_sheet,
        "column_mapping": asdict(configuration.column_mapping),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def configuration_from_json(contents: str | bytes) -> BillingConfiguration:
    try:
        if isinstance(contents, bytes):
            contents = contents.decode("utf-8")
        payload = json.loads(contents)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationValidationError("El JSON de configuración no es válido.") from exc
    if not isinstance(payload, dict):
        raise ConfigurationValidationError("La configuración debe ser un objeto JSON.")
    version = payload.get("schema_version")
    if version == 1:
        payload = _migrate_schema_v2(_migrate_schema_v1(payload))
    elif version == 2:
        payload = _migrate_schema_v2(payload)
    elif version != SCHEMA_VERSION:
        raise ConfigurationValidationError("La versión del JSON de configuración no es compatible.")

    try:
        people_payload = _required_list(payload, "management_people")
        no_management_payload = _required_list(payload, "no_management_accounts")
        fixed_payload = _required_list(payload, "fixed_management_accounts")
        blocks_payload = _required_list(payload, "management_rate_blocks")
        exceptions_payload = _required_list(payload, "management_person_account_exceptions")
        mapping_payload = payload.get("column_mapping", {})
        if not isinstance(mapping_payload, dict):
            raise ConfigurationValidationError("column_mapping debe ser un objeto JSON.")
        configuration = BillingConfiguration(
            management_people=tuple(
                ManagementPerson(
                    clean_display_value(item["name"], "El nombre"),
                    validate_percentage(item["free_capacity_percentage"]),
                )
                for item in _object_items(people_payload, "management_people")
            ),
            no_management_accounts=tuple(
                clean_display_value(account, "La cuenta") for account in no_management_payload
            ),
            fixed_management_accounts=tuple(
                FixedManagementAccount(
                    clean_display_value(item["account"], "La cuenta"),
                    validate_fixed_hours(item["fixed_hours"]),
                )
                for item in _object_items(fixed_payload, "fixed_management_accounts")
            ),
            management_rate_blocks=tuple(
                ManagementRateBlock(
                    clean_display_value(item["name"], "El nombre del bloque"),
                    tuple(
                        clean_display_value(account, "La cuenta")
                        for account in _required_list(item, "accounts")
                    ),
                    validate_management_rate(item["management_rate"]),
                )
                for item in _object_items(blocks_payload, "management_rate_blocks")
            ),
            management_person_account_exceptions=tuple(
                ManagementPersonAccountException(
                    clean_display_value(item["employee_id"], "El identificador de empleado"),
                    clean_display_value(item["person_name"], "El nombre de la persona"),
                    clean_display_value(item["account_code"], "El código de cuenta"),
                )
                for item in _object_items(
                    exceptions_payload,
                    "management_person_account_exceptions",
                )
            ),
            source_sheet=_optional_text(payload.get("source_sheet")),
            column_mapping=ColumnMapping(
                person=_optional_text(mapping_payload.get("person")),
                account=_optional_text(mapping_payload.get("account")),
                hours=_optional_text(mapping_payload.get("hours")),
                rate=_optional_text(mapping_payload.get("rate")),
            ),
        )
    except (KeyError, TypeError) as exc:
        raise ConfigurationValidationError("Faltan campos obligatorios en el JSON de configuración.") from exc
    validate_configuration(configuration)
    return configuration


def save_configuration(configuration: BillingConfiguration, path: Path = DEFAULT_CONFIGURATION_PATH) -> None:
    serialized = configuration_to_json(configuration)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(serialized, encoding="utf-8")
    temporary_path.replace(path)


def load_configuration(path: Path = DEFAULT_CONFIGURATION_PATH) -> BillingConfiguration:
    if not path.exists():
        return BillingConfiguration()
    return configuration_from_json(path.read_bytes())


def _migrate_schema_v1(payload: dict) -> dict:
    legacy_blocks = _required_list(payload, "fixed_amount_groups")
    migrated_blocks = []
    for item in _object_items(legacy_blocks, "fixed_amount_groups"):
        legacy_rate = item.get("fixed_amount")
        migrated_blocks.append({
            "name": item.get("name"),
            "accounts": item.get("accounts", []),
            "management_rate": 0.0 if legacy_rate is None else legacy_rate,
        })
    return {
        "schema_version": 2,
        "management_people": payload.get("management_people", []),
        "no_management_accounts": payload.get("non_billable_accounts", []),
        "fixed_management_accounts": payload.get("fixed_management_accounts", []),
        "management_rate_blocks": migrated_blocks,
        "source_sheet": payload.get("source_sheet"),
        "column_mapping": payload.get("column_mapping", {}),
    }


def _migrate_schema_v2(payload: dict) -> dict:
    """Migra v2 sin intentar convertir el antiguo booleano global en excepciones."""
    return {
        "schema_version": SCHEMA_VERSION,
        "management_people": payload.get("management_people", []),
        "no_management_accounts": payload.get("no_management_accounts", []),
        "fixed_management_accounts": payload.get("fixed_management_accounts", []),
        "management_rate_blocks": payload.get("management_rate_blocks", []),
        "management_person_account_exceptions": [],
        "source_sheet": payload.get("source_sheet"),
        "column_mapping": payload.get("column_mapping", {}),
    }


def _validated(configuration: BillingConfiguration) -> BillingConfiguration:
    validate_configuration(configuration)
    return configuration


def _block_containing_account(
    blocks: Iterable[ManagementRateBlock],
    account: object,
) -> ManagementRateBlock | None:
    account_key = normalize_identifier(account)
    return next(
        (block for block in blocks if any(normalize_identifier(item) == account_key for item in block.accounts)),
        None,
    )


def _validate_exception_against_assignments(
    configuration: BillingConfiguration,
    exception: ManagementPersonAccountException,
    assignments: Iterable[AccountAssignment],
) -> None:
    management_names = {normalize_identifier(person.name) for person in configuration.management_people}
    if normalize_identifier(exception.person_name) not in management_names:
        raise ConfigurationValidationError(
            f"La persona {exception.person_name} no está configurada como Persona en Gestión."
        )
    if any(
        _exception_key(item.employee_id, item.account_code)
        == _exception_key(exception.employee_id, exception.account_code)
        for item in configuration.management_person_account_exceptions
    ):
        raise ConfigurationValidationError(
            f"Ya existe una excepción para {exception.person_name} y {exception.account_code}."
        )
    person_assignments = _assignments_for_person(
        assignments,
        exception.employee_id,
        exception.person_name,
    )
    if not any(
        assignment.account_code is not None
        and normalize_identifier(assignment.account_code) == normalize_identifier(exception.account_code)
        for assignment in person_assignments
    ):
        raise ConfigurationValidationError(
            f"La persona {exception.person_name} no aparece en la cuenta {exception.account_code} "
            "del Excel activo."
        )


def _assignments_for_person(
    assignments: Iterable[AccountAssignment],
    employee_id: object,
    person_name: object,
) -> tuple[AccountAssignment, ...]:
    records = tuple(assignments)
    employee_key = normalize_identifier(employee_id)
    by_employee_id = tuple(
        assignment
        for assignment in records
        if normalize_identifier(assignment.person.employee_id) == employee_key
    )
    if by_employee_id:
        return by_employee_id
    name_key = normalize_identifier(person_name)
    return tuple(
        assignment
        for assignment in records
        if normalize_identifier(assignment.person.name) == name_key
    )


def _exception_key(employee_id: object, account_code: object) -> tuple[str, str]:
    return normalize_identifier(employee_id), normalize_identifier(account_code)


def _ensure_absent(existing: Iterable[object], candidate: object, label: str) -> None:
    candidate_key = normalize_identifier(candidate)
    if any(normalize_identifier(value) == candidate_key for value in existing):
        raise ConfigurationValidationError(f"Ya existe un registro para esta {label}: {candidate}.")


def _optional_text(value: object | None) -> str | None:
    if value is None or value == "":
        return None
    return clean_display_value(value, "El valor")


def _required_list(payload: dict, key: str) -> list:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise ConfigurationValidationError(f"{key} debe ser una lista JSON.")
    return value


def _object_items(items: list, key: str) -> list[dict]:
    if not all(isinstance(item, dict) for item in items):
        raise ConfigurationValidationError(f"Todos los elementos de {key} deben ser objetos JSON.")
    return items
