"""
tests/mock_meli_server.py — Servidor mock de la API de Mercado Libre.

Simula los endpoints reales de ML para testing 100% local, sin credenciales.
Levanta en http://localhost:9999 y acepta los mismos requests que la API real.

Ejecutar en terminal separada:
  python tests/mock_meli_server.py

Endpoints simulados:
  POST /oauth/token               → Retorna tokens ficticios
  GET  /users/me                  → Info del vendedor ficticio
  GET  /orders/{id}               → Órdenes de prueba configurables
  GET  /orders/search             → Lista de órdenes con shipping=custom
  GET  /messages/packs/{id}/...   → Hilo de mensajes (vacío o con respuesta)
  POST /messages/packs/{id}/...   → Acepta el mensaje (simula envío exitoso)
"""

import json
import random
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# ── Datos de prueba configurables ─────────────────────────────────────────────

SELLER_ID = "111222333"
SELLER_NICKNAME = "DIVINITTYS_STORE"

# Órdenes de prueba: editar para simular distintos escenarios
MOCK_ORDERS = {
    # Orden que debe activar el agente (custom + paid)
    "1000000001": {
        "id": 1000000001,
        "status": "paid",
        "pack_id": 9000000001,
        "seller": {"id": SELLER_ID},
        "buyer": {
            "id": "444555001",
            "nickname": "MARIA.GONZALEZ",
            "first_name": "María",
            "last_name": "González",
        },
        "shipping": {"mode": "custom", "shipping_mode": "custom"},
        "order_items": [{"item": {"title": "Sérum Vitamina C 30ml"}, "quantity": 1}],
        "date_created": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        "_mock_buyer_replied": False,   # Cambiar a True para simular respuesta
        "_mock_messages": [],
    },
    # Orden con Mercado Envíos (debe ser ignorada)
    "1000000002": {
        "id": 1000000002,
        "status": "paid",
        "pack_id": 9000000002,
        "seller": {"id": SELLER_ID},
        "buyer": {"id": "444555002", "nickname": "PEDRO.ROJAS"},
        "shipping": {"mode": "me2", "shipping_mode": "me2"},
        "order_items": [{"item": {"title": "Mascarilla Hidratante"}, "quantity": 2}],
        "date_created": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "_mock_buyer_replied": False,
        "_mock_messages": [],
    },
    # Orden aún no pagada (debe ser ignorada)
    "1000000003": {
        "id": 1000000003,
        "status": "confirmed",
        "pack_id": 9000000003,
        "seller": {"id": SELLER_ID},
        "buyer": {"id": "444555003", "nickname": "ANA.SILVA"},
        "shipping": {"mode": "custom"},
        "order_items": [{"item": {"title": "Kit Rizos Definidos"}, "quantity": 1}],
        "date_created": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        "_mock_buyer_replied": False,
        "_mock_messages": [],
    },
    # Orden custom+paid donde el comprador YA respondió
    "1000000004": {
        "id": 1000000004,
        "status": "paid",
        "pack_id": 9000000004,
        "seller": {"id": SELLER_ID},
        "buyer": {
            "id": "444555004",
            "nickname": "CAROLINA.VEGA",
            "first_name": "Carolina",
        },
        "shipping": {"mode": "custom", "shipping_mode": "custom"},
        "order_items": [{"item": {"title": "Aceite Argán 100ml"}, "quantity": 1}],
        "date_created": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        "_mock_buyer_replied": True,   # ← Ya respondió
        "_mock_messages": [
            {"from": {"role": "BUYER"}, "text": "Hola! Mis datos son: Carolina Vega, RUT 12.345.678-9, fono 9 1234 5678, Av. Providencia 123 depto 45, Providencia, RM"},
        ],
    },
    # Orden cancelada
    "1000000005": {
        "id": 1000000005,
        "status": "cancelled",
        "pack_id": 9000000005,
        "seller": {"id": SELLER_ID},
        "buyer": {"id": "444555005", "nickname": "LUIS.MORALES"},
        "shipping": {"mode": "custom"},
        "order_items": [{"item": {"title": "Shampoo Keratin"}, "quantity": 1}],
        "date_created": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
        "_mock_buyer_replied": False,
        "_mock_messages": [],
    },
}

# Tokens ficticios
MOCK_TOKEN = {
    "access_token": "TEST_ACCESS_TOKEN_DIVINITTYS_MOCK",
    "refresh_token": "TEST_REFRESH_TOKEN_DIVINITTYS_MOCK",
    "token_type": "bearer",
    "expires_in": 21600,
    "scope": "offline_access read write messages",
    "user_id": int(SELLER_ID),
}

