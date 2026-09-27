# -*- coding: utf-8 -*-
"""
Capa web — el staff ve la Posición al buscar y entregar; el residente nunca
(`.scratch/posicion-almacenamiento`, ticket 03).
"""

import re

from app.domain.otp_sender import DevOtpSender
from app.domain.paquete_lifecycle import deliver, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin
from app.domain.telefono import normalizar_telefono
from app.domain.usuario import Usuario
from app.web.otp import get_otp_sender

_PW = "Contrasena1"


def _staff(client):
    create_initial_admin(client.db, "staff@club.com", "Operador", _PW)
    client.db.commit()
    return client.db.query(Usuario).one()


def _login_staff(client):
    client.post("/ingresar", data={"email": "staff@club.com", "password": _PW})


def _recibido(client, staff, tel, nombre, posicion):
    p = announce(client.db, anunciante_telefono=tel, anunciante_nombre=nombre, destinatario=Destinatario.yo_mismo())
    receive(client.db, p, staff, posicion=posicion)
    client.db.commit()
    return p


def _tarjeta(html, p):
    m = re.search(rf'<div[^>]*data-tarjeta-paquete="{p.id}"[^>]*>(.*?)<!-- /tarjeta -->', html, re.S)
    assert m, f"no hay tarjeta móvil para {p.access_code}"
    return m.group(0)


def _fila(html, p):
    """La fila `<tr>` de escritorio del paquete (la que abre su modal Ver)."""
    for fila in re.findall(r"<tr class=.*?</tr>", html, re.S):
        if f'data-open="modal-ver-{p.id}"' in fila:
            return fila
    raise AssertionError(f"no hay fila de escritorio para {p.access_code}")


def _modal_entregar(html, p):
    m = re.search(rf'<div id="modal-deliver-{p.id}".*?</form>', html, re.S)
    assert m, f"no hay modal Entregar para {p.access_code}"
    return m.group(0)


def test_el_listado_muestra_la_posicion_solo_de_los_recibidos_que_la_tienen(client):
    staff = _staff(client)
    con = _recibido(client, staff, "3001111111", "Ana", "41")
    sin = _recibido(client, staff, "3002222222", "Beto", None)
    entregado = _recibido(client, staff, "3003333333", "Caro", "62")
    deliver(client.db, entregado, staff)
    client.db.commit()
    _login_staff(client)

    html = client.get("/paquetes", params={"estado": ""}).text

    assert "📍 41" in _tarjeta(html, con)
    assert "📍 41" in _fila(html, con)
    for p in (sin, entregado):
        assert "📍" not in _tarjeta(html, p)
        assert "📍" not in _fila(html, p)


def test_el_modal_entregar_destaca_la_posicion_o_dice_sin_ubicacion(client):
    staff = _staff(client)
    con = _recibido(client, staff, "3001111111", "Ana", "41")
    sin = _recibido(client, staff, "3002222222", "Beto", None)
    _login_staff(client)

    html = client.get("/paquetes").text

    assert re.search(r"Posición\s*<[^>]*>\s*41\s*<", _modal_entregar(html, con))
    assert "Sin ubicación" in _modal_entregar(html, sin)


def test_el_modal_entregar_de_consultar_tambien_la_muestra(client):
    staff = _staff(client)
    con = _recibido(client, staff, "3001111111", "Ana", "41")
    sin = _recibido(client, staff, "3002222222", "Beto", None)
    _login_staff(client)

    html_con = client.get("/consultar", params={"q": con.access_code}).text
    html_sin = client.get("/consultar", params={"q": sin.access_code}).text

    assert re.search(r"Posición\s*<[^>]*>\s*41\s*<", html_con)
    assert "Sin ubicación" in html_sin


def test_el_residente_nunca_ve_la_posicion(client):
    staff = _staff(client)
    p = _recibido(client, staff, "3001234567", "Ana", "41")

    # /consultar público (sin sesión).
    publico = client.get("/consultar", params={"q": p.access_code}).text
    assert "📍" not in publico and "Posición" not in publico and "Sin ubicación" not in publico

    # /mis-paquetes (sesión de residente por OTP), con su línea de tiempo.
    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    client.post("/otp/solicitar", data={"telefono": "3001234567"})
    client.post("/otp/verificar", data={"telefono": "3001234567", "codigo": sender.enviados[normalizar_telefono("3001234567")]})
    mis = client.get("/mis-paquetes").text
    assert p.access_code in mis
    assert "📍" not in mis and "Posición" not in mis


def test_la_linea_de_tiempo_no_muestra_la_posicion(client):
    staff = _staff(client)
    p = _recibido(client, staff, "3001111111", "Ana", "41")
    _login_staff(client)

    timeline = client.get(f"/paquetes/{p.id}/timeline").text

    assert "Recibido" in timeline
    assert "📍" not in timeline and "Posición" not in timeline
