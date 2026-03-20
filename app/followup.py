"""
followup.py — Módulo de seguimiento automático a las 24h.

Flujo:
  1. Cada hora (via scheduler) busca órdenes donde:
     - El agente ya envió el mensaje inicial
     - El comprador NO ha respondido
     - Han pasado >= FOLLOWUP_HOURS horas desde el mensaje
     - Aún no se envió el follow-up
  2. Envía un segundo mensaje cordial al comprador
  3. Si pasan ESCALATION_HOURS sin respuesta → alerta urgente al vendedor
"""

import inspect
import logging
from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.meli_client import MeliAPIError, MeliClient
from app.message_templates import build_follow_up_message
from app.models import AgentEvent, OAuthToken, ProcessedOrder, SentMessage
from app.notifications import Notifier

logger = logging.getLogger("divinittys.followup")

# ── Tiempos configurables ─────────────────────────────────────────────────────
FOLLOWUP_HOURS = 24       # Enviar seguimiento si no responde en 24h
ESCALATION_HOURS = 48     # Alerta urgente al vendedor si no responde en 48h


class FollowUpAgent:
    """
    Agente de seguimiento post-mensaje.
    Detecta órdenes "silenciosas" y gestiona el segundo contacto.
    """

    def __init__(self, client: MeliClient, db: AsyncSession):
        self.client = client
        self.db = db
        self.notifier = Notifier()

    async def run(self) -> dict:
        """
        Ciclo principal del follow-up.
        Retorna resumen de acciones realizadas.
        """
        now = datetime.now(timezone.utc)

        logger.info(f"🔔 Iniciando ciclo de follow-up. Umbral: {FOLLOWUP_HOURS}h")

        # Obtener seller_id
        result = await self.db.execute(select(OAuthToken).where(OAuthToken.id == 1))
        token_row = result.scalar_one_or_none()
        if not token_row:
            logger.warning("⚠️ No hay token OAuth. Saltando follow-up.")
            return {"status": "no_token"}

        seller_id = token_row.seller_id
        stats = {"followups_sent": 0, "escalations": 0, "errors": 0, "checked": 0}

        # Buscar órdenes candidatas: mensaje enviado, sin respuesta del buyer
        candidates_query = select(ProcessedOrder).where(
            and_(
                ProcessedOrder.message_sent,
                ~ProcessedOrder.buyer_replied,
                ProcessedOrder.status == "message_sent",
            )
        )
        result = await self.db.execute(candidates_query)
        candidates = result.scalars().all()
        stats["checked"] = len(candidates)

        for order in candidates:
            # Obtener tiempo desde que se envió el primer mensaje
            last_message = await self._get_last_sent_message(order.order_id)
            if not last_message:
                continue

            sent_at = last_message.sent_at
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=timezone.utc)

            hours_elapsed = (now - sent_at).total_seconds() / 3600

            # Verificar si el buyer respondió en ML (puede haber lag en nuestra DB)
            pack_id = order.pack_id or order.order_id
            buyer_replied = await self._check_buyer_replied(pack_id, seller_id)

            if buyer_replied:
                # Actualizar DB — el buyer respondió pero no lo habíamos detectado
                order.buyer_replied = True
                order.status = "replied"
                await self.db.commit()
                logger.info(f"✅ Buyer respondió (detectado tardío) orden {order.order_id}")
                await self.notifier.send_buyer_reply_alert(order.order_id, None)
                continue

            # Alerta de escalación (48h sin respuesta)
            if hours_elapsed >= ESCALATION_HOURS and not order.skip_reason:
                await self._send_escalation_alert(order, hours_elapsed)
                # Marcar para no volver a escalar
                order.skip_reason = f"escalated_at_{int(hours_elapsed)}h"
                await self.db.commit()
                stats["escalations"] += 1
                continue

            # Follow-up (24h - 47h sin respuesta)
            if hours_elapsed >= FOLLOWUP_HOURS:
                followup_already_sent = await self._followup_already_sent(order.order_id)
                if followup_already_sent:
                    logger.debug(f"⏭️  Follow-up ya enviado para orden {order.order_id}")
                    continue

                success = await self._send_followup(order, seller_id)
                if success:
                    stats["followups_sent"] += 1
                else:
                    stats["errors"] += 1

        logger.info(f"🔔 Follow-up completado: {stats}")
        await self._log_event("followup_run", detail=str(stats))
        return stats

    # ── Acciones ──────────────────────────────────────────────────────────────

    async def _send_followup(self, order: ProcessedOrder, seller_id: str) -> bool:
        """Envía el mensaje de seguimiento al comprador."""
        pack_id = order.pack_id or order.order_id
        message_text = build_follow_up_message()

        try:
            result = await self.client.send_message(pack_id, seller_id, message_text)
            meli_message_id = result.get("id")

            # Guardar en DB como mensaje adicional
            followup_msg = SentMessage(
                order_id=order.order_id,
                pack_id=pack_id,
                meli_message_id=meli_message_id,
                message_text=message_text,
                delivery_status="sent_followup",
            )
            self.db.add(followup_msg)
            await self.db.commit()

            logger.info(f"📤 Follow-up enviado a orden {order.order_id}")
            await self.notifier.send_followup_sent_alert(order.order_id)
            await self._log_event("followup_sent", order_id=order.order_id,
                                   detail=f"Pack: {pack_id}")
            return True

        except MeliAPIError as e:
            logger.error(f"❌ Error enviando follow-up a orden {order.order_id}: {e}")
            await self._log_event("api_error", severity="error",
                                   order_id=order.order_id,
                                   detail=f"Follow-up error: {e}")
            return False

    async def _send_escalation_alert(self, order: ProcessedOrder, hours: float) -> None:
        """Alerta al vendedor: el comprador lleva 48h sin responder."""
        await self.notifier.send_escalation_alert(order.order_id, int(hours))
        await self._log_event(
            "escalation_alert",
            severity="warning",
            order_id=order.order_id,
            detail=f"Sin respuesta por {int(hours)}h",
        )
        logger.warning(f"🚨 Escalación: orden {order.order_id} lleva {int(hours)}h sin respuesta")

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _check_buyer_replied(self, pack_id: str, seller_id: str) -> bool:
        """Consulta la mensajería de ML para ver si el buyer respondió."""
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
        except MeliAPIError:
            return False

    async def _get_last_sent_message(self, order_id: str) -> SentMessage | None:
        """Obtiene el mensaje más reciente enviado por el agente a esta orden."""
        result = await self.db.execute(
            select(SentMessage)
            .where(SentMessage.order_id == order_id)
            .order_by(SentMessage.sent_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if inspect.isawaitable(row):
            row = await row
        return row

    async def _followup_already_sent(self, order_id: str) -> bool:
        """Verifica si ya se envió un follow-up para esta orden."""
        result = await self.db.execute(
            select(SentMessage).where(
                and_(
                    SentMessage.order_id == order_id,
                    SentMessage.delivery_status == "sent_followup",
                )
            )
        )
        row = result.scalar_one_or_none()
        if inspect.isawaitable(row):
            row = await row
        return row is not None

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


# ── Job para el scheduler ─────────────────────────────────────────────────────

async def followup_job():
    """Función registrada en APScheduler. Se ejecuta cada hora."""
    async with AsyncSessionLocal() as db:
        try:
            from app.meli_client import MeliClient
            client = MeliClient(db=db)
            agent = FollowUpAgent(client=client, db=db)
            await agent.run()
        except Exception as e:
            logger.error(f"❌ Error en followup_job: {e}", exc_info=True)
