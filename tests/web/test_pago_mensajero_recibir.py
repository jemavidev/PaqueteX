# -*- coding: utf-8 -*-
"""
Capa web — pago al mensajero desde el saldo contra entrega, en Recibir
(`.scratch/dinero-contra-entrega`, ticket 03).
"""

from app.domain.apartamento_service import resolver_apartamento, set_apartamento_actual
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona_service import get_or_create_persona
from app.domain.saldo_contra_entrega import MovimientoSaldoContraEntrega
from app.domain.saldo_contra_entrega_service import registrar_movimiento_saldo, saldo_de_persona
from app.domain.staff_service import create_initial_admin
from app.domain.usuario import Usuario

_PW = "Contrasena1"


def _login_staff(client, email="staff@club.com"):
    create_initial_admin(client.db, email, "Staff", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return client.db.query(Usuario).filter(Usuario.email == email).one()


def test_recibir_sin_historial_no_muestra_el_selector(client):
    _login_staff(client)
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert "Pago contra entrega" not in r.text


def test_recibir_con_historial_muestra_el_selector(client):
    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    persona = get_or_create_persona(client.db, "3001234567", "Ana")
    set_apartamento_actual(client.db, "3001234567", apto)
    registrar_movimiento_saldo(client.db, persona.id, 5000, staff)
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert "Pago contra entrega" in r.text


def test_confirmar_pago_crea_movimiento_negativo_y_recibe(client):
    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    persona = get_or_create_persona(client.db, "3001234567", "Ana")
    set_apartamento_actual(client.db, "3001234567", apto)
    registrar_movimiento_saldo(client.db, persona.id, 5000, staff)
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"persona_saldo_id": str(persona.id), "monto_pagado_mensajero": "3000"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.RECIBIDO
    assert saldo_de_persona(client.db, persona.id) == 2000
    mov = (
        client.db.query(MovimientoSaldoContraEntrega)
        .filter(MovimientoSaldoContraEntrega.paquete_id == p.id)
        .one()
    )
    assert mov.monto == -3000


def test_saldo_puede_quedar_en_negativo_sin_bloquear_el_pago(client):
    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    persona = get_or_create_persona(client.db, "3001234567", "Ana")
    set_apartamento_actual(client.db, "3001234567", apto)
    registrar_movimiento_saldo(client.db, persona.id, 1000, staff)
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"persona_saldo_id": str(persona.id), "monto_pagado_mensajero": "5000"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.RECIBIDO
    assert saldo_de_persona(client.db, persona.id) == -4000


def test_recibir_sin_completar_el_selector_no_crea_movimiento(client):
    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    persona = get_or_create_persona(client.db, "3001234567", "Ana")
    set_apartamento_actual(client.db, "3001234567", apto)
    registrar_movimiento_saldo(client.db, persona.id, 5000, staff)
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.post(f"/paquetes/{p.id}/recibir", follow_redirects=False)
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.RECIBIDO
    assert saldo_de_persona(client.db, persona.id) == 5000


# --------------------------------------------------------------------------- #
# "Saldo: $X" debajo del destinatario, en Recibir (pedido explícito del
# cliente) -- verde a favor, rojo en contra.
# --------------------------------------------------------------------------- #
def test_recibir_muestra_saldo_a_favor_en_verde(client):
    staff = _login_staff(client)
    persona = get_or_create_persona(client.db, "3001234567", "Ana")
    registrar_movimiento_saldo(client.db, persona.id, 5000, staff)
    client.db.commit()

    announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert "Saldo: $5,000" in r.text
    assert "text-emerald-600" in r.text


def test_recibir_muestra_saldo_en_contra_en_rojo(client):
    staff = _login_staff(client)
    persona = get_or_create_persona(client.db, "3001234567", "Ana")
    registrar_movimiento_saldo(client.db, persona.id, -3000, staff)
    client.db.commit()

    announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert "Saldo: $-3,000" in r.text
    assert "text-red-600" in r.text


def test_recibir_sin_saldo_no_muestra_la_linea(client):
    _login_staff(client)
    announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert "Saldo: $" not in r.text
