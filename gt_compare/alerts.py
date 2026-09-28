"""Alertas de precio por WhatsApp: selección de las que tocan y envío detrás de flag.

El envío está apagado por defecto (`ALERTS_SEND_ENABLED` distinto de "1") y no
hay proveedor de WhatsApp contratado: con el flag apagado solo se cuenta a
quién se le avisaría; con el flag encendido y sin proveedor, falla en voz alta.
"""

from __future__ import annotations

import logging
import os

from .db import Database
from .history import seen_since

logger = logging.getLogger("gt_compare.alerts")


def send_enabled() -> bool:
    return os.getenv("ALERTS_SEND_ENABLED") == "1"


def due(db: Database, day: str) -> list[dict]:
    """Suscripciones activas cuyo producto, visto en la última corrida y disponible, llegó al precio pedido."""
    return db.query(
        """SELECT a.id, a.whatsapp, a.target_price, p.id AS product_id, p.name, p.cur_price, p.url
           FROM alert_subscriptions a JOIN products p ON p.id = a.product_id
           WHERE a.status = 'active' AND p.id IN (SELECT product_id FROM price_history WHERE end_day >= ?)
             AND COALESCE(p.cur_available, 1) > 0
             AND p.cur_price <= a.target_price""",
        (seen_since(day),),
    )


def send_due(db: Database, day: str) -> dict:
    pending = due(db, day)
    if not send_enabled():
        logger.info("alertas: %s por avisar, envío apagado", len(pending))
        return {"due": len(pending), "sent": 0, "enabled": False}
    raise RuntimeError("ALERTS_SEND_ENABLED=1 pero no hay proveedor de WhatsApp configurado")
