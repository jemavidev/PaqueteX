# -*- coding: utf-8 -*-
"""
Capa web — Cambiar posición desde el chip 📍 y el piso "00" (pedido del cliente 2026-10-31).

Seam HTTP: `POST /paquetes/{id}/posicion` (staff, solo Recibidos, mismas reglas que Recibir), a dónde vuelve, y el HTML
que lo abre (el chip como `<button>`, el modal con la grilla y la Posición actual marcada) en /paquetes y /consultar.
"""

import re
from urllib.parse import parse_qs, urlparse

from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_lifecycle import deliver, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.posicion_service import guardar_filas_activas
from app.domain.staff_service import create_initial_admin
from app.domain.usuario import Usuario

_PW = "Contrasena1"


def _staff(client):
    create_initial_admin(client.db, "staff@club.com", "Operador", _PW)
    client.db.commit()
    return client.db.query(Usuario).one()


def _login_staff(client):
    client.post("/ingresar", data={"email": "staff@club.com", "password": _PW})


def _anunciado(client, tel="3001111111", nombre="Ana"):
    p = announce(client.db, anunciante_telefono=tel, anunciante_nombre=nombre, destinatario=Destinatario.yo_mismo())
    client.db.commit()
    return p


def _recibido(client, staff, tel="3001111111", nombre="Ana", posicion="41"):
    p = _anunciado(client, tel, nombre)
    receive(client.db, p, staff, posicion=posicion)
    client.db.commit()
    return p


def _posicion(client, p):
    client.db.expire_all()
    return client.db.get(Paquete, p.id).posicion


def _modal_posicion(html, p):
    m = re.search(rf'<div id="modal-posicion-{p.id}".*?</form>', html, re.S)
    assert m, f"no hay modal Cambiar posición para {p.access_code}"
    return m.group(0)


# --------------------------------------------------------------------------- #
# La ruta
# --------------------------------------------------------------------------- #
def test_cambiar_posicion_la_guarda_y_vuelve_a_paquetes_con_sus_filtros(client):
    staff = _staff(client)
    p = _recibido(client, staff)
    _login_staff(client)

    r = client.post(
        f"/paquetes/{p.id}/posicion",
        data={"posicion": "00", "q": "Ana", "estado": "RECIBIDO"},
        follow_redirects=False,
    )

    assert r.status_code == 303
    destino = urlparse(r.headers["location"])
    assert destino.path == "/paquetes"
    assert parse_qs(destino.query) == {"q": ["Ana"], "estado": ["RECIBIDO"], "posicion_cambiada": ["00"]}
    assert _posicion(client, p) == "00"
    assert "Posición cambiada a 📍 00." in client.get(r.headers["location"]).text


def test_cambiar_posicion_desde_consultar_vuelve_a_consultar(client):
    staff = _staff(client)
    p = _recibido(client, staff)
    _login_staff(client)

    r = client.post(
        f"/paquetes/{p.id}/posicion",
        data={"posicion": "62", "origen": "consultar", "q": p.access_code},
        follow_redirects=False,
    )

    assert r.status_code == 303
    destino = urlparse(r.headers["location"])
    assert destino.path == "/consultar"
    assert parse_qs(destino.query) == {"q": [p.access_code], "posicion_cambiada": ["62"]}
    assert _posicion(client, p) == "62"
    html = client.get(r.headers["location"]).text
    assert "Posición cambiada a 📍 62." in html
    assert "📍 62" in html


def test_cambiar_posicion_invalida_o_vacia_se_rechaza_sin_efecto(client):
    staff = _staff(client)
    p = _recibido(client, staff)
    _login_staff(client)

    r = client.post(f"/paquetes/{p.id}/posicion", data={"posicion": "13"}, follow_redirects=False)
    assert r.status_code == 400
    assert "La posición «13» no existe en el estante." in r.text

    r = client.post(f"/paquetes/{p.id}/posicion", data={}, follow_redirects=False)
    assert r.status_code == 400

    assert _posicion(client, p) == "41"


