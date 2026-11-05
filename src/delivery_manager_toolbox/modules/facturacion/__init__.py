from delivery_manager_toolbox.core.models import ModuleDefinition


def render() -> None:
    from delivery_manager_toolbox.modules.facturacion.view import render_page

    render_page()


MODULE = ModuleDefinition("facturacion", "Facturación", "Procesos de facturación", 50, render)

