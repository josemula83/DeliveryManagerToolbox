"""Descubrimiento de módulos sin registro central manual."""

from __future__ import annotations

import importlib
import pkgutil

from delivery_manager_toolbox import modules as modules_package
from delivery_manager_toolbox.core.models import ModuleDefinition


def discover_modules() -> tuple[ModuleDefinition, ...]:
    discovered: list[ModuleDefinition] = []

    for info in pkgutil.iter_modules(modules_package.__path__):
        if not info.ispkg or info.name.startswith("_"):
            continue
        package = importlib.import_module(f"{modules_package.__name__}.{info.name}")
        definition = getattr(package, "MODULE", None)
        if not isinstance(definition, ModuleDefinition):
            continue
        discovered.append(definition)

    keys = [module.key for module in discovered]
    if len(keys) != len(set(keys)):
        raise ValueError("Cada módulo debe tener una clave única")
    if not discovered:
        raise RuntimeError("No se ha encontrado ningún módulo válido")

    return tuple(sorted(discovered, key=lambda module: (module.order, module.title)))

