"""
scheduler.py — Trabajos periódicos del agente.

Jobs:
  1. polling_job     : Fallback por si llega una orden sin webhook (cada N min)
  2. reply_check_job : Detecta cuando el comprador respondió (cada 15 min)

Usa APScheduler con asyncio.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.database import AsyncSessionLocal

logger = logging.getLogger("divinittys.scheduler")


async def polling_job():
    """
    Job periódico: busca órdenes 'custom' recientes y las procesa.
    Es el fallback cuando los webhooks fallan o el servidor estuvo caído.
    """
    from app.agent import PostSaleAgent
    from app.meli_client import MeliClient

    logger.info("⏰ Iniciando ciclo de polling...")
    async with AsyncSessionLocal() as db:
        try:
            client = MeliClient(db=db)

            # Obtener seller_id del token guardado
            from sqlalchemy import select

            from app.models import OAuthToken

            result = await db.execute(select(OAuthToken).where(OAuthToken.id == 1))
            token_row = result.scalar_one_or_none()

            if not token_row:
                logger.warning("⚠️ No hay token OAuth. Saltando ciclo de polling.")
                return

            seller_id = token_row.seller_id
            agent = PostSaleAgent(client=client, db=db)
            summary = await agent.run_polling_cycle(
                seller_id=seller_id,
                lookback_hours=settings.POLLING_LOOKBACK_HOURS,
            )
            logger.info(f"✅ Polling completado: {summary}")

        except Exception as e:
            logger.error(f"❌ Error en polling_job: {e}", exc_info=True)


async def reply_check_job():
    """
    Job periódico: verifica si algún comprador respondió a un mensaje enviado.
    Actualiza el estado en DB y notifica al vendedor.
    """
    from sqlalchemy import select

    from app.agent import PostSaleAgent
    from app.meli_client import MeliClient
    from app.models import OAuthToken, ProcessedOrder
    from app.notifications import Notifier

    logger.info("💬 Verificando respuestas de compradores...")
    async with AsyncSessionLocal() as db:
        try:
            # Obtener seller_id
            result = await db.execute(select(OAuthToken).where(OAuthToken.id == 1))
            token_row = result.scalar_one_or_none()
            if not token_row:
                return

            seller_id = token_row.seller_id
            client = MeliClient(db=db)
            notifier = Notifier()

            # Buscar órdenes donde se envió mensaje pero no se detectó respuesta aún
            pending = await db.execute(
                select(ProcessedOrder).where(
                    ProcessedOrder.message_sent,
                    ~ProcessedOrder.buyer_replied,
                    ProcessedOrder.status == "message_sent",
                )
            )
            pending_orders = pending.scalars().all()

            replied_count = 0
            for order in pending_orders:
                pack_id = order.pack_id or order.order_id
                agent = PostSaleAgent(client=client, db=db)
                has_replied = await agent._buyer_has_replied(pack_id, seller_id)

                if has_replied:
                    order.buyer_replied = True
                    order.status = "replied"
                    await db.commit()
                    await notifier.send_buyer_reply_alert(order.order_id, None)
                    replied_count += 1
                    logger.info(f"💬 Comprador respondió en orden {order.order_id}")

            if replied_count > 0:
                logger.info(f"✅ {replied_count} respuestas detectadas")

        except Exception as e:
            logger.error(f"❌ Error en reply_check_job: {e}", exc_info=True)


async def start_scheduler() -> AsyncIOScheduler:
    """Inicia el scheduler con todos los jobs configurados."""
    scheduler = AsyncIOScheduler()

    # Job 1: Polling de órdenes (fallback)
    scheduler.add_job(
        polling_job,
        trigger=IntervalTrigger(minutes=settings.POLLING_INTERVAL_MINUTES),
        id="polling_job",
        name="Polling de órdenes ML",
        replace_existing=True,
        misfire_grace_time=60,
    )
    logger.info(f"📋 Polling job: cada {settings.POLLING_INTERVAL_MINUTES} minutos")

    # Job 2: Verificación de respuestas de compradores
    scheduler.add_job(
        reply_check_job,
        trigger=IntervalTrigger(minutes=15),
        id="reply_check_job",
        name="Verificación de respuestas",
        replace_existing=True,
        misfire_grace_time=60,
    )
    logger.info("💬 Reply check job: cada 15 minutos")

    # Job 3: Follow-up automático a las 24h sin respuesta
    from app.followup import followup_job
    scheduler.add_job(
        followup_job,
        trigger=IntervalTrigger(hours=1),
        id="followup_job",
        name="Follow-up 24h sin respuesta",
        replace_existing=True,
        misfire_grace_time=120,
    )
    logger.info("🔔 Follow-up job: cada 1 hora")

    # Job 4: Detección de cancelaciones de órdenes
    from app.cancellation_handler import CancellationHandler

    async def cancellation_scan_job():
        async with AsyncSessionLocal() as db:
            try:
                from app.meli_client import MeliClient
                client = MeliClient(db=db)
                handler = CancellationHandler(client=client, db=db)
                await handler.scan_active_orders_for_cancellations()
            except Exception as e:
                logger.error(f"❌ Error en cancellation_scan_job: {e}", exc_info=True)

    scheduler.add_job(
        cancellation_scan_job,
        trigger=IntervalTrigger(minutes=30),
        id="cancellation_scan_job",
        name="Escaneo de cancelaciones ML",
        replace_existing=True,
        misfire_grace_time=120,
    )
    logger.info("🚫 Cancellation scan job: cada 30 minutos")

    scheduler.start()
    logger.info("✅ Scheduler iniciado")
    return scheduler


async def stop_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Detiene el scheduler limpiamente."""
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("🛑 Scheduler detenido")
