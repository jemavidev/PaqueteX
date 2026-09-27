# -*- coding: utf-8 -*-
"""
Capa web — Administración → Posiciones (issue 416, `.scratch/pendientes-cliente`).

El ADMIN activa/desactiva filas del estante; las Posiciones de una fila desactivada salen deshabilitadas en el modal
Recibir y la ruta de Recibir las rechaza.
"""

import re

from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.posicion_service import filas_desactivadas, guardar_filas_activas
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"
_URL = "/administracion/posiciones"


def _login_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _desactivadas(client):
    client.db.expire_all()
    return filas_desactivadas(client.db)


def test_operador_no_puede_ver_ni_cambiar_las_posiciones(client):
    _login_operador(client)

    assert client.get(_URL).status_code == 403
    assert client.post(_URL, data={"fila": ["1"]}).status_code == 403


def test_la_pantalla_lista_las_7_filas_de_arriba_hacia_abajo_con_su_estado(client):
    guardar_filas_activas(client.db, {1, 2, 3, 5, 6, 7})
    client.db.commit()
    _login_admin(client)

    html = client.get(_URL).text

    toggles = re.findall(r'<input type="checkbox"[^>]*name="fila"[^>]*>', html)
    assert [re.search(r'value="(\d)"', t).group(1) for t in toggles] == ["7", "6", "5", "4", "3", "2", "1"]
    marcadas = {re.search(r'value="(\d)"', t).group(1) for t in toggles if "checked" in t}
    assert marcadas == {"7", "6", "5", "3", "2", "1"}
    assert 'href="/administracion/posiciones"' in html  # enlace en el menú de Administración


def test_guardar_desactiva_las_filas_no_marcadas(client):
    _login_admin(client)

    r = client.post(_URL, data={"fila": ["3", "4", "5", "6", "7"]})

    assert r.status_code == 200
    assert "Posiciones guardadas." in r.text
    assert _desactivadas(client) == frozenset({1, 2})


def test_no_se_puede_guardar_sin_ninguna_fila_activa(client):
    guardar_filas_activas(client.db, {1, 2, 3, 4, 5, 6})
    client.db.commit()
    _login_admin(client)

    r = client.post(_URL, data={})

    assert r.status_code == 400
    assert "Debe quedar al menos una fila activa" in r.text
    assert _desactivadas(client) == frozenset({7})


def _anunciar(client):
    p = announce(client.db, anunciante_telefono="3001234567", anunciante_nombre="Ana",
                 destinatario=Destinatario.yo_mismo())
    client.db.commit()
    return p


def test_el_modal_recibir_deshabilita_las_posiciones_de_las_filas_desactivadas(client):
    guardar_filas_activas(client.db, {1, 2, 3, 5, 6, 7})
    client.db.commit()
    _login_admin(client)
    p = _anunciar(client)

    for ruta in ["/paquetes", f"/consultar?q={p.access_code}"]:
        html = client.get(ruta).text
        radios = dict(re.findall(r'<input type="radio" name="posicion" value="(\d\d)"([^>]*)>', html))
        assert "disabled" in radios["41"] and "disabled" in radios["42"], ruta
        assert all("disabled" not in attrs for codigo, attrs in radios.items() if codigo[0] != "4"), ruta


def test_recibir_en_una_fila_desactivada_se_rechaza_sin_efecto(client):
    guardar_filas_activas(client.db, {1, 2, 3, 5, 6, 7})
    client.db.commit()
    _login_admin(client)
    p = _anunciar(client)

    r = client.post(f"/paquetes/{p.id}/recibir", data={"posicion": "41"}, follow_redirects=False)

    assert r.status_code == 400
    assert "La posición «41» está desactivada." in r.text
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.ANUNCIADO
