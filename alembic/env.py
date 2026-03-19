"""
alembic/env.py — Configuración de Alembic para migraciones de base de datos.

Soporta tanto SQLite (desarrollo) como PostgreSQL (producción).
Compatible con SQLAlchemy async.

Uso:
  # Generar nueva migración tras cambiar models.py
  alembic revision --autogenerate -m "descripcion_del_cambio"

  # Aplicar migraciones pendientes
  alembic upgrade head

  # Ver historial
  alembic history

  # Revertir última migración
  alembic downgrade -1
"""

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Importar modelos para que Alembic los detecte en autogenerate
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.models import Base
from app.config import settings

# Configuración de Alembic desde alembic.ini
config = context.config

# Configurar logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata de los modelos (para autogenerate)
target_metadata = Base.metadata

# Usar DATABASE_URL del .env
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)


def run_migrations_offline() -> None:
    """Ejecuta migraciones en modo 'offline' (sin conexión real)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Ejecuta migraciones en modo async (para asyncpg / aiosqlite)."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point para migraciones online."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
