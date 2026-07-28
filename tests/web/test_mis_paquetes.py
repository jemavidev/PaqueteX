# -*- coding: utf-8 -*-
"""
Capa web — `/mis-paquetes` (historial del cliente, Grupo 10 de la Ronda 2).

Comportamiento observable: exige sesión de cliente; lista paquetes donde su
teléfono es Anunciante O Destinatario; cada fila enlaza a `/consultar` por
`access_code`.
"""

from app.domain.otp_sender import DevOtpSender
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.web.otp import get_otp_sender

_CANON = "+573001234567"


def _login_cliente(client, telefono="3001234567"):
    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    client.post("/otp/solicitar", data={"telefono": telefono})
    codigo = sender.enviados[_CANON]
    client.post("/otp/verificar", data={"telefono": telefono, "codigo": codigo})
    return client.db.query(Persona).filter(Persona.telefono == _CANON).one()


def test_sin_sesion_redirige_a_login_de_cliente(client):
    r = client.get("/mis-paquetes", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/otp")


def test_lista_paquetes_anunciados_por_el_cliente(client):
    # Anuncia ANTES de loguearse -- así la Persona nace con nombre "Ana"
    # (get_or_create_persona no sobreescribe el nombre de una ya existente).
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    _login_cliente(client)

    r = client.get("/mis-paquetes")
    assert r.status_code == 200
    assert "Ana" in r.text
    assert f"/consultar?q={p.access_code}" in r.text


def test_lista_paquetes_donde_es_destinatario_aunque_no_haya_anunciado(client):
    from app.domain.persona_service import update_datos_personales

    persona = _login_cliente(client)
    update_datos_personales(client.db, persona, nombre="Ana")
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3019999999",
        anunciante_nombre="Portero",
        destinatario=Destinatario.persona_registrada("3001234567"),
    )
    client.db.commit()

    r = client.get("/mis-paquetes")
    assert r.status_code == 200
    assert "Ana" in r.text  # el nombre mostrado es el del destinatario, no el anunciante
    assert f"/consultar?q={p.access_code}" in r.text


def test_no_muestra_paquetes_de_otro_telefono(client):
    _login_cliente(client)
    announce(
        client.db,
        anunciante_telefono="3019999999",
        anunciante_nombre="Otro",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.get("/mis-paquetes")
    assert r.status_code == 200
    assert "Otro" not in r.text


def test_sin_paquetes_muestra_mensaje_vacio(client):
    _login_cliente(client)
    r = client.get("/mis-paquetes")
    assert r.status_code == 200
    assert "no tenés ningún paquete" in r.text.lower()
