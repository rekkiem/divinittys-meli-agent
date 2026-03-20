"""
notifications.py — Sistema de notificaciones internas al vendedor.

Canal: Telegram Bot (simple, sin dependencias extra).
El vendedor recibe alertas en su Telegram personal con:
  - ✅ "Se envió mensaje a comprador X"
  - 💬 "Comprador X respondió con sus datos"
  - ❌ "Error en API de ML"
  - 🔑 "Token OAuth renovado"
  - 📊 "Resumen del ciclo de polling"
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger("divinittys.notifier")

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class Notifier:
    """
    Envía notificaciones al vendedor vía Telegram Bot.
    Si TELEGRAM_BOT_TOKEN no está configurado, loguea en consola solamente.
    """

    def __init__(self):
        self._enabled = bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)
        if not self._enabled:
            logger.warning(
                "⚠️ Notificaciones Telegram desactivadas. "
                "Configura TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env"
            )

    async def send(self, text: str) -> bool:
        """
        Envía un mensaje de texto al chat del vendedor.
        Usa Markdown para formato enriquecido.
        """
        if not self._enabled:
            logger.info(f"[NOTIF-LOCAL] {text}")
            return True

        url = TELEGRAM_API_URL.format(token=settings.TELEGRAM_BOT_TOKEN)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json={
                    "chat_id": settings.TELEGRAM_CHAT_ID,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                })
                if resp.status_code != 200:
                    logger.error(f"❌ Telegram error: {resp.status_code} {resp.text}")
                    return False
                return True
        except Exception as e:
            logger.error(f"❌ No se pudo enviar notificación Telegram: {e}")
            return False

    async def send_alert(self, message: str) -> bool:
        """Alias genérico para alertas críticas."""
        return await self.send(message)

    async def send_message_sent_alert(
        self, order_id: str, buyer_name: str | None
    ) -> None:
        """Notifica al vendedor que se envió el mensaje al comprador."""
        buyer_display = f"*{buyer_name}*" if buyer_name else "el comprador"
        text = (
            f"✅ *Mensaje enviado*\n"
            f"Orden: `{order_id}`\n"
            f"Se solicitaron datos de envío a {buyer_display}.\n"
            f"Esperando respuesta del cliente... 💬"
        )
        await self.send(text)

    async def send_buyer_reply_alert(
        self, order_id: str, buyer_name: str | None
    ) -> None:
        """Notifica al vendedor que el comprador respondió con sus datos."""
        buyer_display = f"*{buyer_name}*" if buyer_name else "El comprador"
        text = (
            f"🎉 *¡Respuesta recibida!*\n"
            f"Orden: `{order_id}`\n"
            f"{buyer_display} envió sus datos de envío.\n\n"
            f"👉 Revisa Mercado Libre y procesa el despacho."
        )
        await self.send(text)

    async def send_polling_summary(self, summary: dict) -> None:
        """Envía resumen del ciclo de polling (opcional, para logs diarios)."""
        text = (
            f"📊 *Resumen de ciclo — Divinittys*\n"
            f"Órdenes encontradas: {summary.get('orders_found', 0)}\n"
            f"Mensajes enviados: {summary.get('processed', 0)}\n"
            f"Ignoradas: {summary.get('skipped', 0)}\n"
            f"Errores: {summary.get('errors', 0)}"
        )
        await self.send(text)

    async def send_token_refreshed_alert(self) -> None:
        """Notifica renovación exitosa del token OAuth."""
        await self.send("🔑 Token OAuth de Mercado Libre renovado exitosamente.")

    async def send_token_error_alert(self, detail: str) -> None:
        """Alerta crítica: el token no se pudo renovar."""
        text = (
            f"🚨 *ERROR CRÍTICO — Token OAuth*\n"
            f"El agente no pudo renovar el token de ML.\n"
            f"Detalle: `{detail}`\n\n"
            f"⚠️ Acción requerida: visita `/auth/login` en el servidor para re-autorizar."
        )
        await self.send(text)

    async def send_followup_sent_alert(self, order_id: str) -> None:
        """Notifica que se envió el mensaje de seguimiento (24h)."""
        text = (
            f"🔔 *Follow-up enviado*\n"
            f"Orden: `{order_id}`\n"
            f"El comprador lleva 24h sin responder. Se envió recordatorio. ⏰"
        )
        await self.send(text)

    async def send_escalation_alert(self, order_id: str, hours: int) -> None:
        """Alerta urgente: comprador no responde en 48h."""
        text = (
            f"🚨 *Atención requerida — {hours}h sin respuesta*\n"
            f"Orden: `{order_id}`\n"
            f"El comprador no ha respondido en {hours} horas.\n\n"
            f"👉 Te recomendamos revisar la orden directamente en Mercado Libre "
            f"o considerar cancelarla si corresponde."
        )
        await self.send(text)
