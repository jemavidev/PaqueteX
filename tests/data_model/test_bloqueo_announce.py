# -*- coding: utf-8 -*-
"""
Seam 1 — guard de bloqueo en `announce()` (.scratch/bloquear-clientes,
ticket 02), contra el Postgres efímero construido con `alembic upgrade head`.
"""

import pytest

from app.domain.apartamento_service import resolver_apartamento
from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante
from app.domain.paquete import Paquete
from app.domain.paquete_service import ClienteBloqueadoError, Destinatario, announce
from app.domain.persona import Persona
from app.domain.persona_service import bloquear_persona, get_or_create_persona
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration


def _usuario(session) -> Usuario:
    u = Usuario(nombre="Operador", rol=RolUsuario.OPERADOR)
    session.add(u)
    session.flush()
    return u


def test_anunciar_a_telefono_bloqueado_se_rechaza(db_session):
    persona = get_or_create_persona(db_session, "3001234567", "Ana")
    bloquear_persona(db_session, persona, "Motivo")

    with pytest.raises(ClienteBloqueadoError):
        announce(
            db_session,
            anunciante_telefono="3009999999",
            anunciante_nombre="Otro",
            destinatario=Destinatario.persona_registrada("3001234567"),
        )

    assert db_session.query(Paquete).count() == 0


def test_ocupante_sin_persona_propia_con_principal_bloqueado_se_rechaza(db_session):
    staff = _usuario(db_session)
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    principal = agregar_ocupante(db_session, apto, "Ana Principal", "3001234567")
    confirmar_ocupante(db_session, principal, staff)
    # Ocupante SIN Persona propia (sin teléfono) -- depende del teléfono de
    # notificación del Principal de su misma unidad.
    dependiente = agregar_ocupante(db_session, apto, "Hijo Dependiente", None)
    confirmar_ocupante(db_session, dependiente, staff)
    db_session.commit()

    principal_persona = get_or_create_persona(db_session, "3001234567", "Ana Principal")
    bloquear_persona(db_session, principal_persona, "Motivo")

    with pytest.raises(ClienteBloqueadoError):
        announce(
            db_session,
            anunciante_telefono="3009999999",
            anunciante_nombre="Mensajero",
            destinatario=Destinatario.ocupante(dependiente.id),
        )


def test_co_residente_con_persona_propia_no_se_ve_afectado(db_session):
    staff = _usuario(db_session)
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    principal = agregar_ocupante(db_session, apto, "Ana Principal", "3001234567")
    confirmar_ocupante(db_session, principal, staff)
    independiente = agregar_ocupante(db_session, apto, "Juan Independiente", "3002222222")
    confirmar_ocupante(db_session, independiente, staff)
    db_session.commit()

    principal_persona = get_or_create_persona(db_session, "3001234567", "Ana Principal")
    bloquear_persona(db_session, principal_persona, "Motivo")

    # No lanza -- el co-residente independiente tiene su propio teléfono.
    p = announce(
        db_session,
        anunciante_telefono="3009999999",
        anunciante_nombre="Mensajero",
        destinatario=Destinatario.ocupante(independiente.id),
    )
    assert p is not None


def test_bloquear_no_afecta_ningun_paquete_ya_existente(db_session):
    persona = get_or_create_persona(db_session, "3001234567", "Ana")
    p = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    db_session.commit()

    bloquear_persona(db_session, persona, "Motivo")

    db_session.refresh(p)
    assert p.estado.value == "ANUNCIADO"
