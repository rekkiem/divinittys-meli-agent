"""
admin_panel.py — Panel de administración web para Divinittys Agent.

Rutas:
  GET  /admin/dashboard          → Panel HTML en tiempo real (SSE)
  GET  /admin/api/stats          → JSON con métricas del agente
  GET  /admin/api/orders         → Lista paginada de órdenes procesadas
  GET  /admin/api/events         → Log de eventos recientes
  GET  /admin/api/events/stream  → Server-Sent Events (actualizaciones en vivo)

El panel es una SPA minimalista servida directamente por FastAPI.
No requiere build step ni Node.js — HTML/JS/CSS en un solo template.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AgentEvent, OAuthToken, ProcessedOrder, SentMessage

logger = logging.getLogger("divinittys.admin")

router = APIRouter(prefix="/admin", tags=["Admin Panel"])

# ─── API JSON ────────────────────────────────────────────────────────────────

@router.get("/api/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Métricas globales del agente."""
    # Token status
    token_result = await db.execute(select(OAuthToken).where(OAuthToken.id == 1))
    token_row = token_result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if token_row:
        seconds_left = (token_row.expires_at - now).total_seconds()
        token_status = {
            "status": "valid" if seconds_left > 0 else "expired",
            "expires_in_minutes": int(seconds_left / 60) if seconds_left > 0 else 0,
            "seller_id": token_row.seller_id,
        }
    else:
        token_status = {"status": "no_token"}

    # Conteos de órdenes
    total = await db.scalar(select(func.count()).select_from(ProcessedOrder))
    sent = await db.scalar(
        select(func.count()).select_from(ProcessedOrder)
        .where(ProcessedOrder.message_sent == True)
    )
    replied = await db.scalar(
        select(func.count()).select_from(ProcessedOrder)
        .where(ProcessedOrder.buyer_replied == True)
    )
    errors_count = await db.scalar(
        select(func.count()).select_from(ProcessedOrder)
        .where(ProcessedOrder.status == "error")
    )

    # Eventos de las últimas 24h
    since = now - timedelta(hours=24)
    events_24h = await db.scalar(
        select(func.count()).select_from(AgentEvent)
        .where(AgentEvent.created_at >= since)
    )
    errors_24h = await db.scalar(
        select(func.count()).select_from(AgentEvent)
        .where(AgentEvent.created_at >= since, AgentEvent.severity == "error")
    )

    return {
        "token": token_status,
        "orders": {
            "total": total or 0,
            "messages_sent": sent or 0,
            "buyers_replied": replied or 0,
            "reply_rate": f"{(replied / sent * 100):.1f}%" if sent else "0%",
            "errors": errors_count or 0,
        },
        "events_24h": {
            "total": events_24h or 0,
            "errors": errors_24h or 0,
        },
        "updated_at": now.isoformat(),
    }


