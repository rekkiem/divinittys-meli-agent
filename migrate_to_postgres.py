#!/usr/bin/env python3
"""
migrate_to_postgres.py — Script de migración SQLite → PostgreSQL.

Uso:
  python migrate_to_postgres.py

El script:
  1. Lee todos los datos desde la DB SQLite actual
  2. Los inserta en PostgreSQL (ya debe existir el schema)
  3. Verifica integridad post-migración

Pre-requisitos:
  - PostgreSQL corriendo y accesible
  - Tablas ya creadas en PG: `alembic upgrade head`
  - Variables en .env actualizadas con la nueva DATABASE_URL de PG
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ── Setup ─────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/divinittys_agent.db")

SQLITE_URL = "sqlite+aiosqlite:///./data/divinittys_agent.db"


async def migrate():
    """Migración completa SQLite → PostgreSQL."""
    print("🔄 Iniciando migración SQLite → PostgreSQL")
    print("=" * 60)

    # ── Importar después de configurar paths ──────────────────────────────────
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy import select, text

    from app.models import OAuthToken, ProcessedOrder, SentMessage, AgentEvent

    # ── Conexión SQLite (fuente) ───────────────────────────────────────────────
    sqlite_engine = create_async_engine(SQLITE_URL, echo=False)
    SQLiteSession = async_sessionmaker(sqlite_engine, expire_on_commit=False)

    # ── Conexión PostgreSQL (destino) ─────────────────────────────────────────
    from app.config import settings
    if "sqlite" in settings.DATABASE_URL:
        print("❌ ERROR: DATABASE_URL en .env todavía apunta a SQLite.")
        print("   Actualiza DATABASE_URL a la URL de PostgreSQL primero.")
        print("   Ejemplo: postgresql+asyncpg://divinittys:pass@localhost/divinittys")
        sys.exit(1)

    pg_engine = create_async_engine(settings.DATABASE_URL, echo=False)
    PGSession = async_sessionmaker(pg_engine, expire_on_commit=False)

    stats = {}

    async with SQLiteSession() as sqlite_db, PGSession() as pg_db:

        # ── 1. OAuthToken ─────────────────────────────────────────────────────
        print("\n📋 Migrando oauth_tokens...")
        result = await sqlite_db.execute(select(OAuthToken))
        tokens = result.scalars().all()
        for tok in tokens:
            new_tok = OAuthToken(
                id=tok.id,
                seller_id=tok.seller_id,
                access_token=tok.access_token,
                refresh_token=tok.refresh_token,
                expires_at=tok.expires_at,
                scope=tok.scope or "",
            )
            pg_db.add(new_tok)
        await pg_db.commit()
        stats["tokens"] = len(tokens)
        print(f"   ✅ {len(tokens)} tokens migrados")

        # ── 2. ProcessedOrders ────────────────────────────────────────────────
        print("\n📦 Migrando processed_orders...")
        result = await sqlite_db.execute(select(ProcessedOrder))
        orders = result.scalars().all()
        for o in orders:
            new_o = ProcessedOrder(
                order_id=o.order_id,
                pack_id=o.pack_id,
                buyer_id=o.buyer_id,
                status=o.status,
                shipping_mode=o.shipping_mode,
                message_sent=o.message_sent,
                buyer_replied=o.buyer_replied,
                skip_reason=o.skip_reason,
                processed_at=o.processed_at,
            )
            pg_db.add(new_o)
        await pg_db.commit()
        stats["orders"] = len(orders)
        print(f"   ✅ {len(orders)} órdenes migradas")

        # ── 3. SentMessages ───────────────────────────────────────────────────
        print("\n💬 Migrando sent_messages...")
        result = await sqlite_db.execute(select(SentMessage))
        messages = result.scalars().all()
        for m in messages:
            new_m = SentMessage(
                order_id=m.order_id,
                pack_id=m.pack_id,
                meli_message_id=m.meli_message_id,
                message_text=m.message_text,
                sent_at=m.sent_at,
                delivery_status=m.delivery_status,
            )
            pg_db.add(new_m)
        await pg_db.commit()
        stats["messages"] = len(messages)
        print(f"   ✅ {len(messages)} mensajes migrados")

        # ── 4. AgentEvents ────────────────────────────────────────────────────
        print("\n📊 Migrando agent_events...")
        result = await sqlite_db.execute(select(AgentEvent))
        events = result.scalars().all()
        for e in events:
            new_e = AgentEvent(
                event_type=e.event_type,
                severity=e.severity,
                order_id=e.order_id,
                detail=e.detail,
                created_at=e.created_at,
            )
            pg_db.add(new_e)
        await pg_db.commit()
        stats["events"] = len(events)
        print(f"   ✅ {len(events)} eventos migrados")

        # ── Verificación post-migración ───────────────────────────────────────
        print("\n🔍 Verificación de integridad...")
        pg_tokens = await pg_db.scalar(text("SELECT COUNT(*) FROM oauth_tokens"))
        pg_orders = await pg_db.scalar(text("SELECT COUNT(*) FROM processed_orders"))
        pg_msgs = await pg_db.scalar(text("SELECT COUNT(*) FROM sent_messages"))
        pg_events = await pg_db.scalar(text("SELECT COUNT(*) FROM agent_events"))

        all_ok = (
            pg_tokens == stats["tokens"]
            and pg_orders == stats["orders"]
            and pg_msgs == stats["messages"]
            and pg_events == stats["events"]
        )

        print(f"\n   oauth_tokens:     SQLite={stats['tokens']:>4}  PG={pg_tokens:>4}  {'✅' if pg_tokens == stats['tokens'] else '❌'}")
        print(f"   processed_orders: SQLite={stats['orders']:>4}  PG={pg_orders:>4}  {'✅' if pg_orders == stats['orders'] else '❌'}")
        print(f"   sent_messages:    SQLite={stats['messages']:>4}  PG={pg_msgs:>4}  {'✅' if pg_msgs == stats['messages'] else '❌'}")
        print(f"   agent_events:     SQLite={stats['events']:>4}  PG={pg_events:>4}  {'✅' if pg_events == stats['events'] else '❌'}")

    await sqlite_engine.dispose()
    await pg_engine.dispose()

    print("\n" + "=" * 60)
    if all_ok:
        print("🎉 Migración completada exitosamente.")
        print("\nPróximos pasos:")
        print("  1. Actualiza docker-compose.yml para usar el profile 'prod'")
        print("  2. Reinicia el agente: docker compose --profile prod up -d")
        print("  3. Verifica en /admin/dashboard que todo está OK")
    else:
        print("⚠️  Migración completada con discrepancias. Revisa los logs.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(migrate())
