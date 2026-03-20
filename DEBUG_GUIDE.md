# 🧪 Guía de Debug Local — Divinittys Meli Agent

Sin credenciales reales de Mercado Libre. Sin tocar tu cuenta de ML.
Todo 100% local con el mock server incluido.

---

## Requisitos previos

```bash
python --version   # Necesitas Python 3.11+
docker --version   # Para el flujo Docker (opcional)
```

---

## ⚡ Setup en 3 minutos

```bash
# 1. Entrar al proyecto
cd divinittys-meli-agent

# 2. Crear entorno virtual
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

# 3. Instalar dependencias
pip install -r requirements-test.txt

# 4. Crear directorio de datos
mkdir -p data

# 5. Activar configuración de debug
cp .env.debug .env
```

---

## Flujo de debug completo (4 terminales)

Abre **4 terminales** en el directorio del proyecto:

---

### 🖥️ Terminal 1 — Mock Server (simula la API de ML)

```bash
source .venv/bin/activate
python tests/mock_meli_server.py
```

Verás:
```
╔══════════════════════════════════════════════════════╗
║     Mock ML API Server — Divinittys Agent Debug     ║
║  URL base: http://localhost:9999                    ║
║  Órdenes de prueba disponibles:                     ║
║  1000000001 → ✅ Custom+Paid  (debe enviar mensaje) ║
║  1000000002 → ⏭️  me2+Paid    (debe ignorar)        ║
║  ...                                                ║
╚══════════════════════════════════════════════════════╝
```

**Deja esta terminal corriendo.** Es tu API falsa de Mercado Libre.

---

### 🚀 Terminal 2 — El Agente

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000 --log-level info
```

Verás en el arranque:
```
INFO  Iniciando DB...
INFO  Polling job: cada 1 minutos
INFO  Follow-up job: cada 1 hora
INFO  ✅ Divinittys Meli Agent iniciado
INFO  Uvicorn running on http://127.0.0.1:8000
```

---

### 🔧 Terminal 3 — Debug CLI (tu consola de control)

```bash
source .venv/bin/activate
python tests/debug_cli.py
```

Verás el prompt interactivo:
```
╔══════════════════════════════════════════════════════╗
║     Divinittys Agent — Debug CLI                    ║
╚══════════════════════════════════════════════════════╝

🔧 debug>
```

---

### 📋 Terminal 4 — Logs en tiempo real

```bash
# Sigue los logs del agente en tiempo real
tail -f /dev/null   # (los logs salen en Terminal 2 con --reload)
# O bien filtra solo los de Divinittys:
# uvicorn app.main:app --reload 2>&1 | grep -E "(divinittys|ERROR|WARNING)"
```

---

## 🎯 Escenarios de prueba paso a paso

### Escenario 1 — Flujo completo (orden nueva → mensaje enviado)

**En Terminal 3 (Debug CLI):**

```
🔧 debug> process 1000000001
```

**Resultado esperado:**
```
  ⚙️  Procesando orden 1000000001 (force=True)...
  ✅ Resultado: {'status': 'message_sent', 'order_id': '1000000001',
                 'buyer': 'MARIA.GONZALEZ', 'meli_message_id': 'mock_msg_...'}
```

**En Terminal 1 (Mock Server) verás:**
```
  ╔══════════════════════════════════════════╗
  ║ 📤 MENSAJE ENVIADO AL COMPRADOR          ║
  ║ Pack: 9000000001                         ║
  ╚══════════════════════════════════════════╝
  Texto:
  ¡Hola, MARIA! 💖 Soy del equipo de DIVINITTYS...
```

**Verificar en CLI:**
```
🔧 debug> orders
🔧 debug> messages 1000000001
```

---

### Escenario 2 — Órdenes que deben ignorarse

```
🔧 debug> process 1000000002
```
Resultado esperado: `{'status': 'skipped', 'reason': 'shipping_mode=me2'}`

```
🔧 debug> process 1000000003
```
Resultado esperado: `{'status': 'skipped', 'reason': 'status=confirmed'}`

---

### Escenario 3 — Comprador que ya respondió

```
🔧 debug> process 1000000004
```
Resultado esperado: `{'status': 'buyer_replied'}`

El agente detecta que hay un mensaje de BUYER en el hilo → notifica al vendedor
en lugar de enviar el mensaje de solicitud de datos.

---

### Escenario 4 — Webhook simulado (como si llegara desde ML)

Asegúrate de que **Terminal 2 (agente)** esté corriendo.

```
🔧 debug> webhook 1000000001
```

Resultado esperado:
```
  📡 Enviando webhook simulado para orden 1000000001...
  Respuesta HTTP 200: {'status': 'accepted'}
