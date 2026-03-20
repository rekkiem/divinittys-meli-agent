"""
auth_middleware.py — Seguridad del panel de administración.

Protege las rutas /admin/* con dos capas:
  1. HTTP Basic Auth (usuario/contraseña configurables en .env)
  2. Allowlist de IPs (opcional — para acceso solo desde tu red/VPN)

Por qué Basic Auth y no JWT:
  - El panel es de uso personal (1 usuario)
  - No requiere sesiones ni refresh tokens
  - Funciona directamente en el navegador sin frontend extra
  - Simple de configurar con Nginx como proxy adicional

IMPORTANTE: Funciona SOLO con HTTPS. Con HTTP los credentials van en base64
(no cifrados). Asegúrate de tener Nginx + SSL activo en producción.
"""

import base64
import hashlib
import hmac
import logging
from ipaddress import ip_address, ip_network

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

logger = logging.getLogger("divinittys.auth")

# ── Rutas que requieren autenticación ────────────────────────────────────────
PROTECTED_PREFIXES = ["/admin/"]

# ── Rutas admin que son públicas (salud del sistema) ─────────────────────────
PUBLIC_ADMIN_ROUTES = {"/admin/api/events/stream"}  # SSE requiere manejo especial


class AdminAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware de autenticación para rutas /admin/*.
    Se registra en el app FastAPI una sola vez.
    """

    def __init__(self, app, admin_username: str, admin_password: str,
                 allowed_ips: list[str] | None = None):
        super().__init__(app)
        self.admin_username = admin_username
        # Almacenar hash de la contraseña (nunca en texto plano en memoria)
        self.password_hash = hashlib.sha256(admin_password.encode()).hexdigest()
        self.allowed_ips = self._parse_ip_list(allowed_ips or [])
        self.realm = "Divinittys Admin"

    def _parse_ip_list(self, ips: list[str]) -> list:
        """Parsea IPs y rangos CIDR en una lista de objetos de red."""
        networks = []
        for ip_str in ips:
            ip_str = ip_str.strip()
            if not ip_str:
                continue
            try:
                networks.append(ip_network(ip_str, strict=False))
            except ValueError:
                logger.warning(f"⚠️ IP/CIDR inválido ignorado: {ip_str}")
        return networks

    def _is_ip_allowed(self, client_ip: str) -> bool:
        """Verifica si la IP del cliente está en la allowlist."""
        if not self.allowed_ips:
            return True  # Sin allowlist → aceptar todas las IPs
        try:
            addr = ip_address(client_ip)
            return any(addr in network for network in self.allowed_ips)
        except ValueError:
            return False

    def _verify_credentials(self, authorization: str | None) -> bool:
        """Verifica el header Authorization: Basic <base64>."""
        if not authorization or not authorization.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(authorization[6:]).decode("utf-8")
            username, _, password = decoded.partition(":")
            # Comparación de username
            username_ok = hmac.compare_digest(username, self.admin_username)
            # Comparación de password via hash (timing-safe)
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            password_ok = hmac.compare_digest(password_hash, self.password_hash)
            return username_ok and password_ok
        except Exception:
            return False

    def _unauthorized_response(self) -> Response:
        """Respuesta 401 con prompt de login del navegador."""
        return Response(
            content='{"detail": "Autenticación requerida"}',
            status_code=401,
            headers={
                "WWW-Authenticate": f'Basic realm="{self.realm}"',
                "Content-Type": "application/json",
            },
        )

    def _forbidden_response(self, client_ip: str) -> JSONResponse:
        """Respuesta 403 para IPs no permitidas."""
        logger.warning(f"🚫 Acceso admin bloqueado desde IP: {client_ip}")
        return JSONResponse(
            {"detail": f"IP {client_ip} no autorizada"},
            status_code=403,
        )

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Solo proteger rutas /admin/
        is_protected = any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES)
        if not is_protected:
            return await call_next(request)

        # ── 1. Verificar IP ───────────────────────────────────────────────────
        client_ip = (
            request.headers.get("X-Real-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown")
        )

        if not self._is_ip_allowed(client_ip):
            return self._forbidden_response(client_ip)

        # ── 2. Verificar credenciales Basic Auth ──────────────────────────────
        authorization = request.headers.get("Authorization")
        if not self._verify_credentials(authorization):
            logger.info(f"🔒 Intento de acceso admin no autenticado desde {client_ip}")
            return self._unauthorized_response()

        logger.debug(f"✅ Acceso admin autenticado desde {client_ip}")
        return await call_next(request)


def setup_admin_auth(app) -> None:
    """
    Registra el middleware de autenticación admin en la app FastAPI.
    Llamar desde main.py después de crear la app.
    """
    username = getattr(settings, "ADMIN_USERNAME", "divinittys")
    password = getattr(settings, "ADMIN_PASSWORD", "")

    if not password:
        logger.warning(
            "⚠️ ADMIN_PASSWORD no configurado en .env. "
            "El panel /admin/ NO está protegido. "
            "Configura ADMIN_USERNAME y ADMIN_PASSWORD en producción."
        )
        return

    allowed_ips_raw = getattr(settings, "ADMIN_ALLOWED_IPS", "")
    allowed_ips = [ip.strip() for ip in allowed_ips_raw.split(",") if ip.strip()]

    app.add_middleware(
        AdminAuthMiddleware,
        admin_username=username,
        admin_password=password,
        allowed_ips=allowed_ips if allowed_ips else None,
    )
    logger.info(
        f"🔐 Admin auth activado. Usuario: {username} | "
        f"IPs permitidas: {allowed_ips or 'todas'}"
    )
