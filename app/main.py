"""
Divinittys - Agente de Post-Venta Automático para Mercado Libre Chile
main.py — FastAPI entry point: OAuth callback, Webhooks, Health check
"""

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings
from app.database import engine, get_db, init_db
from app.meli_client import MeliClient
from app.agent import PostSaleAgent
from app.scheduler import start_scheduler, stop_scheduler
from app.admin_panel import router as admin_router
from app.models import Base

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("divinittys.main")


# ─── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: init DB + start polling scheduler."""
    await init_db()
    scheduler = await start_scheduler()
    logger.info("✅ Divinittys Meli Agent iniciado")
    yield
    await stop_scheduler(scheduler)
    logger.info("🛑 Agente detenido")


app = FastAPI(
    title="Divinittys Post-Sale Agent",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(admin_router)


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Sistema"])
async def health():
    return {"status": "ok", "store": "Divinittys", "vendor": settings.MELI_SELLER_ID}


# ─── OAuth 2.0 Flow ──────────────────────────────────────────────────────────

@app.get("/auth/login", tags=["OAuth"])
async def oauth_login():
    """
    Redirige al portal de autorización de Mercado Libre.
    Visita esta URL UNA sola vez para obtener el primer token.
    """
    auth_url = (
        f"https://auth.mercadolibre.cl/authorization"
        f"?response_type=code"
        f"&client_id={settings.MELI_CLIENT_ID}"
        f"&redirect_uri={settings.MELI_REDIRECT_URI}"
        f"&code_challenge_method=none"  # PKCE opcional para server-side
    )
    return RedirectResponse(url=auth_url)


@app.get("/auth/callback", tags=["OAuth"])
async def oauth_callback(code: str, db=Depends(get_db)):
    """
    Mercado Libre redirige aquí con el 'code'.
    Intercambia el code por access_token + refresh_token y los persiste.
    """
    client = MeliClient(db=db)
    try:
        token_data = await client.exchange_code_for_token(code)
        logger.info(f"✅ Token obtenido para seller_id={token_data.get('user_id')}")
        return JSONResponse({
            "message": "Autorización exitosa. El agente está activo.",
            "seller_id": token_data.get("user_id"),
            "expires_in": token_data.get("expires_in"),
        })
    except Exception as e:
        logger.error(f"❌ Error en OAuth callback: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ─── Webhook Receiver ────────────────────────────────────────────────────────

def _verify_meli_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    """
    Valida la firma x-signature del webhook de Mercado Libre.
    Formato: ts=<timestamp>,v1=<hash>
    """
    if not signature or not secret:
        return True  # Si no hay secret configurado, saltar validación (dev mode)
    try:
        parts = dict(p.split("=", 1) for p in signature.split(","))
        ts = parts.get("ts", "")
        v1 = parts.get("v1", "")
        message = f"{settings.MELI_CLIENT_ID}:{ts}:{payload.decode()}"
        expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, v1)
    except Exception:
        return False


@app.post("/webhooks/meli", tags=["Webhooks"])
async def meli_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_signature: str | None = Header(default=None, alias="x-signature"),
    db=Depends(get_db),
):
    """
    Endpoint receptor de notificaciones push de Mercado Libre.
    Configura esta URL en el portal de desarrolladores:
      https://tu-dominio.com/webhooks/meli
    Topic: orders_v2
    """
    body = await request.body()

    # Verificar autenticidad del webhook
    if not _verify_meli_signature(body, x_signature, settings.MELI_WEBHOOK_SECRET):
        logger.warning("⚠️ Webhook con firma inválida recibido")
        raise HTTPException(status_code=401, detail="Firma inválida")

    payload = await request.json()
    logger.info(f"📩 Webhook recibido: topic={payload.get('topic')} resource={payload.get('resource')}")

    # Solo procesar notificaciones de órdenes
    topic = payload.get("topic", "")
    if topic not in ("orders_v2", "orders"):
        return {"status": "ignored", "reason": f"topic '{topic}' no relevante"}

    # Disparar procesamiento en background (responde 200 inmediato a ML)
    resource = payload.get("resource", "")
    order_id = resource.split("/")[-1] if "/" in resource else resource

    background_tasks.add_task(_process_order_background, order_id, db)

    return {"status": "accepted"}


async def _process_order_background(order_id: str, db):
    """Tarea background: procesa una orden y envía mensaje si aplica."""
    try:
        client = MeliClient(db=db)
        agent = PostSaleAgent(client=client, db=db)
        await agent.process_order(order_id)
    except Exception as e:
        logger.error(f"❌ Error procesando orden {order_id}: {e}", exc_info=True)


# ─── Manual trigger (testing) ────────────────────────────────────────────────

@app.post("/admin/process-order/{order_id}", tags=["Admin"])
async def manual_process(order_id: str, db=Depends(get_db)):
    """Fuerza el procesamiento de una orden. Solo para testing."""
    client = MeliClient(db=db)
    agent = PostSaleAgent(client=client, db=db)
    result = await agent.process_order(order_id, force=True)
    return result


@app.get("/admin/token-status", tags=["Admin"])
async def token_status(db=Depends(get_db)):
    """Verifica el estado del token OAuth actual."""
    client = MeliClient(db=db)
    status = await client.get_token_status()
    return status
