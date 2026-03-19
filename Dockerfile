# Dockerfile — Divinittys Meli Agent
# Python 3.11 slim, producción-ready

FROM python:3.11-slim

# Metadata
LABEL maintainer="rekkiem@gmail.com"
LABEL description="Divinittys - Agente Post-Venta Mercado Libre"

# Variables de entorno del sistema
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar e instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY . .

# Usuario no-root (seguridad)
RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

# Crear directorio para la DB SQLite si se usa
RUN mkdir -p /app/data

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Puerto expuesto
EXPOSE 8000

# Comando de inicio
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
