# -*- coding: utf-8 -*-
"""
Seam 1 — `migrar_codigos_del_anio` (.scratch/migracion-por-anio, ticket 01),
contra el Postgres efímero construido con `alembic upgrade head`.
"""

from datetime import datetime, timezone

import pytest

from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_lifecycle import cancel, deliver, receive
from app.domain.paquete_service import Destinatario, announce, migrar_codigos_del_anio
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration


def _usuario(session) -> Usuario:
    u = Usuario(nombre="Operador", rol=RolUsuario.OPERADOR)
    session.add(u)
    session.flush()
    return u


def _anunciar(session, tel="3001234567") -> Paquete:
    return announce(
        session,
        anunciante_telefono=tel,
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )


def _fecha(anio: int) -> datetime:
    return datetime(anio, 6, 15, tzinfo=timezone.utc)


def test_entregado_del_anio_anterior_migra(db_session):
    staff = _usuario(db_session)
    p = _anunciar(db_session)
    receive(db_session, p, staff)
    deliver(db_session, p, staff)
    p.delivered_at = _fecha(2025)
    db_session.flush()
    codigo_original = p.access_code

    resumen = migrar_codigos_del_anio(db_session, 2025)

    db_session.refresh(p)
    assert len(p.access_code) == 6
    assert p.access_code == codigo_original + "25"
    assert resumen.total == 1


def test_cancelado_del_anio_anterior_migra_por_cancelled_at(db_session):
    staff = _usuario(db_session)
    p = _anunciar(db_session)
    cancel(db_session, p, staff, "Anuncio erróneo")
    p.cancelled_at = _fecha(2025)
    db_session.flush()
    codigo_original = p.access_code

    migrar_codigos_del_anio(db_session, 2025)

    db_session.refresh(p)
    assert p.access_code == codigo_original + "25"


def test_anunciado_no_migra_sin_importar_su_antiguedad(db_session):
    p = _anunciar(db_session)
    p.announced_at = _fecha(2020)
    db_session.flush()
    codigo_original = p.access_code

    migrar_codigos_del_anio(db_session, 2025)

    db_session.refresh(p)
    assert p.access_code == codigo_original


def test_recibido_no_migra_sin_importar_su_antiguedad(db_session):
    staff = _usuario(db_session)
    p = _anunciar(db_session)
    receive(db_session, p, staff)
    p.received_at = _fecha(2020)
    db_session.flush()
    codigo_original = p.access_code

    migrar_codigos_del_anio(db_session, 2025)

    db_session.refresh(p)
    assert p.access_code == codigo_original


def test_segunda_corrida_no_vuelve_a_tocar_lo_ya_migrado(db_session):
    staff = _usuario(db_session)
    p = _anunciar(db_session)
    receive(db_session, p, staff)
    deliver(db_session, p, staff)
    p.delivered_at = _fecha(2025)
    db_session.flush()

    migrar_codigos_del_anio(db_session, 2025)
    db_session.refresh(p)
    codigo_migrado = p.access_code

    resumen = migrar_codigos_del_anio(db_session, 2025)

    db_session.refresh(p)
    assert p.access_code == codigo_migrado
    assert resumen.total == 0


def test_ejecutar_false_cuenta_sin_modificar(db_session):
    staff = _usuario(db_session)
    p = _anunciar(db_session)
    receive(db_session, p, staff)
    deliver(db_session, p, staff)
    p.delivered_at = _fecha(2025)
    db_session.flush()
    codigo_original = p.access_code

    resumen = migrar_codigos_del_anio(db_session, 2025, ejecutar=False)

    db_session.refresh(p)
    assert resumen.total == 1
    assert p.access_code == codigo_original


def test_entregado_del_anio_vigente_no_se_incluye(db_session):
    staff = _usuario(db_session)
    p = _anunciar(db_session)
    receive(db_session, p, staff)
    deliver(db_session, p, staff)
    p.delivered_at = _fecha(2026)
    db_session.flush()
    codigo_original = p.access_code

    resumen = migrar_codigos_del_anio(db_session, 2025)

    db_session.refresh(p)
    assert resumen.total == 0
    assert p.access_code == codigo_original
