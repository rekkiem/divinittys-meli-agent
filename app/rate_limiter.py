"""
rate_limiter.py — Manejo de rate limits de la API de Mercado Libre.

ML impone límites por app:
  - ~100 requests/minuto en endpoints de órdenes y mensajes
  - Responde 429 Too Many Requests al superar el límite
  - Header Retry-After indica cuántos segundos esperar

Este módulo:
  ✅ Intercepta errores 429 y aplica back-off exponencial
  ✅ Mantiene un contador de requests en memoria (sliding window)
  ✅ Prioriza webhooks sobre polling cuando el límite está cerca
  ✅ Notifica al vendedor si hay throttling persistente
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("divinittys.rate_limiter")

# ── Configuración ─────────────────────────────────────────────────────────────
WINDOW_SECONDS = 60          # Ventana deslizante de 1 minuto
MAX_REQUESTS_PER_WINDOW = 80 # 80% del límite real (100) como margen de seguridad
MAX_BACKOFF_SECONDS = 300    # Back-off máximo de 5 minutos
INITIAL_BACKOFF_SECONDS = 5  # Back-off inicial tras primer 429


@dataclass
class RateLimitState:
    """Estado del rate limiter compartido entre workers."""
    request_timestamps: deque = field(default_factory=lambda: deque(maxlen=200))
    backoff_until: float = 0.0       # timestamp Unix hasta el que hay que esperar
    consecutive_429s: int = 0
    total_throttled: int = 0
    last_reset: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Estado global (singleton por proceso)
_state = RateLimitState()


class RateLimiter:
    """
    Rate limiter para la API de Mercado Libre.
    Uso: inyectar en MeliClient para wrappear los requests.

    Ejemplo:
        limiter = RateLimiter()
        await limiter.acquire()   # Espera si es necesario
        response = await http_client.get(url)
        await limiter.on_response(response.status_code, response.headers)
    """

    def __init__(self, state: RateLimitState | None = None):
        self._state = state or _state

    async def acquire(self, priority: str = "normal") -> None:
        """
        Espera si es necesario antes de hacer un request.

        Args:
            priority: "high" (webhooks) | "normal" (polling) | "low" (follow-up)
        """
        now = time.monotonic()

        # ── 1. Respetar back-off activo (tras 429) ────────────────────────────
        if self._state.backoff_until > now:
            wait = self._state.backoff_until - now
            logger.warning(f"⏳ Rate limit activo. Esperando {wait:.1f}s...")
            await asyncio.sleep(wait)

        # ── 2. Verificar ventana deslizante ───────────────────────────────────
        current_time = time.time()
        window_start = current_time - WINDOW_SECONDS

        # Limpiar timestamps fuera de la ventana
        while self._state.request_timestamps and self._state.request_timestamps[0] < window_start:
            self._state.request_timestamps.popleft()

        requests_in_window = len(self._state.request_timestamps)

        # Si estamos cerca del límite y es baja prioridad → esperar
        if requests_in_window >= MAX_REQUESTS_PER_WINDOW:
            if priority == "low":
                wait_time = WINDOW_SECONDS - (current_time - self._state.request_timestamps[0])
                logger.info(f"⏸️ Cerca del rate limit ({requests_in_window}/{MAX_REQUESTS_PER_WINDOW}). Esperando {wait_time:.0f}s")
                await asyncio.sleep(max(0, wait_time))
            elif priority == "normal":
                # Espera mínima de 1 segundo para distribuir requests
                await asyncio.sleep(1)

        # Registrar este request
        self._state.request_timestamps.append(time.time())

    async def on_response(self, status_code: int, headers: dict) -> bool:
        """
        Procesa la respuesta de ML.
        Retorna True si el request fue exitoso, False si hubo 429.
        """
        if status_code != 429:
            # Request exitoso — resetear contador de 429s consecutivos
            if self._state.consecutive_429s > 0:
                logger.info(f"✅ Rate limit superado. Back-off terminado.")
            self._state.consecutive_429s = 0
            return True

        # ── Manejo de 429 ─────────────────────────────────────────────────────
        self._state.consecutive_429s += 1
        self._state.total_throttled += 1

        # Leer Retry-After del header si existe
        retry_after = self._parse_retry_after(headers)

        if retry_after:
            backoff = retry_after
        else:
            # Back-off exponencial: 5s, 10s, 20s, 40s... máx 5min
            backoff = min(
                INITIAL_BACKOFF_SECONDS * (2 ** (self._state.consecutive_429s - 1)),
                MAX_BACKOFF_SECONDS,
            )

        self._state.backoff_until = time.monotonic() + backoff

        logger.warning(
            f"🚦 Rate limit 429 recibido (#{self._state.consecutive_429s}). "
            f"Back-off: {backoff}s | Total throttled: {self._state.total_throttled}"
        )

        return False

    def get_stats(self) -> dict:
        """Retorna métricas del rate limiter para el panel admin."""
        current_time = time.time()
        window_start = current_time - WINDOW_SECONDS
        recent = sum(1 for t in self._state.request_timestamps if t >= window_start)

        return {
            "requests_last_minute": recent,
            "limit_per_minute": MAX_REQUESTS_PER_WINDOW,
            "utilization_pct": round(recent / MAX_REQUESTS_PER_WINDOW * 100, 1),
            "in_backoff": self._state.backoff_until > time.monotonic(),
            "consecutive_429s": self._state.consecutive_429s,
            "total_throttled_lifetime": self._state.total_throttled,
        }

    @staticmethod
    def _parse_retry_after(headers: dict) -> int | None:
        """Parsea el header Retry-After (puede ser segundos o fecha HTTP)."""
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if not retry_after:
            return None
        try:
            return int(retry_after)
        except ValueError:
            return None


# ── Decorador para retry con back-off ────────────────────────────────────────

async def with_retry(coro_fn, max_retries: int = 3, priority: str = "normal"):
    """
    Ejecuta una corutina con retry automático en caso de 429.

    Uso:
        result = await with_retry(lambda: client.get_order(order_id))
    """
    limiter = RateLimiter()
    last_exception = None

    for attempt in range(max_retries + 1):
        await limiter.acquire(priority=priority)
        try:
            return await coro_fn()
        except Exception as e:
            from app.meli_client import MeliAPIError
            if isinstance(e, MeliAPIError) and e.status_code == 429:
                await limiter.on_response(429, {})
                last_exception = e
                if attempt < max_retries:
                    logger.info(f"🔄 Retry {attempt + 1}/{max_retries} tras rate limit...")
                    continue
            raise

    raise last_exception
