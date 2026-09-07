# -*- coding: utf-8 -*-
"""
Reactivación automática de baja administrativa al recibir un paquete
(.scratch/baja-administrativa, ticket 02).

Comportamiento observable: un paquete a nombre de un residente en baja
administrativa que llega a Recibido lo reactiva ahí mismo -- sin paso
manual, sin reconectar ningún Ocupante. Recibir un paquete de alguien que
no está de baja no toca nada.
"""

import pytest

from app.domain.apartamento_service import resolver_apartamento
from app.domain.ocupante_service import (
    agregar_ocupante,
    confirmar_ocupante,
    desvincular_ocupante_activo_de_persona,
)
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.domain.persona_service import dar_de_baja_administrativa
from app.domain.staff_service import create_initial_admin
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration

_PW = "Contrasena1"


def _apto(db_session, torre="TORRE 1", numero="101"):
    return resolver_apartamento(db_session, torre, numero)


def _staff(session):
    admin = session.query(Usuario).filter(Usuario.rol == RolUsuario.ADMIN).first()
    if admin is not None:
        return admin
    return create_initial_admin(session, "admin@test.local", "Admin", _PW)


def test_recibir_reactiva_a_alguien_de_baja_resuelto_por_telefono(db_session):
    apto = _apto(db_session)
    ocupante = agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")
    principal = confirmar_ocupante(db_session, ocupante, _staff(db_session))
    persona = db_session.get(Persona, principal.persona_id)

    desvincular_ocupante_activo_de_persona(db_session, persona)
    dar_de_baja_administrativa(db_session, persona)
    assert persona.baja_administrativa_en is not None

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        destinatario=Destinatario.yo_mismo(),
    )
    receive(db_session, paquete, _staff(db_session))
    db_session.refresh(persona)

    assert persona.baja_administrativa_en is None


def test_recibir_no_reactiva_a_alguien_que_no_esta_de_baja(db_session):
    apto = _apto(db_session)
    ocupante = agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")
    principal = confirmar_ocupante(db_session, ocupante, _staff(db_session))
    persona = db_session.get(Persona, principal.persona_id)
    assert persona.baja_administrativa_en is None

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        destinatario=Destinatario.yo_mismo(),
    )
    receive(db_session, paquete, _staff(db_session))
    db_session.refresh(persona)

    assert persona.baja_administrativa_en is None


def test_recibir_reactiva_sin_reconectar_ningun_ocupante(db_session):
    apto = _apto(db_session)
    ocupante = agregar_ocupante(db_session, apto, "Ana", telefono="3001234567")
    principal = confirmar_ocupante(db_session, ocupante, _staff(db_session))
    persona = db_session.get(Persona, principal.persona_id)

    desvincular_ocupante_activo_de_persona(db_session, persona)
    dar_de_baja_administrativa(db_session, persona)

    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        destinatario=Destinatario.yo_mismo(),
    )
    receive(db_session, paquete, _staff(db_session))
    db_session.refresh(persona)
    db_session.refresh(ocupante)

    assert persona.baja_administrativa_en is None
    # Sigue desvinculado -- la reactivación NO reconecta ningún Ocupante.
    assert ocupante.desvinculado_en is not None


def test_recibir_sin_destinatario_resoluble_no_falla(db_session):
    # Destinatario declarado a mano (sin Ocupante/Persona previa) -- ver
    # `Destinatario.declarado_por_cliente` -- announce() igual crea una
    # Persona nueva para el destinatario aquí, así que se fuerza el caso
    # "sin match" limpiando el teléfono del paquete después de anunciar.
    paquete = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    paquete.recipient_phone = None

    receive(db_session, paquete, _staff(db_session))  # no debe lanzar nada
