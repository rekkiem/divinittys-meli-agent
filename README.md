# 🌸 Divinittys — Agente de Post-Venta Mercado Libre

Agente automático que detecta ventas con envío "Acordado con el vendedor"
y envía un mensaje al comprador solicitando sus datos y el pago del flete.

---

## 📐 Arquitectura

```
                          ┌──────────────────────────────────────────┐
                          │           MERCADO LIBRE API              │
                          │  (Chile: api.mercadolibre.com)           │
                          └─────────┬────────────────────────────────┘
                                    │
              ┌─────────────────────▼────────────────────┐
              │          CANAL DE DETECCIÓN               │
              │                                          │
              │  🥇 WEBHOOKS (instantáneo)               │
              │     ML notifica al agente en <1 seg      │
              │     Endpoint: POST /webhooks/meli        │
              │                                          │
              │  🥈 POLLING (fallback cada 5 min)        │
              │     APScheduler busca órdenes recientes  │
              │     Cubre caídas del servidor/red        │
              └─────────────────┬────────────────────────┘
                                │ order_id
                                ▼
              ┌─────────────────────────────────────────────┐
              │              AGENTE FASTAPI                  │
              │                                              │
              │  1. Obtener orden (GET /orders/{id})        │
              │  2. ¿shipping.mode == 'custom'?             │
              │  3. ¿order.status == 'paid'?                │
              │  4. ¿Ya procesada? (idempotencia en DB)     │
              │  5. ¿Buyer ya respondió? (leer mensajes)    │
              │  6. Enviar mensaje con datos bancarios      │
              └─────────────┬──────────────────────────────┘
                            │
               ┌────────────▼──────────────┐
               │         SALIDAS           │
               │                          │
               │  📤 Mensaje → Buyer ML   │
               │  💾 Registro en DB       │
               │  📱 Notif → Telegram     │
               └───────────────────────────┘
```

### ¿Webhooks o Polling?

| Criterio         | Webhooks                     | Polling                     |
|-----------------|------------------------------|-----------------------------|
| Velocidad        | ⚡ Instantáneo (<1 seg)      | ⏱ Máx 5 min de demora      |
| Complejidad      | Requiere HTTPS + IP pública  | Solo necesita correr        |
| Recomendado      | ✅ **Sí, para producción**   | ✅ Como fallback             |
| Este agente      | Ambos activos simultáneamente |                            |

---

## 🚀 Setup Paso a Paso

### PASO 1 — Crear la App en el Portal de Desarrolladores de ML

1. Ve a **https://developers.mercadolibre.cl/apps**
2. Haz clic en **"Crear aplicación"**
3. Completa:
   - **Nombre**: `Divinittys Post-Sale Agent`
   - **Descripción corta**: Agente de post-venta para gestión de envíos
   - **Redirect URI**: `https://tu-dominio.com/auth/callback`
4. En **Permisos (Scopes)**, activa EXACTAMENTE estos:
   ```
   ✅ offline_access    → permite el refresh_token automático
   ✅ read              → leer órdenes y mensajes
   ✅ write             → enviar mensajes al comprador
   ✅ messages          → acceso a la mensajería de órdenes
   ```
5. En **Notifications (Webhooks)**:
   - URL: `https://tu-dominio.com/webhooks/meli`
   - Topic: `orders_v2`
6. Guarda el **App ID** y el **Secret Key**

### PASO 2 — Configurar el Servidor

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-repo/divinittys-meli-agent.git
cd divinittys-meli-agent

# 2. Copiar y editar el .env
cp .env.example .env
nano .env  # o vim .env

# 3. Rellenar MELI_CLIENT_ID, MELI_CLIENT_SECRET, datos bancarios, Telegram, etc.
```

### PASO 3 — Levantar con Docker

**Desarrollo (SQLite, sin Nginx):**
```bash
docker compose up --build agente
```

**Producción (PostgreSQL + Nginx + SSL):**
```bash
# Primero obtener certificado SSL con Let's Encrypt:
# sudo certbot certonly --standalone -d tu-dominio.com
# Copiar certs a ./ssl/

