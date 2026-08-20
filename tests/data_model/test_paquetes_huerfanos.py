# -*- coding: utf-8 -*-
"""
Seam A — detección de Paquetes huérfanos (Anunciados sin Apartamento en su
snapshot) de un Teléfono dado. Ver
.scratch/asociacion-retroactiva-apartamento/spec.md e issues/01.

Comportamiento observable: trae solo Anunciados sin snapshot de apartamento
para ese teléfono (como anunciante o destinatario); nunca trae paquetes ya
avanzados de estado ni paquetes con apartamento ya resuelto.
"""

from app.domain.apartamento_service import resolver_apartamento, set_apartamento_actual
from app.domain.paquete_lifecycle import cancel, deliver, receive
from app.domain.paquete_service import (
    Destinatario,
    announce,
    paquetes_sin_apartamento_de_telefono,
)
from app.domain.persona_service import get_or_create_persona
from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"


def _staff(session):
    return create_initial_admin(session, "admin@club.com", "Admin", _PW)


def test_telefono_sin_ningun_paquete_devuelve_lista_vacia(db_session):
    assert paquetes_sin_apartamento_de_telefono(db_session, "+573001234567") == []


def test_anunciante_anunciado_sin_apartamento_es_huerfano(db_session):
    p = announce(
        db_session,
        anunciante_telefono="3001234567",
        anunciante_nombre="Jesus Peres",
        destinatario=Destinatario.yo_mismo(),
    )
    db_session.flush()

    assert paquetes_sin_apartamento_de_telefono(db_session, p.announced_by_phone) == [p]


def test_destinatario_registrado_sin_apartamento_es_huerfano(db_session):
    # Persona 3001234567 existe (por ejemplo, ya recibio un paquete antes)
    # pero SIN anunciar nada ella misma -- asi el unico Paquete en juego es
    # el que la nombra como Destinatario.
    get_or_create_persona(db_session, "3001234567", "Jesus Peres")
    p = announce(
        db_session,
        anunciante_telefono="3009999999",
        anunciante_nombre="Otro Anunciante",
        destinatario=Destinatario.persona_registrada("3001234567"),
    )
    db_session.flush()

    assert paquetes_sin_apartamento_de_telefono(db_session, "+573001234567") == [p]


def test_paquete_con_apartamento_ya_resuelto_no_es_huerfano(db_session):
    apto = resolver_apartamento(db_session, "TORRE 1", "101")
    announce(db_session, "3001234567", "Jesus Peres", Destinatario.yo_mismo())
    set_apartamento_actual(db_session, "3001234567", apto)
    p2 = announce(db_session, "3001234567", "Jesus Peres", Destinatario.yo_mismo())
    db_session.flush()

    # El primero (sin apto en el momento de anunciar) sigue siendo huerfano;
    # el segundo (anunciado ya con apto resuelto) no lo es.
    assert p2.snapshot_apartamento == "101"
    huerfanos = paquetes_sin_apartamento_de_telefono(db_session, "+573001234567")
    assert p2 not in huerfanos


def test_paquete_recibido_sin_apartamento_no_es_huerfano(db_session):
    staff = _staff(db_session)
    p = announce(db_session, "3001234567", "Jesus Peres", Destinatario.yo_mismo())
    receive(db_session, p, staff)

    assert paquetes_sin_apartamento_de_telefono(db_session, "+573001234567") == []


def test_paquete_entregado_sin_apartamento_no_es_huerfano(db_session):
    staff = _staff(db_session)
    p = announce(db_session, "3001234567", "Jesus Peres", Destinatario.yo_mismo())
    receive(db_session, p, staff)
    deliver(db_session, p, staff)

    assert paquetes_sin_apartamento_de_telefono(db_session, "+573001234567") == []


def test_paquete_cancelado_sin_apartamento_no_es_huerfano(db_session):
    staff = _staff(db_session)
    p = announce(db_session, "3001234567", "Jesus Peres", Destinatario.yo_mismo())
    cancel(db_session, p, staff, "OTRO")

    assert paquetes_sin_apartamento_de_telefono(db_session, "+573001234567") == []
