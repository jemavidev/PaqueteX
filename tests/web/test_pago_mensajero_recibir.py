# -*- coding: utf-8 -*-
"""
Capa web — pago al mensajero desde el saldo contra entrega, en Recibir
(`.scratch/dinero-contra-entrega`, ticket 03).
"""

from app.domain.apartamento_service import resolver_apartamento, set_apartamento_actual
from app.domain.ocupante_service import agregar_ocupante
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_correccion_service import candidatos_correccion
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


def test_recibir_sin_historial_ni_apartamento_igual_habilita_la_caja(client):
    """Pedido explícito del cliente, reportado en vivo: antes esto exigía
    historial de saldo Y apartamento ya asignado a la vez -- dos candados
    que casi nunca coinciden en el caso real (contra entrega de un
    cliente nuevo). La caja (toggle) debe habilitarse para el
    destinatario de este paquete aunque no tenga ninguno de los dos.
    "Descontar del saldo de" (elegir a OTRA persona) se removió del todo
    (pedido explícito) -- el monto siempre se registra contra este mismo
    destinatario, resuelto server-side al confirmar (sin campo oculto, ver
    bug real de `test_pago_mensajero_sigue_al_destinatario_corregido_en_
    el_mismo_recibir`)."""
    _login_staff(client)
    announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.get("/paquetes")
    assert "Pago contra entrega" in r.text
    assert "Descontar del saldo de" not in r.text
    assert 'name="persona_saldo_id"' not in r.text


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


def test_pago_mensajero_sigue_al_destinatario_corregido_en_el_mismo_recibir(client):
    """Reportado en vivo por el cliente: si en el MISMO envío de Recibir el
    staff corrige el destinatario a otro Ocupante del apartamento (ej. el
    paquete llegó a nombre de quien anunció, pero en realidad es para un
    co-residente), el pago al mensajero debe descontarse del saldo del
    destinatario FINAL (Beto) -- no del que estaba resuelto cuando se abrió
    el modal (Ana, dueña del `persona_saldo_id` que trae el campo oculto)."""
    staff = _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    ana = get_or_create_persona(client.db, "3001234567", "Ana")
    set_apartamento_actual(client.db, "3001234567", apto)
    registrar_movimiento_saldo(client.db, ana.id, 5000, staff)

    beto = get_or_create_persona(client.db, "3009876543", "BETO")
    agregar_ocupante(client.db, apto, "BETO", telefono="3009876543")
    registrar_movimiento_saldo(client.db, beto.id, 5000, staff)
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    candidatos = candidatos_correccion(client.db, p)
    idx_beto = next(i for i, c in enumerate(candidatos) if c["nombre"] == "BETO")

    # `persona_saldo_id=ana.id` es el valor que el campo oculto trae porque
    # se calculó server-side cuando el modal se abrió, ANTES de que el
    # staff eligiera a Beto en `candidato_idx` -- mismo request, pero el
    # navegador nunca lo actualiza.
    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={
            "persona_saldo_id": str(ana.id),
            "monto_pagado_mensajero": "3000",
            "candidato_idx": str(idx_beto),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    paquete = client.db.get(Paquete, p.id)
    assert paquete.estado == EstadoPaquete.RECIBIDO
    assert paquete.recipient_name == "BETO"

    assert saldo_de_persona(client.db, beto.id) == 2000
    assert saldo_de_persona(client.db, ana.id) == 5000


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
    assert "Saldo pendiente: $-3,000" in r.text
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