docker compose --profile prod up -d --build
```

### PASO 4 — Primera Autorización OAuth (solo una vez)

```bash
# Abre en el navegador:
https://tu-dominio.com/auth/login
```

Esto te redirige a Mercado Libre. Acepta los permisos y serás redirigido a
`/auth/callback`. El agente guardará el token en la DB y ya está listo.

✅ **No tendrás que repetir este paso.** El refresh es automático.

### PASO 5 — Configurar Bot de Telegram (opcional pero recomendado)

1. Habla con `@BotFather` en Telegram → `/newbot`
2. Copia el token al `.env` como `TELEGRAM_BOT_TOKEN`
3. Envíale un mensaje al bot, luego visita:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Copia el `chat_id` al `.env` como `TELEGRAM_CHAT_ID`

---

## 🧪 Testing

### Test Manual de una Orden

```bash
# Fuerza el procesamiento de una orden específica (bypasea idempotencia)
curl -X POST https://tu-dominio.com/admin/process-order/TU_ORDER_ID
```

### Verificar Estado del Token

```bash
curl https://tu-dominio.com/admin/token-status
# Respuesta esperada:
# {"status":"valid","seller_id":"...","expires_at":"...","seconds_until_expiry":18432}
```

### Simular Webhook de ML

```bash
curl -X POST https://tu-dominio.com/webhooks/meli \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "orders_v2",
    "resource": "/orders/TU_ORDER_ID",
    "user_id": TU_SELLER_ID,
    "application_id": TU_APP_ID
  }'
```

### Health Check

```bash
curl https://tu-dominio.com/health
# {"status":"ok","store":"Divinittys","vendor":"..."}
```

---

## 📊 Protocolo de Feedback — ¿Cómo saber qué pasa?

| Evento                     | Notificación                          | DB                         |
|---------------------------|---------------------------------------|----------------------------|
| Mensaje enviado al buyer   | ✅ Telegram: "Mensaje enviado"        | `status=message_sent`      |
| Buyer respondió            | 🎉 Telegram: "¡Respuesta recibida!"  | `status=replied`           |
| Error de API de ML         | ⚠️ Telegram: "Error en API"          | `agent_events.severity=error` |
| Token renovado             | 🔑 Telegram (silencioso en log)       | `oauth_tokens.expires_at`  |
| Token expirado sin renovar | 🚨 Telegram: "ERROR CRÍTICO"         | —                          |
| Orden ignorada (no custom) | Solo log en consola                   | `status=skipped`           |

### Ver logs en tiempo real

```bash
docker logs -f divinittys-meli-agent
```

---

## 🔐 Seguridad Anti-Ban de ML

El agente sigue estas reglas estrictamente:

1. **Sin links externos** en los mensajes al comprador
2. **Datos bancarios en texto plano** (no en URLs ni imágenes con links)
3. **Mensajería oficial** de ML (endpoint `/messages/packs/`)
4. **1 solo mensaje** por orden (idempotencia garantizada por DB)
5. **Tono natural**, no robótico ni spam

---

## 📁 Estructura del Proyecto

```
divinittys-meli-agent/
├── app/
│   ├── main.py              # FastAPI: webhooks, OAuth routes
│   ├── config.py            # Settings (pydantic-settings)
│   ├── database.py          # SQLAlchemy async engine
│   ├── models.py            # Tablas: tokens, órdenes, mensajes, eventos
│   ├── meli_client.py       # Cliente API ML + auto refresh token
│   ├── agent.py             # Lógica central del agente
│   ├── message_templates.py # Mensajes al comprador (tono Divinittys)
│   ├── notifications.py     # Alertas Telegram al vendedor
│   └── scheduler.py        # Polling + detección de respuestas
├── Dockerfile
├── docker-compose.yml
├── nginx.conf
├── requirements.txt
├── .env.example
└── README.md
```

---

## 🔄 Ciclo de Vida del Token OAuth

```
Vendedor visita /auth/login
        ↓
    ML → code
        ↓
/auth/callback → exchange code → access_token + refresh_token
        ↓
    Guardados en DB (tabla oauth_tokens)
        ↓
    Cada request → ¿expira en < 10 min?
        ├── No → usar access_token actual
        └── Sí → POST /oauth/token (grant: refresh_token)
                   ↓
               Nuevo access_token + refresh_token
                   ↓
               Actualizar en DB → continuar
```

Los tokens de ML expiran cada **6 horas**. El agente los renueva automáticamente.
