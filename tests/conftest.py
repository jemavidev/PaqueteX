# -*- coding: utf-8 -*-
"""
Fixtures del arnés de integración (rebanada data-model).

- `postgres_server_url`: usa `TEST_DATABASE_URL` si está (CI: service:postgres),
  o levanta un `postgres:16` desechable con docker (local).
- `migrated_db_url`: construye el esquema con `alembic upgrade head` (NO create_all).
- `db_session`: sesión aislada por test (transacción con rollback al final).
- `empty_db_url`: una base VACÍA fresca en el mismo servidor, para el round-trip
  de migraciones (Seam B) sin contaminar la base de los tests de dominio.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import _harness as H


@pytest.fixture(scope="session")
def postgres_server_url():
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        H.wait_for_postgres(url)
        yield url
        return

    name, url = H.start_ephemeral_postgres()
    try:
        H.wait_for_postgres(url)
        yield url
    finally:
        H.stop_container(name)


@pytest.fixture(scope="session")
def migrated_db_url(postgres_server_url):
    # El esquema se construye corriendo las migraciones, no con create_all.
    H.run_alembic(postgres_server_url, "upgrade", "head")
    return postgres_server_url


@pytest.fixture()
def db_session(migrated_db_url):
    engine = create_engine(migrated_db_url)
    connection = engine.connect()
    trans = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        if trans.is_active:
            trans.rollback()
        connection.close()
        engine.dispose()


@pytest.fixture()
def empty_db_url(postgres_server_url):
    server = postgres_server_url.rsplit("/", 1)[0]
    dbname = f"rt_{uuid.uuid4().hex[:10]}"
    admin = create_engine(server + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    try:
        yield server + "/" + dbname
    finally:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": dbname},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        admin.dispose()
