"""
agent.py — Núcleo del Agente de Post-Venta.

Lógica principal:
  1. Recibe un order_id (desde webhook o polling)
  2. Verifica si la orden es de tipo 'custom' shipping + estado 'paid'
  3. Chequea si ya fue procesada (idempotencia)
  4. Verifica si el comprador ya envió sus datos (leyendo mensajes)
  5. Si no hay respuesta del comprador → envía mensaje de solicitud de datos
  6. Registra todo en DB y notifica al vendedor por Telegram
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.meli_client import MeliAPIError, MeliAuthError, MeliClient
from app.message_templates import (
    build_confirmation_message,
    build_shipping_request_message,
)
from app.models import AgentEvent, ProcessedOrder, SentMessage
from app.notifications import Notifier

logger = logging.getLogger("divinittys.agent")


class PostSaleAgent:
    """
    Agente autónomo de post-venta para Divinittys.
    Gestiona el ciclo completo de una venta con envío acordado.
    """

    def __init__(self, client: MeliClient, db: AsyncSession):
        self.client = client
        self.db = db
        self.notifier = Notifier()

    # ── Entry Point ───────────────────────────────────────────────────────────

    async def process_order(self, order_id: str, force: bool = False) -> dict:
        """
        Procesa una orden completa.
        
        Args:
            order_id: ID de la orden de ML
            force: Si True, re-procesa aunque ya esté registrada (para testing)
        
        Returns:
            dict con resultado del procesamiento
        """
        logger.info(f"🔍 Procesando orden: {order_id}")

        # 1. Idempotencia: ¿Ya fue procesada?
        if not force:
            existing = await self._get_processed_order(order_id)
            if existing and existing.message_sent:
                logger.info(f"⏭️  Orden {order_id} ya procesada. Skip.")
                return {"status": "skipped", "reason": "already_processed"}

        # 2. Obtener datos de la orden desde ML
        try:
            order = await self.client.get_order(order_id)
        except MeliAuthError as e:
            logger.error(f"❌ Auth error: {e}")
            await self.notifier.send_alert(
                f"🚨 *Error de autenticación*\nOrden: {order_id}\nDetalle: {e}\n"
                f"Acción requerida: visita /auth/login"
            )
            return {"status": "error", "reason": "auth_error"}
        except MeliAPIError as e:
            logger.error(f"❌ API error obteniendo orden {order_id}: {e}")
            await self._log_event("api_error", severity="error", order_id=order_id,
                                  detail=str(e))
            return {"status": "error", "reason": str(e)}

        # 3. Verificar condiciones de activación
        check = self._should_process(order)
        if not check["proceed"]:
            await self._upsert_processed_order(order, status="skipped",
                                               skip_reason=check["reason"])
            logger.info(f"⏭️  Orden {order_id} ignorada: {check['reason']}")
            return {"status": "skipped", "reason": check["reason"]}

        # 4. Obtener pack_id para mensajería
        pack_id = str(order.get("pack_id") or order_id)
        seller_id = str(order["seller"]["id"])
        buyer = order.get("buyer", {})
        buyer_name = buyer.get("nickname") or buyer.get("first_name")

        # 5. Verificar si el comprador ya interactuó
        buyer_already_replied = await self._buyer_has_replied(pack_id, seller_id)

        if buyer_already_replied:
            logger.info(f"✅ Comprador ya respondió en orden {order_id}. Notificando vendedor.")
            await self._upsert_processed_order(order, status="replied",
                                               message_sent=True, buyer_replied=True)
            await self.notifier.send_buyer_reply_alert(order_id, buyer_name)
            await self._log_event("buyer_replied", order_id=order_id,
                                  detail=f"Buyer: {buyer_name}")
            # Opcional: enviar mensaje de confirmación
            # await self._send_confirmation(pack_id, seller_id, buyer_name)
            return {"status": "buyer_replied"}

        # 6. Enviar mensaje de solicitud de datos
        message_text = build_shipping_request_message(order_id, buyer_name)
        try:
            result = await self.client.send_message(pack_id, seller_id, message_text)
            meli_message_id = result.get("id")
            logger.info(f"📤 Mensaje enviado a orden {order_id}. ML message_id={meli_message_id}")

            # Guardar en DB
            await self._save_sent_message(order_id, pack_id, meli_message_id, message_text)
            await self._upsert_processed_order(order, status="message_sent", message_sent=True)
            await self._log_event("message_sent", order_id=order_id,
                                  detail=f"Buyer: {buyer_name} | Pack: {pack_id}")

            # Notificar al vendedor
            await self.notifier.send_message_sent_alert(order_id, buyer_name)

            return {
                "status": "message_sent",
                "order_id": order_id,
                "buyer": buyer_name,
                "meli_message_id": meli_message_id,
            }

        except MeliAPIError as e:
            logger.error(f"❌ No se pudo enviar mensaje a orden {order_id}: {e}")
            await self._upsert_processed_order(order, status="error",
                                               skip_reason=str(e))
            await self._log_event("api_error", severity="error", order_id=order_id,
                                  detail=f"Error enviando mensaje: {e}")
            await self.notifier.send_alert(
                f"⚠️ *Error enviando mensaje*\nOrden: `{order_id}`\nDetalle: `{e}`"
            )
            return {"status": "error", "reason": str(e)}

    # ── Lógica de Decisión ────────────────────────────────────────────────────

    def _should_process(self, order: dict) -> dict:
        """
        Determina si una orden debe activar el agente.
        Retorna dict con 'proceed' (bool) y 'reason' (str).
        """
        order_id = order.get("id")

        # Solo órdenes pagadas
        if order.get("status") != "paid":
            return {"proceed": False, "reason": f"status={order.get('status')}"}

        # Solo envío 'custom' (Acordado con el vendedor)
        shipping = order.get("shipping", {})
        shipping_mode = shipping.get("mode") or shipping.get("shipping_mode", "")
        if shipping_mode != "custom":
            return {"proceed": False, "reason": f"shipping_mode={shipping_mode}"}

        # Orden válida
        return {"proceed": True, "reason": "ok"}

    async def _buyer_has_replied(self, pack_id: str, seller_id: str) -> bool:
        """
        Verifica si el comprador ya envió un mensaje en el hilo de la orden.
        Considera que el agente ya envió el primer mensaje.
        """
        try:
            messages = await self.client.get_order_messages(pack_id, seller_id)
            for msg in messages:
                from_role = (
                    msg.get("from", {}).get("role", "")
                    or msg.get("message_role", "")
                ).upper()
                if from_role == "BUYER":
                    return True
            return False
        except MeliAPIError as e:
            # Si hay error leyendo mensajes, asumir que no respondió (falso negativo seguro)
            logger.warning(f"⚠️ No se pudo leer mensajes de pack {pack_id}: {e}")
            return False

    # ── Polling ───────────────────────────────────────────────────────────────

    async def run_polling_cycle(self, seller_id: str, lookback_hours: int = 2) -> dict:
        """
        Ciclo de polling: busca órdenes recientes con envío 'custom' y las procesa.
        Llamado por el scheduler cada N minutos como fallback al webhook.
        """
        from datetime import timedelta

        date_from = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
        logger.info(f"🔄 Polling cycle. Buscando órdenes desde {date_from}...")

        try:
            orders = await self.client.search_custom_shipping_orders(seller_id, date_from)
        except (MeliAuthError, MeliAPIError) as e:
            logger.error(f"❌ Error en polling: {e}")
            await self._log_event("api_error", severity="error", detail=f"Polling error: {e}")
            return {"status": "error", "detail": str(e)}

        results = []
        for order in orders:
            order_id = str(order["id"])
            result = await self.process_order(order_id)
            results.append({"order_id": order_id, **result})

        summary = {
            "status": "ok",
            "orders_found": len(orders),
            "processed": len([r for r in results if r.get("status") == "message_sent"]),
            "skipped": len([r for r in results if r.get("status") == "skipped"]),
            "errors": len([r for r in results if r.get("status") == "error"]),
        }
        await self._log_event("polling_run", detail=str(summary))
        logger.info(f"✅ Polling completado: {summary}")
        return summary

    # ── DB Helpers ────────────────────────────────────────────────────────────

    async def _get_processed_order(self, order_id: str) -> ProcessedOrder | None:
        result = await self.db.execute(
            select(ProcessedOrder).where(ProcessedOrder.order_id == order_id)
        )
        return result.scalar_one_or_none()

    async def _upsert_processed_order(
        self,
        order: dict,
        status: str,
        message_sent: bool = False,
        buyer_replied: bool = False,
        skip_reason: str | None = None,
    ) -> None:
        order_id = str(order["id"])
        existing = await self._get_processed_order(order_id)

        if existing:
            existing.status = status
            if message_sent:
                existing.message_sent = message_sent
            if buyer_replied:
                existing.buyer_replied = buyer_replied
            if skip_reason:
                existing.skip_reason = skip_reason
        else:
            shipping = order.get("shipping", {})
            new_order = ProcessedOrder(
                order_id=order_id,
                pack_id=str(order.get("pack_id") or order_id),
                buyer_id=str(order.get("buyer", {}).get("id", "")),
                status=status,
                shipping_mode=shipping.get("mode") or shipping.get("shipping_mode"),
                message_sent=message_sent,
                buyer_replied=buyer_replied,
                skip_reason=skip_reason,
            )
            self.db.add(new_order)

        await self.db.commit()

    async def _save_sent_message(
        self,
        order_id: str,
        pack_id: str,
        meli_message_id: str | None,
        message_text: str,
    ) -> None:
        msg = SentMessage(
            order_id=order_id,
            pack_id=pack_id,
            meli_message_id=meli_message_id,
            message_text=message_text,
        )
        self.db.add(msg)
        await self.db.commit()

    async def _log_event(
        self,
        event_type: str,
        severity: str = "info",
        order_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        event = AgentEvent(
            event_type=event_type,
            severity=severity,
            order_id=order_id,
            detail=detail,
        )
        self.db.add(event)
        await self.db.commit()
