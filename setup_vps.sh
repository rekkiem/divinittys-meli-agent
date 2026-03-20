#!/usr/bin/env bash
# setup_vps.sh — Script de configuración inicial del VPS para producción.
#
# Ejecutar UNA SOLA VEZ como root en un VPS Ubuntu 22.04 / 24.04 nuevo:
#   chmod +x setup_vps.sh && sudo bash setup_vps.sh
#
# Configura:
#   ✅ Docker + Docker Compose
#   ✅ Nginx + Certbot (Let's Encrypt SSL)
#   ✅ Firewall UFW (solo 80, 443, SSH)
#   ✅ Usuario no-root para el agente
#   ✅ Directorio de trabajo con permisos correctos
#   ✅ Systemd watchdog (reinicia Docker si se cae)

set -euo pipefail

# ── Variables — editar antes de ejecutar ──────────────────────────────────────
DOMAIN="tu-dominio.com"          # ← Cambiar a tu dominio real
EMAIL="rekkiem@gmail.com"         # ← Cambiar al email del vendedor
APP_DIR="/opt/divinittys-meli-agent"
APP_USER="divinittys"

echo "╔══════════════════════════════════════════════════════╗"
echo "║   Divinittys Meli Agent — Setup de Producción       ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. Actualizar sistema ─────────────────────────────────────────────────────
echo "📦 Actualizando sistema..."
apt-get update -qq && apt-get upgrade -y -qq

# ── 2. Instalar dependencias del sistema ──────────────────────────────────────
echo "🔧 Instalando dependencias..."
apt-get install -y -qq \
  curl wget git ufw fail2ban \
  nginx certbot python3-certbot-nginx \
  ca-certificates gnupg lsb-release

# ── 3. Instalar Docker ────────────────────────────────────────────────────────
echo "🐳 Instalando Docker..."
if ! command -v docker &> /dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    | tee /etc/apt/sources.list.d/docker.list > /dev/null
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

# ── 4. Crear usuario dedicado ─────────────────────────────────────────────────
echo "👤 Creando usuario '$APP_USER'..."
if ! id "$APP_USER" &>/dev/null; then
  useradd -m -s /bin/bash -G docker "$APP_USER"
fi

# ── 5. Directorio de la app ───────────────────────────────────────────────────
echo "📁 Configurando directorio de trabajo..."
mkdir -p "$APP_DIR/data" "$APP_DIR/ssl"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo ""
echo "   Copia el proyecto a: $APP_DIR"
echo "   Ejemplo: scp -r ./divinittys-meli-agent/* $APP_USER@$DOMAIN:$APP_DIR/"
echo ""

# ── 6. Firewall ───────────────────────────────────────────────────────────────
echo "🔒 Configurando UFW (firewall)..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp      # HTTP (redirect a HTTPS)
ufw allow 443/tcp     # HTTPS
# Puerto 8000 solo accesible internamente (Nginx hace proxy)
# NO abrir 8000 al público
ufw --force enable

# ── 7. Fail2ban (protección contra brute-force) ───────────────────────────────
echo "🛡️ Configurando Fail2ban..."
cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime  = 3600
findtime  = 600
maxretry = 5

[sshd]
enabled = true
port = ssh

[nginx-http-auth]
enabled = true
EOF
systemctl enable --now fail2ban

# ── 8. SSL con Let's Encrypt ──────────────────────────────────────────────────
echo "🔐 Obteniendo certificado SSL para $DOMAIN..."
echo "   (el DNS de $DOMAIN debe apuntar a este servidor)"
echo ""

# Detener nginx si está corriendo para el challenge standalone
systemctl stop nginx 2>/dev/null || true

certbot certonly \
  --standalone \
  --non-interactive \
  --agree-tos \
  --email "$EMAIL" \
  -d "$DOMAIN" \
  || echo "⚠️  Certbot falló. Configura el DNS y ejecuta manualmente: certbot --nginx -d $DOMAIN"

# Copiar certs al directorio de la app
if [ -d "/etc/letsencrypt/live/$DOMAIN" ]; then
  cp /etc/letsencrypt/live/$DOMAIN/fullchain.pem "$APP_DIR/ssl/"
  cp /etc/letsencrypt/live/$DOMAIN/privkey.pem "$APP_DIR/ssl/"
  chown -R "$APP_USER:$APP_USER" "$APP_DIR/ssl"
  echo "✅ Certificados copiados a $APP_DIR/ssl/"
fi

# Auto-renovación mensual
echo "0 3 1 * * root certbot renew --quiet && cp /etc/letsencrypt/live/$DOMAIN/*.pem $APP_DIR/ssl/ && docker compose -f $APP_DIR/docker-compose.yml restart nginx" \
  > /etc/cron.d/certbot-divinittys

# ── 9. Systemd service (watchdog) ────────────────────────────────────────────
echo "⚙️  Configurando servicio systemd..."
cat > /etc/systemd/system/divinittys-agent.service << EOF
[Unit]
Description=Divinittys Meli Agent
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/docker compose --profile prod up -d
ExecStop=/usr/bin/docker compose down
ExecReload=/usr/bin/docker compose pull && /usr/bin/docker compose --profile prod up -d
User=$APP_USER
Group=$APP_USER
TimeoutStartSec=120
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable divinittys-agent.service

# ── 10. Resumen final ─────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║            Setup completado exitosamente             ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Próximos pasos:"
echo ""
echo "  1. Copiar proyecto al servidor:"
echo "     scp -r ./divinittys-meli-agent/* $APP_USER@$DOMAIN:$APP_DIR/"
echo ""
echo "  2. Configurar variables en $APP_DIR/.env"
echo "     (copiar desde .env.example y completar)"
echo ""
echo "  3. Levantar el agente:"
echo "     cd $APP_DIR && docker compose --profile prod up -d"
echo ""
echo "  4. Autorizar OAuth (solo la primera vez):"
echo "     Visita: https://$DOMAIN/auth/login"
echo ""
echo "  5. Verificar que todo está OK:"
echo "     curl https://$DOMAIN/health"
echo "     Visita: https://$DOMAIN/admin/dashboard"
echo ""
echo "  6. Configurar webhook en ML Developer Portal:"
echo "     URL: https://$DOMAIN/webhooks/meli"
echo "     Topic: orders_v2"
