"""
Migración inicial — Crea todas las tablas del agente Divinittys.

Revision ID: 0001_initial
Revises: —
Create Date: 2025-01-01
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── oauth_tokens ──────────────────────────────────────────────────────────
    op.create_table(
        "oauth_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("seller_id", sa.String(50), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── processed_orders ──────────────────────────────────────────────────────
    op.create_table(
        "processed_orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.String(50), nullable=False),
        sa.Column("pack_id", sa.String(50), nullable=True),
        sa.Column("buyer_id", sa.String(50), nullable=True),
        sa.Column("status", sa.String(30), nullable=True),
        sa.Column("shipping_mode", sa.String(30), nullable=True),
        sa.Column("message_sent", sa.Boolean(), nullable=True),
        sa.Column("buyer_replied", sa.Boolean(), nullable=True),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("last_checked", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
    )
    op.create_index("ix_processed_orders_order_id", "processed_orders", ["order_id"])

    # ── sent_messages ─────────────────────────────────────────────────────────
    op.create_table(
        "sent_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.String(50), nullable=False),
        sa.Column("pack_id", sa.String(50), nullable=True),
        sa.Column("meli_message_id", sa.String(100), nullable=True),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("delivery_status", sa.String(20), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sent_messages_order_id", "sent_messages", ["order_id"])

    # ── agent_events ──────────────────────────────────────────────────────────
    op.create_table(
        "agent_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(10), nullable=True),
        sa.Column("order_id", sa.String(50), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_events_event_type", "agent_events", ["event_type"])
    op.create_index("ix_agent_events_order_id", "agent_events", ["order_id"])


def downgrade() -> None:
    op.drop_table("agent_events")
    op.drop_table("sent_messages")
    op.drop_table("processed_orders")
    op.drop_table("oauth_tokens")
