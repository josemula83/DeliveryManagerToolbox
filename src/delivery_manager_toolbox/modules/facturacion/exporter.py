"""Punto de extensión para la futura exportación del resultado."""

from __future__ import annotations

from delivery_manager_toolbox.modules.facturacion.models import BillingResult


def export_billing_result(result: BillingResult) -> bytes:
    """Generará el Excel final cuando exista un resultado de cálculo."""
    del result
    raise NotImplementedError("La exportación de facturación está pendiente.")

