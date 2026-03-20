"""
config.py — Configuración centralizada con Pydantic Settings.
Carga variables desde .env automáticamente.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Mercado Libre ─────────────────────────────────────────────────────────
    MELI_CLIENT_ID: str
    MELI_CLIENT_SECRET: str
    MELI_REDIRECT_URI: str = "https://tu-dominio.com/auth/callback"
    MELI_SELLER_ID: str = ""          # Se autocompleta tras el primer OAuth
    MELI_WEBHOOK_SECRET: str = ""     # Secret configurado en el portal ML (opcional)

    # ── Base URL ML ───────────────────────────────────────────────────────────
    MELI_API_BASE: str = "https://api.mercadolibre.com"
    MELI_AUTH_BASE: str = "https://auth.mercadolibre.cl"

    # ── Base de datos ─────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite+aiosqlite:///./divinittys_agent.db"
    # Para producción: "postgresql+asyncpg://user:pass@localhost/divinittys"

    # ── Notificaciones internas (Telegram) ───────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""        # Tu chat_id personal

    # ── Datos bancarios del vendedor (se incluyen en el mensaje al comprador) ─
    BANK_NAME: str = "Banco Estado"
    BANK_ACCOUNT_TYPE: str = "Cuenta Corriente"
    BANK_ACCOUNT_NUMBER: str = "000000000"
    BANK_RUT: str = "12.345.678-9"
    BANK_OWNER: str = "Nombre Titular"
    BANK_EMAIL: str = "rekkiem@gmail.com"
    SHIPPING_COST: int = 4000         # Costo envío BlueExpress en pesos CLP

    # ── Polling fallback ──────────────────────────────────────────────────────
    POLLING_INTERVAL_MINUTES: int = 5
    POLLING_LOOKBACK_HOURS: int = 2   # Revisar órdenes de las últimas N horas

    # ── Ambiente ──────────────────────────────────────────────────────────────
    ENVIRONMENT: str = "production"   # "development" | "production"
    DEBUG: bool = False

    # ── Seguridad del panel admin ─────────────────────────────────────────────
    ADMIN_USERNAME: str = "divinittys"
    ADMIN_PASSWORD: str = ""           # OBLIGATORIO en producción
    ADMIN_ALLOWED_IPS: str = ""        # IPs/CIDRs separadas por coma. Vacío = todas

    # ── Reintentos ────────────────────────────────────────────────────────────
    MAX_RETRIES: int = 3
    RETRY_DELAY_SECONDS: int = 10


settings = Settings()
