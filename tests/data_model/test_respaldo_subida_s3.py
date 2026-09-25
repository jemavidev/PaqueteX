# -*- coding: utf-8 -*-
"""
Respaldos, ticket 04 (`.scratch/respaldos-y-restauracion`): cada respaldo sube al bucket de respaldos, bajo la
carpeta de su dominio y en `diario/`, `mensual/` (el del día 1), `anual/` (el del 1 de enero) o `puntual/` -- las
reglas de conservación de S3 van por carpeta. Seam A con un destino S3 falso en memoria.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

import _harness as H
from app.domain.respaldo_service import (
    Instalacion,
    MotivoRespaldo,
    RespaldoFallido,
    crear_respaldo,
    subir_respaldo,
)


class DestinoFalso:
    """El puerto `DestinoRespaldos` en memoria: guarda {clave: bytes}."""

    def __init__(self, falla=False):
        self.objetos = {}
        self.falla = falla

    def subir(self, clave, ruta):
        if self.falla:
            raise ConnectionError("S3 no responde")
        self.objetos[clave] = ruta.read_bytes()


@pytest.fixture(scope="module")
def bd(postgres_server_url):
    server = postgres_server_url.rsplit("/", 1)[0]
    nombre = f"s3_{uuid.uuid4().hex[:10]}"
    admin = create_engine(server + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{nombre}"'))
    url = server + "/" + nombre
    H.run_alembic(url, "upgrade", "head")
    yield url
    with admin.connect() as conn:
        conn.execute(
            text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n AND pid <> pg_backend_pid()"),
            {"n": nombre},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{nombre}"'))
    admin.dispose()


def _respaldo(bd, tmp_path, motivo, ahora):
    instalacion = Instalacion(database_url=bd, dominio="test.papyrus.com.co", commit="abc1234")
    return crear_respaldo(instalacion, tmp_path, motivo, ahora=ahora)


def _carpetas(destino):
    return sorted({clave.rsplit("/", 2)[0] for clave in destino.objetos})


@pytest.mark.parametrize(
    "motivo, ahora_utc, esperadas",
    [
        # 25-sep 03:00 Colombia: un día cualquiera.
        (MotivoRespaldo.DIARIO, datetime(2026, 9, 25, 8, 0), ["diario"]),
        # 1-oct 03:00 Colombia (08:00 UTC): además la mensual.
        (MotivoRespaldo.DIARIO, datetime(2026, 10, 1, 8, 0), ["diario", "mensual"]),
        # 1-ene 03:00 Colombia: diaria, mensual y anual.
        (MotivoRespaldo.DIARIO, datetime(2027, 1, 1, 8, 0), ["anual", "diario", "mensual"]),
        # 1-oct 02:00 UTC es todavía 30-sep en Colombia: el día cuenta en hora de Colombia.
        (MotivoRespaldo.DIARIO, datetime(2026, 10, 1, 2, 0), ["diario"]),
        (MotivoRespaldo.ANTES_DE_DEPLOY, datetime(2026, 10, 1, 8, 0), ["puntual"]),
        (MotivoRespaldo.A_PEDIDO, datetime(2026, 9, 25, 15, 0), ["puntual"]),
    ],
)
def test_cada_respaldo_sube_a_la_carpeta_de_su_dominio_y_tipo(bd, tmp_path, motivo, ahora_utc, esperadas):
    respaldo = _respaldo(bd, tmp_path, motivo, ahora_utc.replace(tzinfo=timezone.utc))
    destino = DestinoFalso()

    subir_respaldo(respaldo, destino)

    assert _carpetas(destino) == [f"test.papyrus.com.co/{t}" for t in esperadas]
    # Cada carpeta lleva todos los archivos del respaldo, bajo el nombre del respaldo.
    for tipo in esperadas:
        claves = sorted(c for c in destino.objetos if c.startswith(f"test.papyrus.com.co/{tipo}/"))
        assert claves == [
            f"test.papyrus.com.co/{tipo}/{respaldo.carpeta.name}/{n}" for n in ("base_datos.dump", "manifiesto.txt")
        ]


def test_si_la_subida_falla_el_respaldo_local_se_conserva(bd, tmp_path):
    respaldo = _respaldo(bd, tmp_path, MotivoRespaldo.DIARIO, datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc))

    with pytest.raises(RespaldoFallido, match="S3"):
        subir_respaldo(respaldo, DestinoFalso(falla=True))

    assert (respaldo.carpeta / "base_datos.dump").is_file()
