"""
models.py — Modelos de base de datos.

Tablas:
  - oauth_tokens    : access/refresh token con expiración
  - processed_orders: registro de órdenes ya procesadas (idempotencia)
  - sent_messages   : historial de mensajes enviados
  - agent_events    : log de errores y eventos para el panel de admin
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OAuthToken(Base):
    """
    Almacena el token OAuth de Mercado Libre.
    Solo existe UNA fila (el token del vendedor de Divinittys).
    """
    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    seller_id: Mapped[str] = mapped_column(String(50), nullable=False)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProcessedOrder(Base):
    """
    Registro de órdenes ya procesadas por el agente.
    Garantiza idempotencia: no se envía el mismo mensaje dos veces.
    """
    __tablename__ = "processed_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    pack_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    buyer_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    # Estados: pending | message_sent | replied | skipped | error
    shipping_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    message_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    buyer_replied: Mapped[bool] = mapped_column(Boolean, default=False)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_checked: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SentMessage(Base):
    """Historial de mensajes enviados al comprador."""
    __tablename__ = "sent_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    pack_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    meli_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivery_status: Mapped[str] = mapped_column(String(20), default="sent")
    # Estados ML: sent | read | replied


class AgentEvent(Base):
    """
    Log de eventos del agente: errores de API, refreshes de token,
    respuestas de compradores, etc.
    """
    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # Tipos: token_refreshed | message_sent | buyer_replied | api_error
    #        order_skipped   | polling_run  | webhook_received
    severity: Mapped[str] = mapped_column(String(10), default="info")
    # Severidades: info | warning | error
    order_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
