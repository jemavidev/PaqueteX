# -*- coding: utf-8 -*-
"""
Seam A — días en portería (issue 382, `.scratch/pendientes-cliente`): el contador cuenta SOLO mientras el paquete
está Recibido; al entregarse (o cancelarse después de recibido) queda congelado. Antes seguía contando desde la
recepción para siempre, así que un paquete entregado hace un año mostraba "365 días".
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.paquete_lifecycle import cancel, deliver, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.paquete_timeline_service import dias_desde_recibido
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration

_AHORA = datetime.now(timezone.utc)


def _staff(session):
    u = Usuario(nombre="Operador", rol=RolUsuario.OPERADOR)
    session.add(u)
    session.flush()
    return u


def _anunciado(session):
    return announce(session, anunciante_telefono="3001234567", anunciante_nombre="Ana",
                    destinatario=Destinatario.yo_mismo())


def test_anunciado_no_tiene_contador(db_session):
    assert dias_desde_recibido(_anunciado(db_session)) is None


def test_recibido_cuenta_desde_la_recepcion_hasta_hoy(db_session):
    p = _anunciado(db_session)
    receive(db_session, p, _staff(db_session))
    p.received_at = _AHORA - timedelta(days=5, hours=2)

    assert dias_desde_recibido(p) == 5


def test_entregado_queda_congelado_en_los_dias_que_estuvo_en_porteria(db_session):
    staff = _staff(db_session)
    p = _anunciado(db_session)
    receive(db_session, p, staff)
    deliver(db_session, p, staff)
    p.received_at = _AHORA - timedelta(days=41)
    p.delivered_at = _AHORA - timedelta(days=39)

    assert dias_desde_recibido(p) == 2


def test_cancelado_despues_de_recibido_queda_congelado_al_cancelar(db_session):
    staff = _staff(db_session)
    p = _anunciado(db_session)
    receive(db_session, p, staff)
    cancel(db_session, p, staff, "Anuncio erróneo")
    p.received_at = _AHORA - timedelta(days=30)
    p.cancelled_at = _AHORA - timedelta(days=27)

    assert dias_desde_recibido(p) == 3


def test_cancelado_sin_haberse_recibido_no_tiene_contador(db_session):
    p = _anunciado(db_session)
    cancel(db_session, p, _staff(db_session), "Anuncio erróneo")

    assert dias_desde_recibido(p) is None
