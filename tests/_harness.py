# -*- coding: utf-8 -*-
"""
Utilidades del arnés de integración de la rebanada data-model.

El esquema de test se construye SIEMPRE corriendo las migraciones
(`alembic upgrade head`), nunca con `create_all`, de modo que los tests de
comportamiento ejerciten las migraciones de paso.
"""

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg2

HARNESS_DIR = Path(__file__).resolve().parent          # CODE/tests
CODE_DIR = HARNESS_DIR.parent                           # CODE
ALEMBIC_INI = CODE_DIR / "alembic.ini"
POSTGRES_IMAGE = os.getenv("TEST_POSTGRES_IMAGE", "postgres:16")


def free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for_postgres(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            conn = psycopg2.connect(url)
            conn.close()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"Postgres no quedó listo en {timeout}s: {last_error}")


def run_alembic(url: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Corre alembic contra `url` usando el mismo intérprete que pytest.

    Pasa `-x db_url=` (prioridad máxima en env.py) para no tocar jamás la URL
    de producción.
    """
    env = dict(os.environ)
    env["DATABASE_URL"] = url  # respaldo; -x db_url tiene prioridad
    cmd = [sys.executable, "-m", "alembic", "-x", f"db_url={url}", *args]
    result = subprocess.run(
        cmd, cwd=str(CODE_DIR), env=env, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise AssertionError(
            "alembic "
            + " ".join(args)
            + f" falló (rc={result.returncode}).\n"
            + f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result


def table_exists(url: str, table: str) -> bool:
    conn = psycopg2.connect(url)
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        return cur.fetchone()[0] is not None
    finally:
        conn.close()


def column_exists(url: str, table: str, column: str) -> bool:
    conn = psycopg2.connect(url)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            (table, column),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def rename_column(url: str, table: str, old: str, new: str) -> None:
    conn = psycopg2.connect(url)
    try:
        cur = conn.cursor()
        cur.execute(f'ALTER TABLE {table} RENAME COLUMN {old} TO {new}')
        conn.commit()
    finally:
        conn.close()


def alembic_config():
    """Config de Alembic para leer el ScriptDirectory (sin tocar la BD)."""
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(CODE_DIR / "alembic"))
    return cfg


def start_ephemeral_postgres() -> tuple[str, str]:
    """Levanta un `postgres:16` desechable. Devuelve (nombre_contenedor, url)."""
    name = f"paquetex_test_pg_{uuid.uuid4().hex[:8]}"
    port = free_port()
    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "-e", "POSTGRES_PASSWORD=postgres",
            "-e", "POSTGRES_USER=postgres",
            "-e", "POSTGRES_DB=paquetex_test",
            "-p", f"{port}:5432",
            POSTGRES_IMAGE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    url = f"postgresql://postgres:postgres@localhost:{port}/paquetex_test"
    return name, url


def stop_container(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