@router.get("/api/orders")
async def get_orders(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Lista paginada de órdenes procesadas."""
    query = select(ProcessedOrder).order_by(desc(ProcessedOrder.processed_at))
    if status:
        query = query.where(ProcessedOrder.status == status)

    total = await db.scalar(
        select(func.count()).select_from(ProcessedOrder)
    )
    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    orders = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "orders": [
            {
                "order_id": o.order_id,
                "pack_id": o.pack_id,
                "status": o.status,
                "message_sent": o.message_sent,
                "buyer_replied": o.buyer_replied,
                "shipping_mode": o.shipping_mode,
                "skip_reason": o.skip_reason,
                "processed_at": o.processed_at.isoformat() if o.processed_at else None,
            }
            for o in orders
        ],
    }


@router.get("/api/events")
async def get_events(
    limit: int = Query(50, ge=1, le=200),
    severity: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Eventos recientes del agente."""
    query = select(AgentEvent).order_by(desc(AgentEvent.created_at)).limit(limit)
    if severity:
        query = query.where(AgentEvent.severity == severity)
    result = await db.execute(query)
    events = result.scalars().all()

    return {
        "events": [
            {
                "id": e.id,
                "type": e.event_type,
                "severity": e.severity,
                "order_id": e.order_id,
                "detail": e.detail,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ]
    }


# ─── Server-Sent Events (tiempo real) ────────────────────────────────────────

@router.get("/api/events/stream")
async def event_stream(db: AsyncSession = Depends(get_db)):
    """
    SSE stream: emite el último evento de la DB cada 5 segundos.
    El panel JS escucha este stream para auto-actualizar sin polling manual.
    """
    async def generator():
        last_id = 0
        while True:
            await asyncio.sleep(5)
            try:
                result = await db.execute(
                    select(AgentEvent)
                    .where(AgentEvent.id > last_id)
                    .order_by(AgentEvent.id.desc())
                    .limit(5)
                )
                new_events = result.scalars().all()
                if new_events:
                    last_id = new_events[0].id
                    data = [
                        {
                            "type": e.event_type,
                            "severity": e.severity,
                            "order_id": e.order_id,
                            "detail": e.detail,
                            "created_at": e.created_at.isoformat() if e.created_at else None,
                        }
                        for e in reversed(new_events)
                    ]
                    yield f"data: {json.dumps(data)}\n\n"
                else:
                    yield "data: []\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─── Panel HTML ───────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Divinittys — Agente ML</title>
<style>
  :root {
    --pink: #e91e8c; --pink-light: #fce4f3; --pink-dark: #b5156d;
    --green: #22c55e; --yellow: #f59e0b; --red: #ef4444; --blue: #3b82f6;
    --bg: #fdf4fb; --card: #ffffff; --border: #f0d6ec; --text: #2d1b2e;
    --muted: #9c7a9e; --radius: 12px; --shadow: 0 2px 12px rgba(233,30,140,.08);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); min-height: 100vh; }
  header { background: linear-gradient(135deg, var(--pink-dark), var(--pink));
           color: white; padding: 20px 32px; display: flex; align-items: center;
           gap: 16px; box-shadow: 0 4px 20px rgba(233,30,140,.3); }
  header h1 { font-size: 1.5rem; font-weight: 700; letter-spacing: -.5px; }
  header p { font-size: .85rem; opacity: .85; }
  .badge { background: rgba(255,255,255,.2); padding: 3px 10px; border-radius: 20px;
           font-size: .75rem; font-weight: 600; margin-left: auto; }
  .badge.live { animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }

  main { max-width: 1200px; margin: 0 auto; padding: 32px 24px; }
  h2 { font-size: 1rem; font-weight: 600; color: var(--muted);
       text-transform: uppercase; letter-spacing: .5px; margin-bottom: 16px; }

  .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 16px; margin-bottom: 40px; }
  .stat-card { background: var(--card); border: 1px solid var(--border);
               border-radius: var(--radius); padding: 20px 24px;
               box-shadow: var(--shadow); }
  .stat-card .label { font-size: .78rem; font-weight: 600; color: var(--muted);
                      text-transform: uppercase; letter-spacing: .5px; }
  .stat-card .value { font-size: 2.2rem; font-weight: 800; color: var(--pink);
                      margin: 8px 0 4px; line-height: 1; }
  .stat-card .sub { font-size: .8rem; color: var(--muted); }
  .stat-card.ok .value { color: var(--green); }
  .stat-card.warn .value { color: var(--yellow); }
  .stat-card.err .value { color: var(--red); }

  .token-bar { display: flex; align-items: center; gap: 12px; background: var(--card);
               border: 1px solid var(--border); border-radius: var(--radius);
               padding: 16px 24px; margin-bottom: 40px; box-shadow: var(--shadow); }
  .token-bar .dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .token-bar .dot.valid { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .token-bar .dot.expired { background: var(--red); box-shadow: 0 0 6px var(--red); }
  .token-bar .dot.no_token { background: var(--yellow); }
  .token-bar strong { font-weight: 700; }
  .token-bar .exp { margin-left: auto; font-size: .85rem; color: var(--muted); }

  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
  @media(max-width:768px) { .grid-2 { grid-template-columns: 1fr; } }

  .panel { background: var(--card); border: 1px solid var(--border);
           border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden; }
  .panel-header { padding: 16px 24px; border-bottom: 1px solid var(--border);
                  display: flex; justify-content: space-between; align-items: center; }
  .panel-header h3 { font-size: .95rem; font-weight: 700; }
  .panel-body { max-height: 420px; overflow-y: auto; }

  table { width: 100%; border-collapse: collapse; font-size: .85rem; }
  th { text-align: left; padding: 10px 16px; font-size: .75rem; font-weight: 700;
       color: var(--muted); text-transform: uppercase; letter-spacing: .4px;
       border-bottom: 1px solid var(--border); background: #fdf4fb; }
  td { padding: 11px 16px; border-bottom: 1px solid #f8edf6; vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #fdf4fb; }

  .chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px;
          border-radius: 20px; font-size: .72rem; font-weight: 700; }
  .chip.sent     { background:#dcfce7; color:#15803d; }
  .chip.replied  { background:#dbeafe; color:#1d4ed8; }
  .chip.error    { background:#fee2e2; color:#b91c1c; }
  .chip.skipped  { background:#f1f5f9; color:#64748b; }
  .chip.pending  { background:#fef9c3; color:#92400e; }
  .chip.info     { background:#f0fdf4; color:#166534; }
  .chip.warning  { background:#fffbeb; color:#92400e; }

  .event-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; display: inline-block; }
  .event-dot.info    { background: var(--blue); }
  .event-dot.warning { background: var(--yellow); }
  .event-dot.error   { background: var(--red); animation: pulse 1.5s infinite; }

  .filter-bar { display: flex; gap: 8px; }
  .filter-bar button { padding: 4px 12px; border: 1px solid var(--border); border-radius: 20px;
                       font-size: .78rem; cursor: pointer; background: white; color: var(--muted);
                       font-weight: 600; transition: all .15s; }
  .filter-bar button.active { background: var(--pink); color: white; border-color: var(--pink); }

  .refresh-btn { padding: 6px 14px; background: var(--pink); color: white; border: none;
                 border-radius: 8px; font-size: .8rem; font-weight: 700; cursor: pointer;
                 transition: background .15s; }
  .refresh-btn:hover { background: var(--pink-dark); }
  .ts { font-size: .72rem; color: var(--muted); white-space: nowrap; }
  .mono { font-family: monospace; font-size: .8rem; }
  #last-updated { font-size: .75rem; color: var(--muted); }
</style>
</head>
<body>
<header>
  <div>
    <h1>🌸 Divinittys — Agente ML</h1>
    <p>Panel de Post-Venta • Mercado Libre Chile</p>
  </div>
  <span class="badge live" id="live-badge">● EN VIVO</span>
</header>

<main>
  <!-- Token Status -->
  <div class="token-bar" id="token-bar">
    <span class="dot" id="token-dot"></span>
    <span id="token-text">Cargando estado del token...</span>
    <span class="exp" id="token-exp"></span>
  </div>

  <!-- Stats -->
  <h2>Métricas Globales</h2>
  <div class="stats-grid">
    <div class="stat-card"><div class="label">Total Órdenes</div>
      <div class="value" id="stat-total">—</div><div class="sub">procesadas por el agente</div></div>
    <div class="stat-card ok"><div class="label">Mensajes Enviados</div>
      <div class="value" id="stat-sent">—</div><div class="sub">solicitudes de datos</div></div>
    <div class="stat-card" style="--pink:#3b82f6"><div class="label">Compradores Respondieron</div>
      <div class="value" id="stat-replied" style="color:#3b82f6">—</div>
      <div class="sub" id="stat-rate">tasa de respuesta</div></div>
    <div class="stat-card err"><div class="label">Errores</div>
      <div class="value" id="stat-errors">—</div><div class="sub">últimas 24h</div></div>
  </div>

  <!-- Orders + Events -->
  <div class="grid-2">
    <!-- Orders Table -->
    <div class="panel">
      <div class="panel-header">
        <h3>📦 Órdenes Recientes</h3>
        <div class="filter-bar" id="order-filters">
          <button class="active" data-status="">Todas</button>
          <button data-status="message_sent">Enviadas</button>
          <button data-status="replied">Respondidas</button>
          <button data-status="error">Error</button>
        </div>
      </div>
      <div class="panel-body">
        <table>
          <thead><tr>
            <th>Orden</th><th>Estado</th><th>Procesada</th>
          </tr></thead>
          <tbody id="orders-body"><tr><td colspan="3" style="text-align:center;color:var(--muted);padding:24px">Cargando...</td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- Events Log -->
    <div class="panel">
      <div class="panel-header">
        <h3>📋 Log de Eventos</h3>
        <span id="last-updated">—</span>
      </div>
      <div class="panel-body">
        <table>
          <thead><tr><th></th><th>Evento</th><th>Orden</th><th>Hora</th></tr></thead>
          <tbody id="events-body"><tr><td colspan="4" style="text-align:center;color:var(--muted);padding:24px">Cargando...</td></tr></tbody>
        </table>
      </div>
    </div>
  </div>
</main>

<script>
const STATUS_CHIPS = {
  message_sent: '<span class="chip sent">✓ Enviado</span>',
  replied:      '<span class="chip replied">💬 Respondido</span>',
  error:        '<span class="chip error">✗ Error</span>',
  skipped:      '<span class="chip skipped">– Ignorado</span>',
  pending:      '<span class="chip pending">⋯ Pendiente</span>',
};
const SEV_COLOR = { info: 'info', warning: 'warning', error: 'error' };
const EVT_LABELS = {
  message_sent: '📤 Mensaje enviado',
  buyer_replied: '💬 Buyer respondió',
  token_refreshed: '🔑 Token renovado',
  api_error: '❌ Error API',
  polling_run: '🔄 Ciclo polling',
  followup_sent: '🔔 Follow-up enviado',
  followup_run: '🔔 Ciclo follow-up',
  escalation_alert: '🚨 Escalación',
  order_skipped: '⏭ Orden ignorada',
};

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleTimeString('es-CL', {hour:'2-digit', minute:'2-digit'}) +
         ' ' + d.toLocaleDateString('es-CL', {day:'2-digit', month:'2-digit'});
}

// ── Stats ──────────────────────────────────────────────────────────────────
async function loadStats() {
  const r = await fetch('/admin/api/stats');
  const d = await r.json();

  // Token bar
  const tok = d.token;
  const dot = document.getElementById('token-dot');
  dot.className = 'dot ' + tok.status;
  document.getElementById('token-text').innerHTML =
    tok.status === 'valid'
      ? `<strong>Token OAuth:</strong> Válido — Seller ID: ${tok.seller_id}`
      : tok.status === 'expired'
        ? `<strong>Token OAuth:</strong> ⚠️ EXPIRADO — Visita <code>/auth/login</code>`
        : `<strong>Token OAuth:</strong> No configurado — Visita <code>/auth/login</code>`;
  document.getElementById('token-exp').textContent =
    tok.expires_in_minutes ? `Expira en ${tok.expires_in_minutes} min` : '';

  // Stats cards
  const o = d.orders;
  document.getElementById('stat-total').textContent = o.total;
  document.getElementById('stat-sent').textContent = o.messages_sent;
  document.getElementById('stat-replied').textContent = o.buyers_replied;
  document.getElementById('stat-rate').textContent = o.reply_rate + ' de respuesta';
  document.getElementById('stat-errors').textContent = d.events_24h.errors;
}

// ── Orders ─────────────────────────────────────────────────────────────────
let currentStatus = '';
async function loadOrders(status = '') {
  currentStatus = status;
  const url = status ? `/admin/api/orders?status=${status}&per_page=30` : '/admin/api/orders?per_page=30';
  const r = await fetch(url);
  const d = await r.json();
  const tbody = document.getElementById('orders-body');
  if (!d.orders.length) {
    tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--muted);padding:24px">Sin órdenes</td></tr>';
    return;
  }
  tbody.innerHTML = d.orders.map(o => `
    <tr>
      <td><span class="mono">${o.order_id}</span>
          ${o.buyer_replied ? '<br><span style="font-size:.72rem;color:#3b82f6">↩ Respondió</span>' : ''}</td>
      <td>${STATUS_CHIPS[o.status] || o.status}</td>
      <td class="ts">${fmtTime(o.processed_at)}</td>
    </tr>
  `).join('');
}

document.getElementById('order-filters').addEventListener('click', e => {
  const btn = e.target.closest('button');
  if (!btn) return;
  document.querySelectorAll('#order-filters button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadOrders(btn.dataset.status);
});

// ── Events ─────────────────────────────────────────────────────────────────
async function loadEvents() {
  const r = await fetch('/admin/api/events?limit=50');
  const d = await r.json();
  renderEvents(d.events);
}

function renderEvents(events) {
  const tbody = document.getElementById('events-body');
  if (!events.length) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:24px">Sin eventos</td></tr>';
    return;
  }
  tbody.innerHTML = events.map(e => `
    <tr>
      <td><span class="event-dot ${e.severity}"></span></td>
      <td>${EVT_LABELS[e.type] || e.type}
          ${e.detail ? `<br><span style="font-size:.72rem;color:var(--muted)">${e.detail.slice(0,60)}</span>` : ''}</td>
      <td class="mono">${e.order_id ? e.order_id.slice(-8) : '—'}</td>
      <td class="ts">${fmtTime(e.created_at)}</td>
    </tr>
  `).join('');
  document.getElementById('last-updated').textContent = 'Actualizado ' + new Date().toLocaleTimeString('es-CL');
}

// ── SSE (tiempo real) ──────────────────────────────────────────────────────
function startSSE() {
  const es = new EventSource('/admin/api/events/stream');
  es.onmessage = e => {
    const data = JSON.parse(e.data);
    if (data.length > 0) {
      loadStats();
      loadOrders(currentStatus);
      loadEvents();
    }
  };
  es.onerror = () => {
    document.getElementById('live-badge').textContent = '○ RECONECTANDO';
    setTimeout(() => { es.close(); startSSE(); }, 5000);
  };
}

// ── Init ───────────────────────────────────────────────────────────────────
loadStats();
loadOrders();
loadEvents();
startSSE();
setInterval(() => { loadStats(); loadOrders(currentStatus); }, 30000);
</script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Panel de administración web — interfaz visual del agente."""
    return DASHBOARD_HTML
