# SECRETS.md — Variables secretas requeridas en GitHub Actions

## Configurar en: Settings → Secrets and variables → Actions

### Secrets de Producción (requeridos para el deploy automático)

| Secret | Valor | Descripción |
|--------|-------|-------------|
| `VPS_HOST` | `200.x.x.x` | IP pública del VPS |
| `VPS_USER` | `divinittys` | Usuario SSH del servidor |
| `VPS_SSH_KEY` | `-----BEGIN...` | Clave SSH privada (sin passphrase) |
| `VPS_PORT` | `22` | Puerto SSH (opcional, default 22) |
| `TELEGRAM_BOT_TOKEN` | `123456:ABC...` | Token del bot para notificar deploys |
| `TELEGRAM_CHAT_ID` | `123456789` | Tu chat_id de Telegram |

### Cómo generar la clave SSH para GitHub Actions

```bash
# En tu máquina local — genera un par de claves dedicado para CI/CD
ssh-keygen -t ed25519 -C "github-actions-divinittys" -f ~/.ssh/divinittys_deploy -N ""

# Agregar la clave PÚBLICA al servidor VPS
ssh-copy-id -i ~/.ssh/divinittys_deploy.pub divinittys@tu-servidor.com

# Copiar la clave PRIVADA al secret de GitHub (VPS_SSH_KEY)
cat ~/.ssh/divinittys_deploy
# ↑ Copiar TODO el contenido (incluyendo BEGIN y END) al secret de GitHub
```

### Flujo del CI/CD

```
Push a 'develop' → Tests + Lint (sin deploy)
Push a 'main'    → Tests + Lint + Build Docker + Deploy al VPS
Pull Request     → Tests + Lint (protección del main)
```

### GitHub Environments (aprobación manual)

El job de deploy usa `environment: production`. Para requerir aprobación manual:
1. Ve a Settings → Environments → production
2. Activa "Required reviewers"
3. Agrégate a ti mismo como reviewer

Esto te pide aprobar cada deploy antes de que toque el servidor.
