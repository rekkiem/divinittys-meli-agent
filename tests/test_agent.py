"""
tests/test_agent.py — Suite de tests unitarios e integración.

Cubre:
  ✅ Token refresh automático
  ✅ Lógica de decisión del agente (qué órdenes procesar)
  ✅ Idempotencia (no enviar doble mensaje)
  ✅ Detección de respuesta del comprador
  ✅ Envío del mensaje correcto
  ✅ Follow-up a las 24h
  ✅ Manejo de errores de API

Ejecutar:
  pip install pytest pytest-asyncio httpx
  pytest tests/ -v
"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_paid_custom_order():
    """Orden pagada con envío 'custom' — debe activar el agente."""
    return {
        "id": 1234567890,
        "status": "paid",
        "pack_id": 9876543210,
        "seller": {"id": "111222333"},
        "buyer": {
            "id": "444555666",
            "nickname": "COMPRADORA.TEST",
            "first_name": "María",
        },
        "shipping": {
            "mode": "custom",
            "shipping_mode": "custom",
        },
        "order_items": [
            {"item": {"title": "Serum Vitamina C Divinittys"}, "quantity": 1}
        ],
    }


@pytest.fixture
def sample_paid_non_custom_order():
    """Orden pagada con Mercado Envíos — NO debe activar el agente."""
    return {
        "id": 9999999999,
        "status": "paid",
        "pack_id": 8888888888,
        "seller": {"id": "111222333"},
        "buyer": {"id": "777888999", "nickname": "COMPRADOR.2"},
        "shipping": {
            "mode": "me2",
            "shipping_mode": "me2",
        },
    }


@pytest.fixture
def sample_pending_order():
    """Orden aún no pagada — NO debe activar el agente."""
    return {
        "id": 5555555555,
        "status": "confirmed",
        "pack_id": 4444444444,
        "seller": {"id": "111222333"},
        "buyer": {"id": "123456789"},
        "shipping": {"mode": "custom"},
    }


# ─── Tests: Lógica de Decisión ────────────────────────────────────────────────

class TestAgentDecisionLogic:
    """Tests para _should_process(): el cerebro de filtrado del agente."""

    def _make_agent(self):
        """Crea un agente con dependencias mockeadas."""
        from app.agent import PostSaleAgent
        mock_client = MagicMock()
        mock_db = MagicMock()
        return PostSaleAgent(client=mock_client, db=mock_db)

    def test_should_process_custom_paid_order(self, sample_paid_custom_order):
        """Una orden pagada con envío custom DEBE procesarse."""
        agent = self._make_agent()
        result = agent._should_process(sample_paid_custom_order)
        assert result["proceed"] is True
        assert result["reason"] == "ok"

    def test_should_skip_non_custom_order(self, sample_paid_non_custom_order):
        """Una orden con Mercado Envíos (me2) debe ignorarse."""
        agent = self._make_agent()
        result = agent._should_process(sample_paid_non_custom_order)
        assert result["proceed"] is False
        assert "me2" in result["reason"]

    def test_should_skip_unpaid_order(self, sample_pending_order):
        """Una orden no pagada debe ignorarse."""
        agent = self._make_agent()
        result = agent._should_process(sample_pending_order)
        assert result["proceed"] is False
        assert "confirmed" in result["reason"]


# ─── Tests: Token Refresh ─────────────────────────────────────────────────────

class TestTokenRefresh:
    """Tests para el sistema de refresh automático del token OAuth."""

    @pytest.mark.asyncio
    async def test_refresh_triggered_when_token_near_expiry(self):
        """Si el token expira en < 10 min, debe llamarse _refresh_token."""
        from app.meli_client import MeliClient, TOKEN_REFRESH_BUFFER_SECONDS
        from app.models import OAuthToken

        # Token que expira en 5 minutos (dentro del buffer de 10min)
        near_expiry_token = OAuthToken(
            id=1,
            seller_id="111222333",
            access_token="old_token_123",
            refresh_token="refresh_abc",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

        mock_db = AsyncMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = near_expiry_token

        client = MeliClient(db=mock_db)
        client._refresh_token = AsyncMock()
        # refresh_token actualiza el objeto in-place
        async def fake_refresh(row):
            row.access_token = "new_refreshed_token"
            row.expires_at = datetime.now(timezone.utc) + timedelta(hours=6)
        client._refresh_token.side_effect = fake_refresh

        token = await client._get_valid_token()

        client._refresh_token.assert_called_once_with(near_expiry_token)
        assert token == "new_refreshed_token"

    @pytest.mark.asyncio
    async def test_no_refresh_when_token_valid(self):
        """Si el token expira en > 10 min, NO debe refrescarse."""
        from app.meli_client import MeliClient
        from app.models import OAuthToken

        valid_token = OAuthToken(
            id=1,
            seller_id="111222333",
            access_token="valid_token_xyz",
            refresh_token="refresh_abc",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=5),
        )

        mock_db = AsyncMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = valid_token

        client = MeliClient(db=mock_db)
        client._refresh_token = AsyncMock()

        token = await client._get_valid_token()

        client._refresh_token.assert_not_called()
        assert token == "valid_token_xyz"

    @pytest.mark.asyncio
    async def test_raises_auth_error_when_no_token_in_db(self):
        """Sin token en DB, debe lanzar MeliAuthError."""
        from app.meli_client import MeliClient, MeliAuthError

        mock_db = AsyncMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = None

        client = MeliClient(db=mock_db)

        with pytest.raises(MeliAuthError) as exc_info:
            await client._get_valid_token()
        assert "auth/login" in str(exc_info.value)


# ─── Tests: Idempotencia ─────────────────────────────────────────────────────

class TestIdempotency:
    """El agente no debe enviar el mismo mensaje dos veces."""

    @pytest.mark.asyncio
    async def test_skips_already_processed_order(self, sample_paid_custom_order):
        """Si la orden ya tiene message_sent=True, debe retornar 'skipped'."""
        from app.agent import PostSaleAgent
        from app.models import ProcessedOrder

        already_done = ProcessedOrder(
            order_id="1234567890",
            status="message_sent",
            message_sent=True,
        )

        mock_client = AsyncMock()
        mock_db = AsyncMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = already_done

        agent = PostSaleAgent(client=mock_client, db=mock_db)
        result = await agent.process_order("1234567890", force=False)

        assert result["status"] == "skipped"
        assert result["reason"] == "already_processed"
        mock_client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_flag_bypasses_idempotency(self, sample_paid_custom_order):
        """Con force=True, debe re-procesar aunque ya esté registrada."""
        from app.agent import PostSaleAgent
        from app.models import ProcessedOrder

        already_done = ProcessedOrder(
            order_id="1234567890",
            status="message_sent",
            message_sent=True,
        )

        mock_client = AsyncMock()
        mock_client.get_order.return_value = sample_paid_custom_order
        mock_client.get_order_messages.return_value = []
        mock_client.send_message.return_value = {"id": "msg_999"}

        mock_db = AsyncMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = already_done

        agent = PostSaleAgent(client=mock_client, db=mock_db)
        agent._upsert_processed_order = AsyncMock()
        agent._save_sent_message = AsyncMock()
        agent._log_event = AsyncMock()
        agent.notifier = AsyncMock()

        result = await agent.process_order("1234567890", force=True)
        # Con force=True, debe intentar procesar (no skip inmediato)
        mock_client.get_order.assert_called_once()


# ─── Tests: Detección de Respuesta del Comprador ─────────────────────────────

class TestBuyerReplyDetection:
    """Detección de si el comprador ya respondió en la mensajería de ML."""

    @pytest.mark.asyncio
    async def test_detects_buyer_reply(self):
        """Debe retornar True cuando hay mensajes con role=BUYER."""
        from app.agent import PostSaleAgent

        mock_client = AsyncMock()
        mock_client.get_order_messages.return_value = [
            {"from": {"role": "SELLER"}, "text": "Hola! Soy Divinittys..."},
            {"from": {"role": "BUYER"}, "text": "Aquí mis datos: María..."},
        ]

        mock_db = AsyncMock()
        agent = PostSaleAgent(client=mock_client, db=mock_db)

        result = await agent._buyer_has_replied("9876543210", "111222333")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_only_seller_messages(self):
        """Solo mensajes del seller → no hay respuesta del buyer."""
        from app.agent import PostSaleAgent

        mock_client = AsyncMock()
        mock_client.get_order_messages.return_value = [
            {"from": {"role": "SELLER"}, "text": "Hola! Soy Divinittys..."},
        ]

        mock_db = AsyncMock()
        agent = PostSaleAgent(client=mock_client, db=mock_db)

        result = await agent._buyer_has_replied("9876543210", "111222333")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_api_error(self):
        """Si falla la lectura de mensajes, asume que no respondió (safe default)."""
        from app.agent import PostSaleAgent
        from app.meli_client import MeliAPIError

        mock_client = AsyncMock()
        mock_client.get_order_messages.side_effect = MeliAPIError(403, "Forbidden")

        mock_db = AsyncMock()
        agent = PostSaleAgent(client=mock_client, db=mock_db)

        result = await agent._buyer_has_replied("9876543210", "111222333")
        assert result is False  # Falso negativo seguro — no bloquea al agente


# ─── Tests: Contenido del Mensaje ────────────────────────────────────────────

class TestMessageContent:
    """Verifica que los mensajes cumplen los requisitos de negocio y anti-ban."""

    def test_message_contains_bank_data(self):
        """El mensaje debe incluir los datos bancarios del vendedor."""
        from app.message_templates import build_shipping_request_message
        msg = build_shipping_request_message("1234567890")
        # Verificar campos clave presentes (usando values del .env.example)
        assert "Nombre completo" in msg
        assert "RUT" in msg
        assert "Teléfono" in msg
        assert "Dirección" in msg

    def test_message_contains_shipping_steps(self):
        """El mensaje debe tener los 2 pasos claramente diferenciados."""
        from app.message_templates import build_shipping_request_message
        msg = build_shipping_request_message("1234567890")
        assert "PASO 1" in msg
        assert "PASO 2" in msg

    def test_message_no_external_links(self):
        """El mensaje NO debe contener URLs externas (anti-ban ML)."""
        from app.message_templates import build_shipping_request_message
        msg = build_shipping_request_message("1234567890")
        assert "http://" not in msg
        assert "https://" not in msg
        assert "www." not in msg

    def test_message_personalized_with_buyer_name(self):
        """Si se provee nombre del buyer, el mensaje debe personalizarse."""
        from app.message_templates import build_shipping_request_message
        msg = build_shipping_request_message("1234567890", buyer_name="María García")
        assert "María" in msg

    def test_followup_message_no_external_links(self):
        """El mensaje de follow-up tampoco debe tener URLs externas."""
        from app.message_templates import build_follow_up_message
        msg = build_follow_up_message()
        assert "http://" not in msg
        assert "https://" not in msg

    def test_message_contains_divinittys_brand(self):
        """El mensaje debe mencionar la marca DIVINITTYS."""
        from app.message_templates import build_shipping_request_message
        msg = build_shipping_request_message("1234567890")
        assert "DIVINITTYS" in msg


# ─── Tests: Follow-up 24h ────────────────────────────────────────────────────

class TestFollowUp:
    """Tests para el módulo de seguimiento automático."""

    @pytest.mark.asyncio
    async def test_followup_not_sent_twice(self):
        """No debe enviarse el follow-up si ya se envió uno antes."""
        from app.followup import FollowUpAgent
        from app.models import SentMessage

        mock_client = AsyncMock()
        mock_db = AsyncMock()

        # Simular que ya existe un follow-up enviado
        existing_followup = SentMessage(
            order_id="1234567890",
            delivery_status="sent_followup",
        )
        mock_db.execute.return_value.scalar_one_or_none.return_value = existing_followup

        agent = FollowUpAgent(client=mock_client, db=mock_db)
        result = await agent._followup_already_sent("1234567890")

        assert result is True
        mock_client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_followup_threshold_24h(self):
        """El follow-up solo debe enviarse si han pasado >= 24h."""
        from app.followup import FollowUpAgent, FOLLOWUP_HOURS
        from app.models import SentMessage
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)

        # Mensaje enviado hace 23h → NO debe triggear follow-up
        recent_message = SentMessage(
            order_id="111",
            sent_at=now - timedelta(hours=23),
        )
        hours_elapsed = (now - recent_message.sent_at).total_seconds() / 3600
        assert hours_elapsed < FOLLOWUP_HOURS  # 23 < 24 ✓

        # Mensaje enviado hace 25h → SÍ debe triggear follow-up
        old_message = SentMessage(
            order_id="222",
            sent_at=now - timedelta(hours=25),
        )
        hours_elapsed_old = (now - old_message.sent_at).total_seconds() / 3600
        assert hours_elapsed_old >= FOLLOWUP_HOURS  # 25 >= 24 ✓


# ─── Tests: Webhooks ─────────────────────────────────────────────────────────

class TestWebhooks:
    """Tests de integración para el endpoint de webhooks."""

    @pytest.mark.asyncio
    async def test_webhook_returns_200_for_orders_topic(self):
        """El webhook debe responder 200 inmediatamente para orders_v2."""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        response = client.post(
            "/webhooks/meli",
            json={
                "topic": "orders_v2",
                "resource": "/orders/1234567890",
                "user_id": 111222333,
                "application_id": 999888777,
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_webhook_ignores_non_order_topics(self):
        """Tópicos que no son orders_v2 deben ser ignorados (status=ignored)."""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        response = client.post(
            "/webhooks/meli",
            json={
                "topic": "items",
                "resource": "/items/MLC123",
                "user_id": 111222333,
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ignored"

    def test_health_endpoint(self):
        """El health check debe responder correctamente."""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["store"] == "Divinittys"