def test_cambiar_a_una_fila_desactivada_se_rechaza_pero_el_piso_no(client):
    staff = _staff(client)
    p = _recibido(client, staff)
    guardar_filas_activas(client.db, {7})
    client.db.commit()
    _login_staff(client)

    r = client.post(
        f"/paquetes/{p.id}/posicion",
        data={"posicion": "62", "origen": "consultar", "q": p.access_code},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "La posición «62» está desactivada." in r.text
    assert _posicion(client, p) == "41"

    r = client.post(f"/paquetes/{p.id}/posicion", data={"posicion": "00"}, follow_redirects=False)
    assert r.status_code == 303
    assert _posicion(client, p) == "00"


def test_cambiar_posicion_de_un_paquete_no_recibido_se_rechaza(client):
    staff = _staff(client)
    anunciado = _anunciado(client, "3002222222", "Beto")
    entregado = _recibido(client, staff, "3003333333", "Caro", "41")
    deliver(client.db, entregado, staff)
    client.db.commit()
    _login_staff(client)

    for p in (anunciado, entregado):
        r = client.post(f"/paquetes/{p.id}/posicion", data={"posicion": "00"}, follow_redirects=False)
        assert r.status_code == 400
        assert "Solo se puede cambiar la posición de un paquete recibido." in r.text

    assert _posicion(client, anunciado) is None
    assert _posicion(client, entregado) == "41"


def test_cambiar_posicion_sin_sesion_de_staff_no_hace_nada(client):
    staff = _staff(client)
    p = _recibido(client, staff)

    r = client.post(f"/paquetes/{p.id}/posicion", data={"posicion": "00"}, follow_redirects=False)

    assert r.status_code in (303, 401, 403)
    assert "/paquetes?" not in r.headers.get("location", "")
    assert _posicion(client, p) == "41"


def test_el_toast_de_exito_no_repite_texto_arbitrario(client):
    _staff(client)
    _login_staff(client)

    html = client.get("/paquetes", params={"posicion_cambiada": "<b>hola</b>"}).text

    assert "Posición cambiada" not in html


# --------------------------------------------------------------------------- #
# Recibir con el piso
# --------------------------------------------------------------------------- #
def test_recibir_en_el_piso_lo_guarda_aunque_todas_las_filas_menos_una_esten_desactivadas(client):
    _staff(client)
    p = _anunciado(client)
    guardar_filas_activas(client.db, {7})
    client.db.commit()
    _login_staff(client)

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"sin_fotos": "1", "sin_fotos_motivo": "camara", "sin_apartamento_motivo": "etiqueta", "posicion": "00"},
        follow_redirects=False,
    )

    assert r.status_code == 303
    client.db.expire_all()
    recibido = client.db.get(Paquete, p.id)
    assert recibido.estado == EstadoPaquete.RECIBIDO
    assert recibido.posicion == "00"


def test_el_piso_no_aparece_en_administracion_posiciones(client):
    _staff(client)
    _login_staff(client)

    html = client.get("/administracion/posiciones").text

    filas = re.findall(r'<input type="checkbox"[^>]*name="fila"[^>]*value="(\d)"', html)
    assert filas == ["7", "6", "5", "4", "3", "2", "1"]
    assert 'value="0"' not in html and "00" not in filas


# --------------------------------------------------------------------------- #
# El HTML que abre el cambio
# --------------------------------------------------------------------------- #
def test_en_paquetes_el_chip_es_un_boton_que_abre_el_modal_con_la_grilla_y_la_posicion_marcada(client):
    staff = _staff(client)
    p = _recibido(client, staff, posicion="00")
    sin = _recibido(client, staff, "3002222222", "Beto", None)
    guardar_filas_activas(client.db, {1, 2, 3, 5, 6, 7})
    client.db.commit()
    _login_staff(client)

    html = client.get("/paquetes", params={"q": "Ana", "estado": "RECIBIDO"}).text

    # Tarjeta móvil, fila de escritorio, título del modal Ver y bloque Posición del modal Entregar.
    botones = re.findall(rf'<button type="button" data-open="modal-posicion-{p.id}"[^>]*title="Cambiar posición"', html)
    assert len(botones) == 4
    modal = _modal_posicion(html, p)
    assert f'action="/paquetes/{p.id}/posicion"' in modal
    assert re.search(r'name="posicion" value="00"[^>]*checked', modal)
    assert re.findall(r'name="posicion" value="(\d\d)"', modal)[-1] == "00"  # el piso, debajo de 12|11
    assert re.search(r'name="posicion" value="41"[^>]*disabled', modal)  # fila 4 desactivada
    assert not re.search(r'name="posicion" value="00"[^>]*disabled', modal)
    assert '<input type="hidden" name="q" value="Ana">' in modal
    assert '<input type="hidden" name="estado" value="RECIBIDO">' in modal
    # "Sin ubicación" no se puede tocar (pedido explícito): sin modal ni botón.
    assert f'modal-posicion-{sin.id}' not in client.get("/paquetes").text


def test_el_modal_cambiar_va_despues_de_ver_y_entregar_para_quedar_encima(client):
    staff = _staff(client)
    p = _recibido(client, staff)
    _login_staff(client)

    html = client.get("/paquetes").text

    cambiar = html.index(f'<div id="modal-posicion-{p.id}"')
    assert html.index(f'<div id="modal-ver-{p.id}"') < cambiar
    assert html.index(f'<div id="modal-deliver-{p.id}"') < cambiar


def test_en_consultar_el_chip_y_el_bloque_de_entregar_abren_el_modal_solo_para_staff(client):
    staff = _staff(client)
    p = _recibido(client, staff)

    publico = client.get("/consultar", params={"q": p.access_code}).text
    assert f"modal-posicion-{p.id}" not in publico

    _login_staff(client)
    html = client.get("/consultar", params={"q": p.access_code}).text

    assert len(re.findall(rf'data-open="modal-posicion-{p.id}"', html)) == 2
    modal = _modal_posicion(html, p)
    assert '<input type="hidden" name="origen" value="consultar">' in modal
    assert f'<input type="hidden" name="q" value="{p.access_code}">' in modal
    assert re.search(r'name="posicion" value="41"[^>]*checked', modal)
    assert html.index('<div id="modal-entregar-consultar"') < html.index(f'<div id="modal-posicion-{p.id}"')


def test_un_paquete_entregado_no_ofrece_cambiar_posicion(client):
    staff = _staff(client)
    p = _recibido(client, staff)
    deliver(client.db, p, staff)
    client.db.commit()
    _login_staff(client)

    assert f"modal-posicion-{p.id}" not in client.get("/paquetes", params={"estado": "ENTREGADO"}).text
    assert f"modal-posicion-{p.id}" not in client.get("/consultar", params={"q": p.access_code}).text
