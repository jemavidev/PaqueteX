# -*- coding: utf-8 -*-
"""
Restauración de respaldos (`.scratch/respaldos-y-restauracion`, ticket 02) -- Seam A: el módulo de
respaldos contra un Postgres real y `pg_dump`/`pg_restore` 16 reales. Cada prueba usa su propia base
(migrada y con datos commiteados), porque restaurar la reemplaza entera.
"""

import uuid
from datetime import datetime, timezone

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import _harness as H
from app.domain.persona_service import get_or_create_persona
from app.domain.respaldo_service import (
    Instalacion,
    MotivoRespaldo,
    RestauracionRechazada,
    crear_respaldo,
    leer_historial,
    leer_manifiesto,
    listar_respaldos,
    restaurar,
    verificar_respaldo,
)
from app.domain.staff_service import create_initial_admin

_AHORA = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
_DOMINIO = "test.papyrus.com.co"


@pytest.fixture()
def nueva_bd(postgres_server_url):
    """Fábrica de bases propias de la prueba, migradas; se borran todas al final."""
    server = postgres_server_url.rsplit("/", 1)[0]
    admin = create_engine(server + "/postgres", isolation_level="AUTOCOMMIT")
    creadas = []

    def crear(revision="head"):
        nombre = f"rest_{uuid.uuid4().hex[:10]}"
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{nombre}"'))
        creadas.append(nombre)
        url = server + "/" + nombre
        H.run_alembic(url, "upgrade", revision)
        return url

    yield crear
    with admin.connect() as conn:
        for nombre in creadas:
            conn.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n AND pid <> pg_backend_pid()"),
                {"n": nombre},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{nombre}"'))
    admin.dispose()


def _sembrar(url):
    engine = create_engine(url)
    with sessionmaker(bind=engine)() as s:
        create_initial_admin(s, "admin@club.com", "Admin", "Contrasena1")
        get_or_create_persona(s, "3001112233", "Ana")
        s.commit()
    engine.dispose()


def _personas(url):
    engine = create_engine(url)
    with engine.connect() as conn:
        nombres = sorted(conn.execute(text("SELECT nombre FROM personas")).scalars())
    engine.dispose()
    return nombres


def _agregar_persona(url, telefono, nombre):
    engine = create_engine(url)
    with sessionmaker(bind=engine)() as s:
        get_or_create_persona(s, telefono, nombre)
        s.commit()
    engine.dispose()


def _instalacion(url, dominio=_DOMINIO):
    return Instalacion(database_url=url, dominio=dominio, commit="abc1234def5678")


def test_restaurar_devuelve_la_base_al_momento_del_respaldo(nueva_bd, tmp_path):
    url = nueva_bd()
    _sembrar(url)
    respaldo = crear_respaldo(_instalacion(url), tmp_path, MotivoRespaldo.DIARIO, ahora=_AHORA)
    _agregar_persona(url, "3009998877", "Posterior al respaldo")

    restaurar(respaldo.carpeta, _instalacion(url), confirmacion=_DOMINIO, carpeta_respaldos=tmp_path, codigo=H.CODE_DIR)

    assert _personas(url) == ["ANA"]


def test_un_respaldo_danado_no_se_restaura_y_la_base_queda_intacta(nueva_bd, tmp_path):
    url = nueva_bd()
    _sembrar(url)
    respaldo = crear_respaldo(_instalacion(url), tmp_path, MotivoRespaldo.DIARIO, ahora=_AHORA)
    _agregar_persona(url, "3009998877", "Posterior al respaldo")
    with open(respaldo.carpeta / "base_datos.dump", "ab") as f:
        f.write(b"descarga incompleta o corrupta")

    with pytest.raises(RestauracionRechazada, match="base_datos.dump"):
        restaurar(respaldo.carpeta, _instalacion(url), confirmacion=_DOMINIO, carpeta_respaldos=tmp_path, codigo=H.CODE_DIR)

    assert _personas(url) == ["ANA", "POSTERIOR AL RESPALDO"]


def test_un_respaldo_de_otro_conjunto_solo_se_restaura_si_se_pide_explicitamente(nueva_bd, tmp_path):
    url = nueva_bd()
    _sembrar(url)
    respaldo = crear_respaldo(_instalacion(url, "elclub.papyrus.com.co"), tmp_path, MotivoRespaldo.DIARIO, ahora=_AHORA)
    _agregar_persona(url, "3009998877", "Posterior al respaldo")
    balcones = _instalacion(url, "balcones.papyrus.com.co")

    with pytest.raises(RestauracionRechazada, match="elclub.papyrus.com.co"):
        restaurar(respaldo.carpeta, balcones, confirmacion="balcones.papyrus.com.co", carpeta_respaldos=tmp_path, codigo=H.CODE_DIR)
    assert _personas(url) == ["ANA", "POSTERIOR AL RESPALDO"]

    # Ej. montar El Club en un servidor nuevo con otro nombre: se pide a propósito.
    restaurar(
        respaldo.carpeta, balcones, confirmacion="balcones.papyrus.com.co", carpeta_respaldos=tmp_path, codigo=H.CODE_DIR, permitir_otro_destino=True
    )
    assert _personas(url) == ["ANA"]