# Estado mutable: mensajes enviados por el agente
sent_messages_log: list[dict] = []


# ── Handler HTTP ──────────────────────────────────────────────────────────────

class MockMeliHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f"  [MOCK ML] {self.path} → {args[1] if len(args)>1 else ''}")

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, indent=2, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # GET /users/me
        if path == "/users/me":
            self._send_json({
                "id": int(SELLER_ID),
                "nickname": SELLER_NICKNAME,
                "email": "rekkiem@gmail.com",
                "site_id": "MLC",
                "country_id": "CL",
            })

        # GET /orders/{id}
        elif path.startswith("/orders/") and "/search" not in path:
            order_id = path.split("/orders/")[1].rstrip("/")
            order = MOCK_ORDERS.get(order_id)
            if order:
                # Filtrar campos internos del mock antes de retornar
                clean = {k: v for k, v in order.items() if not k.startswith("_mock_")}
                self._send_json(clean)
            else:
                self._send_json({"message": "Order not found", "error": "not_found"}, 404)

        # GET /orders/search
        elif "/orders/search" in path:
            results = []
            for order in MOCK_ORDERS.values():
                shipping_mode = order.get("shipping", {}).get("mode", "")
                if shipping_mode == "custom" and order["status"] == "paid":
                    clean = {k: v for k, v in order.items() if not k.startswith("_mock_")}
                    results.append(clean)
            self._send_json({
                "results": results,
                "paging": {"total": len(results), "limit": 50, "offset": 0},
            })

        # GET /messages/packs/{pack_id}/sellers/{seller_id}
        elif "/messages/packs/" in path and "/sellers/" in path:
            pack_id = path.split("/messages/packs/")[1].split("/sellers/")[0]
            # Buscar la orden por pack_id
            order = next(
                (o for o in MOCK_ORDERS.values() if str(o.get("pack_id", "")) == pack_id),
                None,
            )
            messages = order.get("_mock_messages", []) if order else []
            self._send_json({"messages": messages, "paging": {"total": len(messages)}})

        else:
            self._send_json({"message": f"Endpoint no simulado: {path}"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()

        # POST /oauth/token
        if path == "/oauth/token":
            grant_type = body.get("grant_type", "")
            if grant_type in ("authorization_code", "refresh_token"):
                # Simular nuevo token en cada refresh
                token = dict(MOCK_TOKEN)
                token["access_token"] = f"TEST_ACCESS_{int(time.time())}"
                self._send_json(token)
            else:
                self._send_json({"error": "invalid_grant"}, 400)

        # POST /messages/packs/{pack_id}/sellers/{seller_id}
        elif "/messages/packs/" in path and "/sellers/" in path:
            pack_id = path.split("/messages/packs/")[1].split("/sellers/")[0]
            msg_id = f"mock_msg_{random.randint(10000, 99999)}"
            entry = {
                "id": msg_id,
                "pack_id": pack_id,
                "text": body.get("text", ""),
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
            sent_messages_log.append(entry)
            print(f"\n  ╔══════════════════════════════════════════╗")
            print(f"  ║ 📤 MENSAJE ENVIADO AL COMPRADOR          ║")
            print(f"  ║ Pack: {pack_id:<36}║")
            print(f"  ╚══════════════════════════════════════════╝")
            print(f"  Texto:\n{body.get('text','')[:300]}")
            print(f"  {'─'*44}")
            self._send_json({"id": msg_id, "status": "sent"}, 201)

        else:
            self._send_json({"message": f"Endpoint POST no simulado: {path}"}, 404)


def run(port: int = 9999):
    print(f"""
╔══════════════════════════════════════════════════════╗
║     Mock ML API Server — Divinittys Agent Debug     ║
╠══════════════════════════════════════════════════════╣
║  URL base: http://localhost:{port}                      ║
║                                                      ║
║  Órdenes de prueba disponibles:                      ║
║  1000000001 → ✅ Custom+Paid  (debe enviar mensaje)  ║
║  1000000002 → ⏭️  me2+Paid    (debe ignorar)          ║
║  1000000003 → ⏭️  Custom+Conf (debe ignorar)          ║
║  1000000004 → 💬 Buyer ya respondió                  ║
║  1000000005 → 🚫 Cancelada                           ║
╚══════════════════════════════════════════════════════╝
""")
    server = HTTPServer(("localhost", port), MockMeliHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n📋 Mensajes enviados durante la sesión:")
        for msg in sent_messages_log:
            print(f"  → Pack {msg['pack_id']} | ID: {msg['id']}")
        print("\nMock server detenido.")


if __name__ == "__main__":
    run()
