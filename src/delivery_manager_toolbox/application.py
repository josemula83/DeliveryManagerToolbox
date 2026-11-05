"""Composición de la interfaz web local."""

from __future__ import annotations

import streamlit as st

from delivery_manager_toolbox.core.config import load_settings
from delivery_manager_toolbox.core.logging import configure_logging, get_logger
from delivery_manager_toolbox.core.registry import discover_modules


def main() -> None:
    settings = load_settings()
    configure_logging(settings.logging)
    logger = get_logger(__name__)
    modules = discover_modules()

    st.set_page_config(
        page_title=settings.application.name,
        page_icon="🧰",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    with st.sidebar:
        st.title("🧰 Toolbox")
        st.caption(settings.application.subtitle)
        labels = [module.title for module in modules]
        selected_label = st.radio("Módulo", labels, label_visibility="collapsed")

    selected = next(module for module in modules if module.title == selected_label)
    logger.info("Módulo seleccionado: %s", selected.key)
    selected.render()