```

En Terminal 2 verás:
```
INFO  📩 Webhook recibido: topic=orders_v2 resource=/orders/1000000001
INFO  📤 Mensaje enviado a orden 1000000001. ML message_id=mock_msg_...
```

---

### Escenario 5 — Idempotencia (no enviar dos veces)

```
🔧 debug> process 1000000001       # ← primer procesamiento
🔧 debug> process 1000000001       # ← segundo intento (sin force, en código real)
```

Para probar la idempotencia correctamente, edita temporalmente en `agent.py`:
```python
result = await agent.process_order(order_id, force=False)  # ← sin force
```

---

### Escenario 6 — Orden cancelada

```
🔧 debug> process 1000000005
```
Resultado esperado: `{'status': 'skipped', 'reason': 'status=cancelled'}`
O si llega por webhook: el `cancellation_handler` lo marca como cancelado.

---

### Escenario 7 — Preview del mensaje al comprador

```
🔧 debug> message-preview
```

Verás exactamente cómo quedará el mensaje antes de enviarlo:
```
  │ ¡Hola, María! 💖 Soy del equipo de DIVINITTYS...
  │
  │ ━━━━━━━━━━━━━━━━━━━━━━━━
  │ 📦 PASO 1 — TUS DATOS DE ENVÍO
  │ ━━━━━━━━━━━━━━━━━━━━━━━━
  │   • Nombre completo:
  │   • RUT:
  │   ...
```

---

### Escenario 8 — Ver el panel web

Abre en tu navegador:
```
http://localhost:8000/admin/dashboard
```
Usuario: `admin` | Contraseña: `debug1234` (del `.env.debug`)

Verás las órdenes procesadas, el log de eventos y el estado del token en tiempo real.

---

### Escenario 9 — Ejecutar la suite de tests

```bash
# En cualquier terminal con el venv activo:
pytest tests/test_agent.py -v

# Con reporte de cobertura:
pytest tests/test_agent.py -v --cov=app --cov-report=term-missing
```

Todos los tests deben pasar sin necesitar el mock server activo (usan mocks en memoria).

---

## 🔍 Qué observar en cada terminal

### Terminal 1 (Mock Server) — Qué requests hace el agente
```
[MOCK ML] /orders/1000000001 → 200
[MOCK ML] /messages/packs/9000000001/sellers/111222333 → 200  (leer mensajes)
[MOCK ML] /messages/packs/9000000001/sellers/111222333 → 201  (enviar mensaje)
```

### Terminal 2 (Agente) — Flujo interno completo
```
INFO  divinittys.agent: 🔍 Procesando orden: 1000000001
INFO  divinittys.agent: 📤 Mensaje enviado a orden 1000000001
INFO  divinittys.meli_client: 🔑 Token persistido. Expira: ...
```

### Terminal 3 (Debug CLI) — Estado de la DB
```
🔧 debug> events 5
  14:32:01 ℹ️  token_refreshed
  14:32:01 ℹ️  message_sent [1000000001] — Buyer: MARIA.GONZALEZ | Pack: ...
```

---

## 🐛 Problemas comunes y soluciones

### ❌ "No hay token OAuth en la base de datos"

```bash
# El token de debug se inyecta automáticamente al iniciar el CLI
# Si usas curl directo, primero llama al endpoint de debug:
curl -X GET http://localhost:8000/auth/callback?code=debug_code_123
# O usa el CLI: python tests/debug_cli.py (lo inyecta solo)
```

### ❌ "Connection refused" al hacer webhook

```bash
# El agente no está corriendo. En Terminal 2:
uvicorn app.main:app --reload --port 8000
```

### ❌ "Module not found"

```bash
# Asegúrate de estar en el directorio raíz del proyecto:
cd divinittys-meli-agent
# Y con el venv activo:
source .venv/bin/activate
```

### ❌ El polling no corre en debug

```bash
# En .env.debug el polling está en 1 minuto.
# Espera 60 segundos o forza manualmente desde el CLI:
🔧 debug> process 1000000001
```

### ❌ El mock server retorna 404

```bash
# Verifica que MELI_API_BASE apunta al mock:
grep MELI_API_BASE .env
# Debe decir: MELI_API_BASE=http://localhost:9999
```

### ❌ Quiero re-testear una orden ya procesada

```
🔧 debug> reset 1000000001
🔧 debug> process 1000000001
```

---

## 🚀 Cuando estés listo para producción

1. Reemplaza `.env.debug` por `.env` con credenciales reales de ML
2. Elimina `MELI_API_BASE` del `.env` (usa el default: `https://api.mercadolibre.com`)
3. Ejecuta `bash setup_vps.sh` en tu servidor
4. Visita `https://tu-dominio.com/auth/login` para el OAuth real
5. Configura el webhook en el portal de desarrolladores de ML
