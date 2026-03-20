"""
message_templates.py — Plantillas de mensajes para Divinittys.

Tono de marca: Profesional, cercano y eficiente.
Rubro: Belleza profesional.

REGLA DE ORO (anti-ban ML):
  ❌ NO incluir URLs externas (Instagram, WhatsApp links, web)
  ❌ NO usar acortadores de URL
  ✅ Datos bancarios en TEXTO PLANO
  ✅ Lenguaje natural, NO spam
"""

from app.config import settings


def _format_clp(amount: int) -> str:
    """Formatea un entero como precio en pesos chilenos."""
    return f"${amount:,.0f}".replace(",", ".")


def build_shipping_request_message(order_id: str, buyer_name: str | None = None) -> str:
    """
    Mensaje principal: solicita datos de envío + instrucciones de pago.

    Args:
        order_id: ID de la orden (para referencia del comprador)
        buyer_name: Nombre del comprador (opcional, para personalización)

    Returns:
        str: Texto del mensaje listo para enviar por la API de ML
    """
    greeting = f"¡Hola{f', {buyer_name.split()[0]}' if buyer_name else ''}!"
    shipping_cost_str = _format_clp(settings.SHIPPING_COST)

    message = f"""{greeting} 💖 Soy del equipo de DIVINITTYS, muchas gracias por tu compra.

Para preparar tu despacho con BlueExpress lo antes posible, necesitamos coordinar dos pasos rápidos:

━━━━━━━━━━━━━━━━━━━━━━
📦 PASO 1 — TUS DATOS DE ENVÍO
━━━━━━━━━━━━━━━━━━━━━━
Por favor, responde este mensaje con tu información:

  • Nombre completo:
  • RUT:
  • Teléfono de contacto:
  • Dirección completa (calle, número, depto/casa, comuna, región):

━━━━━━━━━━━━━━━━━━━━━━
💳 PASO 2 — COSTO DE ENVÍO
━━━━━━━━━━━━━━━━━━━━━━
El costo de despacho vía BlueExpress es de {shipping_cost_str}.

Puedes transferirlo a:

  Banco:        {settings.BANK_NAME}
  Tipo cuenta:  {settings.BANK_ACCOUNT_TYPE}
  N° cuenta:    {settings.BANK_ACCOUNT_NUMBER}
  RUT titular:  {settings.BANK_RUT}
  Nombre:       {settings.BANK_OWNER}
  Email:        {settings.BANK_EMAIL}

Una vez confirmados los datos y la transferencia, procesamos tu pedido de inmediato. ¡El plazo de entrega es de 1 a 3 días hábiles! 🚀

¿Tienes alguna consulta sobre tus productos? Estamos para ayudarte. ✨

— Equipo DIVINITTYS"""

    return message


def build_follow_up_message(buyer_name: str | None = None) -> str:
    """
    Mensaje de seguimiento si el comprador no ha respondido después de 24h.
    """
    greeting = f"¡Hola{f', {buyer_name.split()[0]}' if buyer_name else ''}!"
    shipping_cost_str = _format_clp(settings.SHIPPING_COST)

    message = f"""{greeting} 🌸 Te escribimos nuevamente desde DIVINITTYS.

Notamos que aún estamos esperando tus datos para coordinar el envío de tu compra. ¡No queremos que se demore más de lo necesario!

Recuerda que solo necesitamos:
  ✅ Nombre, RUT, teléfono y dirección
  ✅ Transferencia de {shipping_cost_str} a la cuenta indicada en nuestro mensaje anterior

Una vez que tengamos esa información, despachamos el mismo día o al día hábil siguiente. 📦

Si tuviste algún inconveniente o tienes una consulta, responde este mensaje y te ayudamos de inmediato.

¡Gracias por elegir DIVINITTYS! 💕"""

    return message


def build_confirmation_message(buyer_name: str | None = None) -> str:
    """
    Mensaje de confirmación cuando el comprador ya respondió con sus datos.
    El agente puede enviarlo automáticamente al detectar una respuesta.
    """
    greeting = f"¡Hola{f', {buyer_name.split()[0]}' if buyer_name else ''}!"

    message = f"""{greeting} ✨ ¡Recibimos tu información correctamente!

Estamos verificando el pago del envío y preparando tu pedido ahora mismo. Te contactaremos con el código de seguimiento de BlueExpress en cuanto despachemos.

¡Gracias por tu paciencia y por confiar en DIVINITTYS! 🌸💖

— Equipo DIVINITTYS"""

    return message
