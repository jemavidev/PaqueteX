# ========================================
# PaqueteXv.2 — Entorno de Alembic (árbol de raíz única)
# ========================================
#
# Apunta SOLO a la metadata del modelo nuevo (`app.domain.base.Base`). No importa
# los modelos viejos ni el subsistema fuera de alcance (facturas/productos/CUFE),
# de modo que `alembic upgrade head` construya únicamente el esquema nuevo.
#
# URL de base de datos (en orden de prioridad):
#   1. `-x db_url=...`      (lo usa el arnés de test → Postgres efímero)
#   2. TEST_DATABASE_URL    (env, p.ej. el service:postgres de CI)
#   3. DATABASE_URL         (env, deploy)
# NUNCA se usa la URL de producción para los tests: el arnés siempre pasa
# `-x db_url=` apuntando al Postgres desechable.

from logging.config import fileConfig
import os
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool

# Cargar .env si existe (no sobreescribe variables ya presentes en el entorno).
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

# src al path para poder importar el paquete de dominio nuevo.
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from app.domain.base import Base  # noqa: E402
from app.domain import persona  # noqa: E402,F401  (registra 'personas' en Base.metadata)
from app.domain import apartamento  # noqa: E402,F401  (registra 'apartamentos' en Base.metadata)
from app.domain import usuario  # noqa: E402,F401  (registra 'usuarios' en Base.metadata)
from app.domain import paquete  # noqa: E402,F401  (registra 'paquetes' en Base.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    url = (
        x_args.get("db_url")
        or os.getenv("TEST_DATABASE_URL")
        or os.getenv("DATABASE_URL")
    )
    if not url:
        raise ValueError(
            "No hay URL de base de datos. Pase `-x db_url=...` o defina "
            "TEST_DATABASE_URL / DATABASE_URL."
        )
    return url


def run_migrations_offline() -> None:
    """Migraciones en modo offline (emite SQL, no requiere DBAPI)."""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Migraciones en modo online (crea Engine y conecta)."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _resolve_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
