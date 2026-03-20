"""
meli_client.py — Cliente HTTP para la API de Mercado Libre.

Responsabilidades:
  ✅ Intercambio de código OAuth → token
  ✅ Refresh automático del access_token (expira cada 6h)
  ✅ Persistencia del token en base de datos
  ✅ Llamadas autenticadas a la API con retry inteligente
  ✅ Envío de mensajes por la mensajería oficial de la orden
  ✅ Lectura del hilo de mensajes de una orden
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AgentEvent, OAuthToken

logger = logging.getLogger("divinittys.meli_client")

# ─── Constantes ───────────────────────────────────────────────────────────────
TOKEN_REFRESH_BUFFER_SECONDS = 600  # Refrescar 10 min antes de expirar
MELI_TOKEN_URL = f"{settings.MELI_API_BASE}/oauth/token"


class MeliAuthError(Exception):
    """Token no disponible o inválido. Requiere re-autorización manual."""


class MeliAPIError(Exception):
    """Error de la API de Mercado Libre."""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"[HTTP {status_code}] {message}")


# ─── Cliente Principal ────────────────────────────────────────────────────────

class MeliClient:
    """
    Cliente async para la API de Mercado Libre Chile.
    Inyecta la sesión de DB para gestionar el token persistido.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self._http = httpx.AsyncClient(
            timeout=30.0,
            headers={"Accept": "application/json"},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._http.aclose()

    # ── Token Management ─────────────────────────────────────────────────────

    async def exchange_code_for_token(self, code: str) -> dict:
        """
        Primera autenticación: intercambia el authorization_code por tokens.
        Persiste en DB. Solo se llama UNA vez manualmente via /auth/callback.
        """
        resp = await self._http.post(
            MELI_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.MELI_CLIENT_ID,
                "client_secret": settings.MELI_CLIENT_SECRET,
                "code": code,
                "redirect_uri": settings.MELI_REDIRECT_URI,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        await self._persist_token(data)
        return data

    async def _persist_token(self, token_data: dict) -> None:
        """Guarda o actualiza el token en la base de datos."""
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])

        # Buscar token existente
        result = await self.db.execute(select(OAuthToken).where(OAuthToken.id == 1))
        token_row = result.scalar_one_or_none()

        if token_row:
            token_row.access_token = token_data["access_token"]
            token_row.refresh_token = token_data["refresh_token"]
            token_row.expires_at = expires_at
            token_row.scope = token_data.get("scope", "")
            token_row.seller_id = str(token_data.get("user_id", token_row.seller_id))
        else:
            token_row = OAuthToken(
                id=1,
                seller_id=str(token_data.get("user_id", "")),
                access_token=token_data["access_token"],
                refresh_token=token_data["refresh_token"],
                expires_at=expires_at,
                scope=token_data.get("scope", ""),
            )
            self.db.add(token_row)

        await self.db.commit()
        await self._log_event("token_refreshed", detail=f"Token renovado. Expira: {expires_at.isoformat()}")
        logger.info(f"🔑 Token persistido. Expira: {expires_at}")

    async def _get_valid_token(self) -> str:
        """
        Retorna un access_token válido.
        Si está por expirar (< 10 min), refresca automáticamente.
        Lanza MeliAuthError si no hay token en DB.
        """
        result = await self.db.execute(select(OAuthToken).where(OAuthToken.id == 1))
        token_row = result.scalar_one_or_none()

        if not token_row:
            raise MeliAuthError(
                "No hay token OAuth en la base de datos. "
                "Visita /auth/login para autorizar el agente."
            )

        now = datetime.now(timezone.utc)
        time_until_expiry = (token_row.expires_at - now).total_seconds()

        if time_until_expiry < TOKEN_REFRESH_BUFFER_SECONDS:
            logger.info(f"⏰ Token expira en {time_until_expiry:.0f}s. Refrescando...")
            await self._refresh_token(token_row)
            # Recargar de DB
            await self.db.refresh(token_row)

        return token_row.access_token

    async def _refresh_token(self, token_row: OAuthToken) -> None:
        """Usa el refresh_token para obtener un nuevo access_token."""
        try:
            resp = await self._http.post(
                MELI_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": settings.MELI_CLIENT_ID,
                    "client_secret": settings.MELI_CLIENT_SECRET,
                    "refresh_token": token_row.refresh_token,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            await self._persist_token(data)
        except httpx.HTTPStatusError as e:
            error_body = e.response.text
            logger.error(f"❌ Fallo al refrescar token: {error_body}")
            await self._log_event("api_error", severity="error",
                                  detail=f"Fallo refresh_token: {error_body}")
            raise MeliAuthError(f"No se pudo refrescar el token: {error_body}") from e

    async def get_token_status(self) -> dict:
        """Retorna el estado actual del token (para /admin/token-status)."""
        result = await self.db.execute(select(OAuthToken).where(OAuthToken.id == 1))
        token_row = result.scalar_one_or_none()
        if not token_row:
            return {"status": "no_token", "message": "Visita /auth/login para autorizar"}
        now = datetime.now(timezone.utc)
        seconds_left = (token_row.expires_at - now).total_seconds()
        return {
            "status": "valid" if seconds_left > 0 else "expired",
            "seller_id": token_row.seller_id,
            "expires_at": token_row.expires_at.isoformat(),
            "seconds_until_expiry": int(seconds_left),
            "scope": token_row.scope,
        }

    # ── API Calls ─────────────────────────────────────────────────────────────

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Wrapper autenticado con manejo de errores y 1 retry en 401."""
        token = await self._get_valid_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{settings.MELI_API_BASE}{path}"

        for attempt in range(2):  # 1 retry si el token expiró mid-flight
            resp = await self._http.request(method, url, headers=headers, **kwargs)

            if resp.status_code == 401 and attempt == 0:
                logger.warning("🔄 401 recibido. Forzando refresh de token...")
                result = await self.db.execute(select(OAuthToken).where(OAuthToken.id == 1))
                token_row = result.scalar_one_or_none()
                if token_row:
                    await self._refresh_token(token_row)
                    token = await self._get_valid_token()
                    headers["Authorization"] = f"Bearer {token}"
                continue

            if resp.status_code >= 400:
                raise MeliAPIError(resp.status_code, resp.text)

            return resp.json()

        raise MeliAPIError(401, "Token inválido después de refresh")

    async def get_order(self, order_id: str) -> dict:
        """Obtiene los detalles completos de una orden."""
        return await self._request("GET", f"/orders/{order_id}")

    async def search_custom_shipping_orders(self, seller_id: str, date_from: str) -> list[dict]:
        """
        Busca órdenes 'paid' con shipping mode 'custom' desde una fecha.
        date_from formato: ISO 8601 (ej: "2025-01-01T00:00:00.000-03:00")
        """
        path = (
            f"/orders/search"
            f"?seller={seller_id}"
            f"&order.status=paid"
            f"&order.date_created.from={date_from}"
            f"&shipping.mode=custom"
            f"&limit=50"
        )
        data = await self._request("GET", path)
        return data.get("results", [])

    async def get_order_messages(self, pack_id: str, seller_id: str) -> list[dict]:
        """
        Obtiene el hilo de mensajes de una orden/pack.
        Usa el endpoint oficial de mensajería de ML.
        """
        data = await self._request(
            "GET",
            f"/messages/packs/{pack_id}/sellers/{seller_id}"
            f"?tag=post_sale"
            f"&limit=50"
        )
        return data.get("messages", [])

    async def send_message(self, pack_id: str, seller_id: str, message_text: str) -> dict:
        """
        Envía un mensaje al comprador por el canal oficial de la orden.

        IMPORTANTE (anti-ban): El texto NO debe contener links externos.
        Los datos bancarios van en texto plano dentro del body.
        """
        payload = {
            "from": {
                "user_id": seller_id,
                "role": "SELLER",
            },
            "to": {
                "role": "BUYER",  # ML determina el buyer_id automáticamente por el pack
            },
            "text": message_text,
            "message_attachments": None,
        }

        return await self._request(
            "POST",
            f"/messages/packs/{pack_id}/sellers/{seller_id}",
            json=payload,
        )

    async def get_seller_info(self) -> dict:
        """Obtiene el ID y datos del vendedor autenticado."""
        return await self._request("GET", "/users/me")

    # ── Helpers de DB ─────────────────────────────────────────────────────────

    async def _log_event(
        self,
        event_type: str,
        severity: str = "info",
        order_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Registra un evento en la tabla agent_events."""
        event = AgentEvent(
            event_type=event_type,
            severity=severity,
            order_id=order_id,
            detail=detail,
        )
        self.db.add(event)
        # No hacer commit aquí; lo hace el caller o la sesión al cerrar
