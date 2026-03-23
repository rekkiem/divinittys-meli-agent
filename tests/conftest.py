"""
tests/conftest.py — Fixtures compartidos para toda la suite de tests.

Problemas resueltos:
  1. AsyncMock chain: scalar_one_or_none() devuelve coroutine, no el valor.
     → make_db_mock() usa MagicMock para el resultado de execute().

  2. Webhook tests: 'no such table' / 'unable to open database file'.
     → Se crea el directorio data/ y se sobreescribe el dependency get_db
       con una sesión mockeada.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# ── Asegurar que existe el directorio de datos para SQLite ───────────────────
# El engine se crea al importar app.database con la DATABASE_URL del .env.
# Si la URL apunta a ./data/*, el directorio debe existir antes de cualquier test.
Path("data").mkdir(exist_ok=True)


# ── Helper: mock de AsyncSession correctamente configurado ───────────────────

def make_db_mock(return_value=None):
    """
    Crea un AsyncSession mock donde scalar_one_or_none() devuelve el valor
    directamente (no una coroutine).

    PROBLEMA que resuelve:
      mock_db = AsyncMock()
      mock_db.execute.return_value.scalar_one_or_none.return_value = obj
      → execute.return_value es AsyncMock → scalar_one_or_none() devuelve coroutine
      → AttributeError: 'coroutine' object has no attribute 'expires_at'

    SOLUCIÓN:
      El result de execute es MagicMock (sync), no AsyncMock.
      Así result.scalar_one_or_none() devuelve obj directamente.
    """
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = return_value
    mock_result.scalars.return_value.all.return_value = (
        return_value if isinstance(return_value, list)
        else ([return_value] if return_value is not None else [])
    )
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.scalar = AsyncMock(return_value=0)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.close = AsyncMock()
    mock_db.refresh = AsyncMock()
    return mock_db, mock_result


def make_db_mock_sequence(*values):
    """Mock de DB que retorna distintos valores en llamadas sucesivas."""
    mock_db = AsyncMock()
    results = []
    for v in values:
        r = MagicMock()
        r.scalar_one_or_none.return_value = v
        r.scalars.return_value.all.return_value = [v] if v is not None else []
        results.append(r)
    mock_db.execute = AsyncMock(side_effect=results)
    mock_db.scalar = AsyncMock(return_value=0)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.close = AsyncMock()
    return mock_db

