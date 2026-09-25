# -*- coding: utf-8 -*-
"""
Respaldos (`.scratch/respaldos-y-restauracion`, ticket 01) -- Seam A: el módulo de
respaldos contra un Postgres real (una base propia de este archivo, migrada con
Alembic y con datos COMMITEADOS: `pg_dump` corre en otra conexión y no vería los
de una transacción abierta), con el `pg_dump` 16 real.
"""

import hashlib
import subprocess
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import _harness as H
from app.domain.configuracion_conjunto_service import renombrar_conjunto
from app.domain.persona_service import get_or_create_persona
from app.domain.respaldo_service import (
    MotivoRespaldo,
    Instalacion,
    RespaldoEnCurso,
    RespaldoFallido,
    crear_respaldo,
    leer_commit,
    leer_manifiesto,
    operacion_exclusiva,
)
from app.domain.staff_service import create_initial_admin

# 2026-09-25 08:00 UTC = 03:00 en Colombia (UTC-5): la hora del respaldo diario.
_AHORA = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def bd_con_datos(postgres_server_url):
    """Una base aparte, migrada, con el conjunto renombrado y 1 usuario + 2 personas commiteados."""
    server = postgres_server_url.rsplit("/", 1)[0]
    nombre = f"resp_{uuid.uuid4().hex[:10]}"
    admin = create_engine(server + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{nombre}"'))
    url = server + "/" + nombre
    H.run_alembic(url, "upgrade", "head")
    engine = create_engine(url)
    with sessionmaker(bind=engine)() as s:
        admin_usuario = create_initial_admin(s, "admin@club.com", "Admin", "Contrasena1")
        renombrar_conjunto(s, "Balcones del Norte", admin_usuario)
        get_or_create_persona(s, "3001112233", "Ana")
        get_or_create_persona(s, "3004445566", "Luis")
        s.commit()
    engine.dispose()
    yield url
    with admin.connect() as conn:
        conn.execute(
            text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n AND pid <> pg_backend_pid()"),
            {"n": nombre},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{nombre}"'))
    admin.dispose()


def _origen(url):
    return Instalacion(database_url=url, dominio="test.papyrus.com.co", commit="abc1234def5678")


def test_un_respaldo_deja_la_base_y_un_manifiesto_que_dice_que_es(bd_con_datos, tmp_path):
    respaldo = crear_respaldo(_origen(bd_con_datos), tmp_path, MotivoRespaldo.DIARIO, ahora=_AHORA)

    assert respaldo.carpeta.parent == tmp_path
    assert (respaldo.carpeta / "base_datos.dump").stat().st_size > 0
    m = leer_manifiesto(respaldo.carpeta)
    assert m.fecha_hora_colombia == "2026-09-25 03:00:00"
    assert m.dominio == "test.papyrus.com.co"
    assert m.conjunto == "BALCONES DEL NORTE"
    assert m.commit == "abc1234def5678"
    assert m.motivo == MotivoRespaldo.DIARIO
    assert m.version_bd  # la migración Alembic vigente, no vacía
    assert m.conteos["usuarios"] == 1
    assert m.conteos["personas"] == 2
    assert m.conteos["paquetes"] == 0


def test_el_manifiesto_guarda_tamano_y_huella_de_cada_archivo(bd_con_datos, tmp_path):
    respaldo = crear_respaldo(_origen(bd_con_datos), tmp_path, MotivoRespaldo.DIARIO, ahora=_AHORA)

    dump = respaldo.carpeta / "base_datos.dump"
    m = leer_manifiesto(respaldo.carpeta)
    assert m.archivos["base_datos.dump"].tamano == dump.stat().st_size
    assert m.archivos["base_datos.dump"].sha256 == hashlib.sha256(dump.read_bytes()).hexdigest()


def _visibles(carpeta):
    return sorted(p.name for p in carpeta.iterdir() if not p.name.startswith("."))


def test_un_respaldo_que_falla_no_deja_una_carpeta_con_apariencia_de_buena(bd_con_datos, tmp_path):
    base_inexistente = bd_con_datos.rsplit("/", 1)[0] + "/no_existe_esta_base"

    with pytest.raises(RespaldoFallido):
        crear_respaldo(_origen(base_inexistente), tmp_path, MotivoRespaldo.DIARIO, ahora=_AHORA)

    assert _visibles(tmp_path) == []


def test_no_corren_dos_respaldos_a_la_vez_sobre_la_misma_carpeta(bd_con_datos, tmp_path):
    # Ej. el diario de las 3:00 y un "Respaldar ahora" al mismo tiempo: el segundo no arranca y lo dice.
    with operacion_exclusiva(tmp_path):
        with pytest.raises(RespaldoEnCurso):
            crear_respaldo(_origen(bd_con_datos), tmp_path, MotivoRespaldo.A_PEDIDO, ahora=_AHORA)

    assert _visibles(tmp_path) == []
    crear_respaldo(_origen(bd_con_datos), tmp_path, MotivoRespaldo.A_PEDIDO, ahora=_AHORA)  # liberado: ya puede
    assert len(_visibles(tmp_path)) == 1


def test_en_el_disco_quedan_solo_los_ultimos_3_respaldos(bd_con_datos, tmp_path):
    for dia in (21, 22, 23, 24):
        crear_respaldo(
            _origen(bd_con_datos), tmp_path, MotivoRespaldo.DIARIO, ahora=datetime(2026, 9, dia, 8, 0, tzinfo=timezone.utc)
        )

    assert _visibles(tmp_path) == [
        "2026-09-22_030000_diario",
        "2026-09-23_030000_diario",
        "2026-09-24_030000_diario",
    ]


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("empaquetado", [False, True], ids=["ref suelta", "packed-refs"])
def test_el_commit_desplegado_se_lee_del_checkout_sin_necesitar_git(tmp_path, empaquetado):
    # La imagen de la app no trae `git`: el commit se lee de `.git` del checkout montado.
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("x")
    _git(repo, "add", "a.txt")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "uno")
    if empaquetado:
        _git(repo, "pack-refs", "--all")

    assert leer_commit(repo) == _git(repo, "rev-parse", "HEAD")
