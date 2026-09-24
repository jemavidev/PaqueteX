# -*- coding: utf-8 -*-
"""
Saldo contra entrega: el destinatario nunca se adivina por un nombre repetido (issue 393, `.scratch/pendientes-cliente`).

Antes, sin teléfono que lo identificara, el destinatario se buscaba por NOMBRE tomando la primera coincidencia: con dos
personas del mismo nombre, el pago al mensajero (Recibir) o el abono (Entregar) podía registrarse a la equivocada.
"""

import sqlalchemy as sa

from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona_service import get_or_create_persona, get_or_create_persona_por_whatsapp
from app.domain.staff_service import create_initial_admin
from app.domain.usuario import Usuario

_PW = "Contrasena1"


def _login(client):
    create_initial_admin(client.db, "staff@club.com", "Operador", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": "staff@club.com", "password": _PW})
    return client.db.query(Usuario).one()


def _movimientos(client):
    client.db.expire_all()
    return client.db.execute(sa.text("select persona_id, monto from movimientos_saldo_contra_entrega")).all()


def _recibir_con_pago_al_mensajero(client, p, monto=20000):
    return client.post(
        f"/paquetes/{p.id}/recibir", data={"monto_pagado_mensajero": str(monto)}, follow_redirects=False
    )


def test_con_dos_personas_del_mismo_nombre_no_se_le_carga_el_pago_a_ninguna(client):
    _login(client)
    get_or_create_persona(client.db, "3001111111", "Juan Perez")
    get_or_create_persona(client.db, "3002222222", "Juan Perez")
    p = announce(client.db, anunciante_telefono="3009999999", anunciante_nombre="Anunciante Externo",
                 destinatario=Destinatario.solo_nombre("Juan Perez"))
    client.db.commit()

    r = _recibir_con_pago_al_mensajero(client, p)

    assert r.status_code == 303  # recibir no falla por esto
    assert _movimientos(client) == []  # antes: a uno de los dos Juan Perez, al azar


def test_con_un_nombre_unico_el_pago_se_le_carga_a_esa_persona(client):
    _login(client)
    unica = get_or_create_persona(client.db, "3001111111", "Juan Perez")
    p = announce(client.db, anunciante_telefono="3009999999", anunciante_nombre="Anunciante Externo",
                 destinatario=Destinatario.solo_nombre("Juan Perez"))
    client.db.commit()

    _recibir_con_pago_al_mensajero(client, p)

    assert _movimientos(client) == [(unica.id, -20000)]


def test_un_destinatario_solo_whatsapp_se_resuelve_por_su_whatsapp_aunque_su_nombre_se_repita(client):
    """El WhatsApp propio del destinatario (`recipient_whatsapp`) es su identidad exacta: gana sobre el nombre."""
    _login(client)
    get_or_create_persona(client.db, "3001111111", "Juan Perez")  # homónimo con teléfono
    p = announce(client.db, anunciante_whatsapp="juan.wa", anunciante_nombre="Juan Perez",
                 destinatario=Destinatario.yo_mismo())
    client.db.commit()
    duenio = get_or_create_persona_por_whatsapp(client.db, "juan.wa", "Juan Perez")

    _recibir_con_pago_al_mensajero(client, p)

    assert _movimientos(client) == [(duenio.id, -20000)]


def test_la_caja_de_pago_al_mensajero_no_aparece_si_el_destinatario_es_ambiguo(client):
    _login(client)
    get_or_create_persona(client.db, "3001111111", "Juan Perez")
    get_or_create_persona(client.db, "3002222222", "Juan Perez")
    p = announce(client.db, anunciante_telefono="3009999999", anunciante_nombre="Anunciante Externo",
                 destinatario=Destinatario.solo_nombre("Juan Perez"))
    client.db.commit()

    html = client.get("/paquetes", params={"q": p.access_code}).text

    assert f'id="monto-mensajero-{p.id}"' not in html
