# -*- coding: utf-8 -*-
"""
Seam A — Notificación de eventos del Paquete (mensaje + destino + best-effort).

Comportamiento observable: el mensaje correcto por evento (Cancelado incluye el
motivo); el destino es el Destinatario si tiene teléfono, si no el Anunciante;
un fallo del sender no se propaga.
"""

import pytest

from app.domain.notification_sender import ConsoleNotificationSender
from app.domain.notificacion_service import (
    construir_mensaje,
    notificar_evento,
    resolver_destino,
    resolver_destino_notificable,
)
from app.domain.paquete import EstadoPaquete
from app.domain.paquete_lifecycle import cancel, deliver, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona_service import anonimizar_persona, get_or_create_persona
from app.domain.plantilla_notificacion import PlantillaNotificacion
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration


def _usuario(session) -> Usuario:
    u = Usuario(nombre="Operador", rol=RolUsuario.OPERADOR)
    session.add(u)
    session.flush()
    return u


def _anunciar(session, destinatario=None):
    return announce(
        session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=destinatario or Destinatario.yo_mismo(),
    )


def test_mensaje_anunciado_incluye_el_codigo_de_acceso(db_session):
    p = _anunciar(db_session)
    msg = construir_mensaje(db_session, EstadoPaquete.ANUNCIADO, p)
    assert "Ana" in msg and p.access_code in msg


def test_mensaje_recibido(db_session):
    p = _anunciar(db_session)
    msg = construir_mensaje(db_session, EstadoPaquete.RECIBIDO, p)
    assert "Ana" in msg and "portería" in msg


def test_mensaje_entregado(db_session):
    p = _anunciar(db_session)
    msg = construir_mensaje(db_session, EstadoPaquete.ENTREGADO, p)
    assert "Ana" in msg and "entregado" in msg


def test_mensaje_cancelado_incluye_el_motivo(db_session):
    op = _usuario(db_session)
    p = _anunciar(db_session)
    cancel(db_session, p, op, "NO_RECLAMADO")

    msg = construir_mensaje(db_session, EstadoPaquete.CANCELADO, p)
    assert "cancelado" in msg.lower()
    assert "no reclamado" in msg.lower()


def test_con_plantilla_personalizada_la_usa_en_vez_del_default(db_session):
    p = _anunciar(db_session)
    db_session.add(
        PlantillaNotificacion(
            evento=EstadoPaquete.RECIBIDO.value,
            motivo=None,
            texto="Hola {recipient_name}, ya llegó tu encomienda.",
        )
    )
    db_session.flush()

    msg = construir_mensaje(db_session, EstadoPaquete.RECIBIDO, p)

    assert msg == "Hola Ana, ya llegó tu encomienda."


def test_sin_plantilla_personalizada_usa_el_default(db_session):
    p = _anunciar(db_session)
    msg = construir_mensaje(db_session, EstadoPaquete.RECIBIDO, p)
    assert "portería" in msg  # el texto por defecto, sin cambios


def test_destino_es_el_destinatario_registrado(db_session):
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(db_session, "3019999999", "Beto")
    p = _anunciar(db_session, Destinatario.persona_registrada("3019999999"))

    assert resolver_destino(p) == "+573019999999"


def test_destino_es_el_anunciante_si_destinatario_sin_telefono(db_session):
    p = _anunciar(db_session, Destinatario.solo_nombre("Carlos"))
    assert resolver_destino(p) == "+573001234567"  # el anunciante (Ana)


def test_notificar_evento_llama_al_sender_con_destino_y_mensaje(db_session):
    p = _anunciar(db_session)
    sender = ConsoleNotificationSender()

    notificar_evento(db_session, p, EstadoPaquete.RECIBIDO, sender)

    assert len(sender.enviados) == 1
    destino, mensaje = sender.enviados[0]
    assert destino == "+573001234567"
    assert "Ana" in mensaje


def test_notificar_evento_no_propaga_si_el_sender_falla(db_session):
    p = _anunciar(db_session)

    class _SenderQueFalla:
        def enviar(self, destino, mensaje):
            raise RuntimeError("proveedor caído")

    notificar_evento(db_session, p, EstadoPaquete.ENTREGADO, _SenderQueFalla())  # no debe lanzar


# --------------------------------------------------------------------------- #
# resolver_destino_notificable — regla unificada de fallback al Anunciante
# (nombre-sin-teléfono Y destinatario-anonimizado-después, misma función).
# --------------------------------------------------------------------------- #
def test_resolver_destino_notificable_destinatario_vivo_con_telefono(db_session):
    get_or_create_persona(db_session, "3019999999", "Beto")
    p = _anunciar(db_session, Destinatario.persona_registrada("3019999999"))

    persona = resolver_destino_notificable(db_session, p)

    assert persona.telefono == "+573019999999"


def test_resolver_destino_notificable_nombre_sin_telefono_cae_al_anunciante(db_session):
    p = _anunciar(db_session, Destinatario.solo_nombre("Carlos"))

    persona = resolver_destino_notificable(db_session, p)

    assert persona.telefono == "+573001234567"  # Ana, la anunciante


def test_resolver_destino_notificable_destinatario_anonimizado_cae_al_anunciante(db_session):
    beto = get_or_create_persona(db_session, "3019999999", "Beto")
    p = _anunciar(db_session, Destinatario.persona_registrada("3019999999"))

    anonimizar_persona(db_session, beto)  # Beto ya no tiene ese teléfono

    persona = resolver_destino_notificable(db_session, p)

    assert persona.telefono == "+573001234567"  # cae a Ana, MISMO resultado que sin-teléfono


def test_resolver_destino_notificable_anunciante_tambien_anonimizado_da_none(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    p = _anunciar(db_session, Destinatario.solo_nombre("Carlos"))

    anonimizar_persona(db_session, ana)

    assert resolver_destino_notificable(db_session, p) is None


# --------------------------------------------------------------------------- #
# notificar_evento respeta notificaciones_activas.
# --------------------------------------------------------------------------- #
def test_notificaciones_desactivadas_no_envia_nada(db_session):
    from app.domain.persona_service import set_notificaciones_activas

    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    set_notificaciones_activas(db_session, ana, False)
    p = _anunciar(db_session)
    sender = ConsoleNotificationSender()

    notificar_evento(db_session, p, EstadoPaquete.RECIBIDO, sender)

    assert sender.enviados == []


def test_sin_destino_alcanzable_no_envia_nada(db_session):
    ana = get_or_create_persona(db_session, "3001234567", "Ana")
    p = _anunciar(db_session, Destinatario.solo_nombre("Carlos"))
    anonimizar_persona(db_session, ana)  # nadie queda alcanzable
    sender = ConsoleNotificationSender()

    notificar_evento(db_session, p, EstadoPaquete.RECIBIDO, sender)

    assert sender.enviados == []
