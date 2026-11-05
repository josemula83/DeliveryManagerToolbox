"""Logging centralizado para toda la aplicación."""

from __future__ import annotations

import logging

from delivery_manager_toolbox.core.config import LoggingSettings


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging(settings: LoggingSettings) -> None:
    """Configura consola y fichero una sola vez por proceso."""
    settings.file.parent.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    if getattr(root_logger, "_dmt_configured", False):
        return

    root_logger.setLevel(settings.level)
    formatter = logging.Formatter(LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(settings.file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger._dmt_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

