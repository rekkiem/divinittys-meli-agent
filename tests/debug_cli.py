"""
tests/debug_cli.py — CLI interactiva de debug para el agente Divinittys.

Permite inspeccionar y manipular el estado del agente sin interfaz web.
Útil para debug rápido y validación de flujos.

Ejecutar:
  python tests/debug_cli.py

Comandos disponibles:
  status          → Estado general: token, DB, scheduler
  orders          → Listar todas las órdenes procesadas
  process <id>    → Forzar procesamiento de una orden específica
  messages <id>   → Ver mensajes enviados a una orden
  events [N]      → Ver últimos N eventos del log
  webhook <id>    → Simular webhook de ML para una orden
  token           → Ver estado del token OAuth
  reset <id>      → Eliminar registro de una orden (para re-testear)
  clear           → Limpiar toda la DB de debug
  quit            → Salir
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Setup de paths
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("ENV_FILE", ".env.debug")

# Cargar .env.debug si existe
from dotenv import load_dotenv
env_file = ".env.debug" if Path(".env.debug").exists() else ".env"
load_dotenv(env_file, override=True)
print(f"📁 Usando configuración: {env_file}")


async def main():
    from app.database import AsyncSessionLocal, init_db
    from app.meli_client import MeliClient
    from app.agent import PostSaleAgent
    from app.models import OAuthToken, ProcessedOrder, SentMessage, AgentEvent
    from sqlalchemy import select, delete, text

    # Inicializar DB
    await init_db()

    print("""
