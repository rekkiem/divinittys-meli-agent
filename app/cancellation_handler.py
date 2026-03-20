"""
cancellation_handler.py — Detección y manejo de órdenes canceladas.

Mercado Libre permite al comprador cancelar una orden pagada dentro de cierto plazo.
Si el agente ya envió el mensaje de envío y luego la orden se cancela,
el agente debe:
  1. Detener cualquier follow-up pendiente
  2. Notificar al vendedor
  3. Marcar la orden como cancelada en DB

Este handler se activa:
  a) Vía webhook (topic: orders_v2, nuevo status=cancelled)
  b) Vía polling de revisión periódica de órdenes activas
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.meli_client import MeliAPIError, MeliClient
from app.models import AgentEvent, ProcessedOrder
from app.notifications import Notifier

logger = logging.getLogger("divinittys.cancellation")

# Estados de ML que indican que la orden ya no está activa
CANCELLED_STATUSES = {"cancelled", "refunded", "chargedback", "invalid"}


class CancellationHandler:
    """
    Detecta y gestiona cancelaciones de órdenes.
    Se ejecuta tanto desde webhooks como desde polling periódico.
    """

    def __init__(self, client: MeliClient, db: AsyncSession):
        self.client = client
        self.db = db
        self.notifier = Notifier()

    async def handle_order_status_change(self, order_id: str) -> dict:
        """
        Verifica si una orden fue cancelada y actúa en consecuencia.
        Llamar cuando llega un webhook de orders_v2 (cualquier cambio de estado).
        """
        try:
            order = await self.client.get_order(order_id)
        except MeliAPIError as e:
            logger.error(f"❌ No se pudo obtener orden {order_id}: {e}")
            return {"status": "error", "detail": str(e)}

        order_status = order.get("status", "")

        if order_status not in CANCELLED_STATUSES:
            return {"status": "not_cancelled", "order_status": order_status}

        # La orden fue cancelada/reembolsada
        logger.info(f"🚫 Orden {order_id} cancelada (status={order_status})")
        await self._process_cancellation(order_id, order_status, order)
        return {"status": "cancelled_handled", "order_status": order_status}

    async def scan_active_orders_for_cancellations(self) -> dict:
        """
        Escanea órdenes activas en la DB y verifica si alguna fue cancelada en ML.
        Job periódico — se ejecuta cada 30 minutos via scheduler.
        """
        # Buscar órdenes donde el agente interactuó pero podrían haberse cancelado
        result = await self.db.execute(
            select(ProcessedOrder).where(
                ProcessedOrder.message_sent,
                ProcessedOrder.status.not_in(["cancelled", "replied"]),
            )
        )
        active_orders = result.scalars().all()

        cancelled_count = 0
        for order in active_orders:
            try:
                ml_order = await self.client.get_order(order.order_id)
                ml_status = ml_order.get("status", "")
                if ml_status in CANCELLED_STATUSES:
                    await self._process_cancellation(order.order_id, ml_status, ml_order)
                    cancelled_count += 1
            except MeliAPIError:
                pass  # Si no se puede obtener, continuar con la siguiente

        result_summary = {
            "scanned": len(active_orders),
            "cancelled_detected": cancelled_count,
        }
        logger.info(f"🔍 Scan cancelaciones: {result_summary}")
        return result_summary

    async def _process_cancellation(
        self, order_id: str, ml_status: str, order_data: dict
    ) -> None:
        """Actualiza DB y notifica al vendedor sobre la cancelación."""
        # Actualizar registro en DB
        result = await self.db.execute(
            select(ProcessedOrder).where(ProcessedOrder.order_id == order_id)
        )
        order_row = result.scalar_one_or_none()

        if order_row:
            order_row.status = "cancelled"
            order_row.skip_reason = f"ml_status={ml_status}"
            await self.db.commit()

        # Registrar evento
        event = AgentEvent(
            event_type="order_cancelled",
            severity="warning",
            order_id=order_id,
            detail=f"ML status: {ml_status}",
        )
        self.db.add(event)
        await self.db.commit()

        # Notificar al vendedor
        buyer = order_data.get("buyer", {})
        buyer_name = buyer.get("nickname") or buyer.get("first_name") or "Desconocido"

        text = (
            f"🚫 *Orden cancelada*\n"
            f"Orden: `{order_id}`\n"
            f"Comprador: {buyer_name}\n"
            f"Estado ML: `{ml_status}`\n\n"
            f"El agente detuvo el follow-up automáticamente."
        )
        await self.notifier.send(text)
        logger.info(f"✅ Cancelación procesada para orden {order_id}")