@pytest.mark.parametrize("respuesta", ["si", "", "test.papyrus", "elclub.papyrus.com.co"])
def test_sin_escribir_el_dominio_exacto_no_se_restaura(nueva_bd, tmp_path, respuesta):
    url = nueva_bd()
    _sembrar(url)
    respaldo = crear_respaldo(_instalacion(url), tmp_path, MotivoRespaldo.DIARIO, ahora=_AHORA)
    _agregar_persona(url, "3009998877", "Posterior al respaldo")

    with pytest.raises(RestauracionRechazada, match="confirm"):
        restaurar(respaldo.carpeta, _instalacion(url), confirmacion=respuesta, carpeta_respaldos=tmp_path, codigo=H.CODE_DIR)

    assert _personas(url) == ["ANA", "POSTERIOR AL RESPALDO"]


def test_antes_de_restaurar_se_respalda_lo_actual_para_poder_deshacer(nueva_bd, tmp_path):
    url = nueva_bd()
    _sembrar(url)
    respaldo = crear_respaldo(_instalacion(url), tmp_path, MotivoRespaldo.DIARIO, ahora=_AHORA)
    _agregar_persona(url, "3009998877", "Posterior al respaldo")

    restaurar(respaldo.carpeta, _instalacion(url), confirmacion=_DOMINIO, carpeta_respaldos=tmp_path, codigo=H.CODE_DIR)

    previos = [c for c in listar_respaldos(tmp_path) if leer_manifiesto(c).motivo == MotivoRespaldo.ANTES_DE_RESTAURAR]
    assert len(previos) == 1
    assert leer_manifiesto(previos[0]).conteos["personas"] == 2  # lo que había justo antes, incluida la posterior
    assert leer_historial(tmp_path)[-1]["motivo"] == "antes_de_restaurar"  # queda en el historial (y el CLI la sube)


def _version(url):
    engine = create_engine(url)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()
    return version


def test_un_respaldo_de_una_version_mas_nueva_que_el_codigo_no_se_restaura(nueva_bd, tmp_path):
    url = nueva_bd()
    _sembrar(url)
    respaldo = crear_respaldo(_instalacion(url), tmp_path, MotivoRespaldo.DIARIO, ahora=_AHORA)
    manifiesto = respaldo.carpeta / "manifiesto.txt"
    manifiesto.write_text(manifiesto.read_text().replace(_version(url), "9999_de_una_version_futura"))
    _agregar_persona(url, "3009998877", "Posterior al respaldo")

    with pytest.raises(RestauracionRechazada, match="sistema.tar.gz"):
        restaurar(respaldo.carpeta, _instalacion(url), confirmacion=_DOMINIO, carpeta_respaldos=tmp_path, codigo=H.CODE_DIR)

    assert _personas(url) == ["ANA", "POSTERIOR AL RESPALDO"]


def test_un_respaldo_de_una_version_anterior_se_restaura_y_queda_al_dia(nueva_bd, tmp_path):
    script = ScriptDirectory.from_config(H.alembic_config())
    head = script.get_current_head()
    anterior = script.get_revision(head).down_revision
    vieja = nueva_bd(anterior)
    _sembrar(vieja)
    respaldo = crear_respaldo(_instalacion(vieja), tmp_path, MotivoRespaldo.DIARIO, ahora=_AHORA)
    actual = nueva_bd()

    restaurar(respaldo.carpeta, _instalacion(actual), confirmacion=_DOMINIO, carpeta_respaldos=tmp_path, codigo=H.CODE_DIR)

    assert _version(actual) == head
    assert _personas(actual) == ["ANA"]


def test_un_manifiesto_incompleto_se_rechaza_con_un_motivo_claro(nueva_bd, tmp_path):
    url = nueva_bd()
    respaldo = crear_respaldo(_instalacion(url), tmp_path, MotivoRespaldo.DIARIO, ahora=_AHORA)
    manifiesto = respaldo.carpeta / "manifiesto.txt"
    manifiesto.write_text("\n".join(l for l in manifiesto.read_text().splitlines() if not l.startswith("dominio")))

    with pytest.raises(RestauracionRechazada, match="manifiesto"):
        verificar_respaldo(respaldo.carpeta)