╔══════════════════════════════════════════════════════╗
║     Divinittys Agent — Debug CLI                     ║
║     Escribe 'help' para ver comandos                 ║
╚══════════════════════════════════════════════════════╝
""")

    # Inyectar token de debug automáticamente
    await _inject_debug_token()

    while True:
        try:
            raw = input("\n🔧 debug> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSaliendo...")
            break

        if not raw:
            continue

        parts = raw.split()
        cmd = parts[0].lower()
        args = parts[1:]

        try:
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                _print_help()
            elif cmd == "status":
                await cmd_status()
            elif cmd == "orders":
                await cmd_orders()
            elif cmd == "process":
                order_id = args[0] if args else input("  Order ID: ").strip()
                await cmd_process(order_id)
            elif cmd == "messages":
                order_id = args[0] if args else input("  Order ID: ").strip()
                await cmd_messages(order_id)
            elif cmd == "events":
                limit = int(args[0]) if args else 10
                await cmd_events(limit)
            elif cmd == "webhook":
                order_id = args[0] if args else input("  Order ID: ").strip()
                await cmd_simulate_webhook(order_id)
            elif cmd == "token":
                await cmd_token_status()
            elif cmd == "reset":
                order_id = args[0] if args else input("  Order ID: ").strip()
                await cmd_reset_order(order_id)
            elif cmd == "clear":
                confirm = input("  ⚠️  ¿Borrar toda la DB de debug? (s/N): ").strip()
                if confirm.lower() == "s":
                    await cmd_clear_db()
            elif cmd == "message-preview":
                order_id = args[0] if args else "1000000001"
                _preview_message(order_id)
            else:
                print(f"  ❓ Comando desconocido: '{cmd}'. Escribe 'help'.")
        except Exception as e:
            print(f"  ❌ Error: {e}")
            import traceback
            traceback.print_exc()


async def _inject_debug_token():
    """Inyecta un token de debug en la DB para no necesitar OAuth real."""
    from app.database import AsyncSessionLocal
    from app.models import OAuthToken
    from datetime import timedelta
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(OAuthToken).where(OAuthToken.id == 1))
        existing = result.scalar_one_or_none()
        if not existing:
            token = OAuthToken(
                id=1,
                seller_id="111222333",
                access_token="TEST_ACCESS_TOKEN_DIVINITTYS_MOCK",
                refresh_token="TEST_REFRESH_TOKEN_DIVINITTYS_MOCK",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
                scope="offline_access read write messages",
            )
            db.add(token)
            await db.commit()
            print("  🔑 Token de debug inyectado automáticamente")


async def cmd_status():
    from app.database import AsyncSessionLocal
    from app.models import OAuthToken, ProcessedOrder, SentMessage, AgentEvent
    from sqlalchemy import select, func

    async with AsyncSessionLocal() as db:
        token = (await db.execute(select(OAuthToken).where(OAuthToken.id == 1))).scalar_one_or_none()
        total_orders = await db.scalar(select(func.count()).select_from(ProcessedOrder))
        total_msgs = await db.scalar(select(func.count()).select_from(SentMessage))
        total_events = await db.scalar(select(func.count()).select_from(AgentEvent))

    now = datetime.now(timezone.utc)
    if token:
        secs = (token.expires_at - now).total_seconds()
        tok_status = f"✅ Válido ({int(secs/60)} min restantes)" if secs > 0 else "❌ Expirado"
    else:
        tok_status = "❌ No configurado"

    print(f"""
  Estado del Agente Divinittys
  ─────────────────────────────────────────
  Token OAuth:       {tok_status}
  Seller ID:         {token.seller_id if token else '—'}
  Órdenes en DB:     {total_orders}
  Mensajes enviados: {total_msgs}
  Eventos log:       {total_events}
  API base:          {os.getenv('MELI_API_BASE', 'https://api.mercadolibre.com')}
  DB:                {os.getenv('DATABASE_URL', '—')}""")


async def cmd_orders():
    from app.database import AsyncSessionLocal
    from app.models import ProcessedOrder
    from sqlalchemy import select
    from sqlalchemy import desc

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ProcessedOrder).order_by(desc(ProcessedOrder.processed_at)).limit(20)
        )
        orders = result.scalars().all()

    if not orders:
        print("  (sin órdenes procesadas)")
        return

    STATUS_ICON = {
        "message_sent": "📤",
        "replied": "💬",
        "skipped": "⏭️ ",
        "error": "❌",
        "cancelled": "🚫",
        "pending": "⏳",
    }

    print(f"\n  {'Orden':<14} {'Estado':<16} {'Msg':<5} {'Reply':<7} {'Procesada'}")
    print("  " + "─" * 65)
    for o in orders:
        icon = STATUS_ICON.get(o.status, "❓")
        sent = "✅" if o.message_sent else "—"
        replied = "💬" if o.buyer_replied else "—"
        ts = o.processed_at.strftime("%d/%m %H:%M") if o.processed_at else "—"
        skip = f"  ({o.skip_reason})" if o.skip_reason and o.status == "skipped" else ""
        print(f"  {o.order_id:<14} {icon} {o.status:<13} {sent:<5} {replied:<7} {ts}{skip}")


async def cmd_process(order_id: str):
    from app.database import AsyncSessionLocal
    from app.meli_client import MeliClient
    from app.agent import PostSaleAgent

    print(f"\n  ⚙️  Procesando orden {order_id} (force=True)...")
    async with AsyncSessionLocal() as db:
        client = MeliClient(db=db)
        agent = PostSaleAgent(client=client, db=db)
        result = await agent.process_order(order_id, force=True)

    icon = {"message_sent": "✅", "skipped": "⏭️", "error": "❌", "buyer_replied": "💬"}.get(result.get("status"), "ℹ️")
    print(f"  {icon} Resultado: {result}")


async def cmd_messages(order_id: str):
    from app.database import AsyncSessionLocal
    from app.models import SentMessage
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SentMessage).where(SentMessage.order_id == order_id)
        )
        msgs = result.scalars().all()

    if not msgs:
        print(f"  (sin mensajes registrados para orden {order_id})")
        return

    for i, m in enumerate(msgs, 1):
        ts = m.sent_at.strftime("%d/%m/%Y %H:%M:%S") if m.sent_at else "—"
        print(f"\n  ── Mensaje #{i} ({ts}) | Status: {m.delivery_status} ──")
        print(f"  ML ID: {m.meli_message_id or '—'}")
        print()
        # Mostrar el mensaje con indentación
        for line in m.message_text.split("\n"):
            print(f"  │ {line}")


async def cmd_events(limit: int = 10):
    from app.database import AsyncSessionLocal
    from app.models import AgentEvent
    from sqlalchemy import select, desc

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AgentEvent).order_by(desc(AgentEvent.created_at)).limit(limit)
        )
        events = result.scalars().all()

    SEV_ICON = {"info": "ℹ️ ", "warning": "⚠️ ", "error": "❌"}
    print(f"\n  Últimos {limit} eventos:")
    print("  " + "─" * 70)
    for e in reversed(events):
        ts = e.created_at.strftime("%H:%M:%S") if e.created_at else "—"
        icon = SEV_ICON.get(e.severity, "·")
        order_ref = f" [{e.order_id}]" if e.order_id else ""
        detail = f" — {e.detail[:50]}" if e.detail else ""
        print(f"  {ts} {icon} {e.event_type}{order_ref}{detail}")


async def cmd_simulate_webhook(order_id: str):
    """Simula el payload exacto que envía ML por webhook."""
    import httpx

    payload = {
        "topic": "orders_v2",
        "resource": f"/orders/{order_id}",
        "user_id": 111222333,
        "application_id": 999888777,
        "_sent": "2025-01-01T00:00:00.000-03:00",
    }

    print(f"\n  📡 Enviando webhook simulado para orden {order_id}...")
    print(f"  Payload: {payload}")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "http://localhost:8000/webhooks/meli",
                json=payload,
            )
        print(f"  Respuesta HTTP {resp.status_code}: {resp.json()}")
    except Exception as e:
        print(f"  ❌ No se pudo conectar al agente en localhost:8000")
        print(f"     ¿Está corriendo? Ejecuta: uvicorn app.main:app --reload")
        print(f"     Error: {e}")


async def cmd_token_status():
    from app.database import AsyncSessionLocal
    from app.meli_client import MeliClient

    async with AsyncSessionLocal() as db:
        client = MeliClient(db=db)
        status = await client.get_token_status()

    for k, v in status.items():
        print(f"  {k}: {v}")


async def cmd_reset_order(order_id: str):
    from app.database import AsyncSessionLocal
    from app.models import ProcessedOrder, SentMessage
    from sqlalchemy import delete

    async with AsyncSessionLocal() as db:
        await db.execute(delete(ProcessedOrder).where(ProcessedOrder.order_id == order_id))
        await db.execute(delete(SentMessage).where(SentMessage.order_id == order_id))
        await db.commit()
    print(f"  ✅ Orden {order_id} eliminada de la DB. Puedes re-procesarla.")


async def cmd_clear_db():
    from app.database import AsyncSessionLocal
    from app.models import ProcessedOrder, SentMessage, AgentEvent
    from sqlalchemy import delete

    async with AsyncSessionLocal() as db:
        await db.execute(delete(SentMessage))
        await db.execute(delete(ProcessedOrder))
        await db.execute(delete(AgentEvent))
        await db.commit()
    print("  ✅ DB de debug limpiada (tokens preservados)")


def _preview_message(order_id: str):
    from app.message_templates import build_shipping_request_message
    msg = build_shipping_request_message(order_id, buyer_name="María González")
    print(f"\n  ── Preview del mensaje que recibiría el comprador ──")
    print()
    for line in msg.split("\n"):
        print(f"  │ {line}")
    print(f"\n  Largo total: {len(msg)} caracteres")


def _print_help():
    print("""
  Comandos disponibles:
  ─────────────────────────────────────────────────────────────
  status                → Estado: token, DB, counts
  orders                → Listar órdenes procesadas
  process <order_id>    → Forzar procesamiento de una orden
  messages <order_id>   → Ver mensajes enviados a esa orden
  events [N]            → Ver últimos N eventos (default: 10)
  webhook <order_id>    → Simular webhook de ML (necesita agente corriendo)
  token                 → Estado del token OAuth
  reset <order_id>      → Borrar orden de DB para re-testear
  message-preview       → Ver cómo quedaría el mensaje al comprador
  clear                 → Limpiar toda la DB de debug
  quit                  → Salir

  IDs de prueba del mock server:
  ─────────────────────────────────────────────────────────────
  1000000001  → ✅ Custom+Paid  (debe enviar mensaje)
  1000000002  → ⏭️  Me2+Paid    (debe ignorar)
  1000000003  → ⏭️  Custom+Conf (debe ignorar - no pagada)
  1000000004  → 💬 Buyer ya respondió
  1000000005  → 🚫 Cancelada
""")


if __name__ == "__main__":
    asyncio.run(main())
