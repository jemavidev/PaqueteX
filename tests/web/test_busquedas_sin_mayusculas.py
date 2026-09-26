# -*- coding: utf-8 -*-
"""
Issue 410 (.scratch/pendientes-cliente): ninguna búsqueda distingue mayúsculas de minúsculas ni espacios de más al
inicio o al final ("La idea es que en cualquier búsqueda no distinga nunca, que sea lo mismo"). Surgió de que
`/consultar?q=za9325` no encontraba el paquete `ZA9325`. Una prueba por cada caja de búsqueda del sistema.
"""

import pytest

from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"


def _anunciar(client, tel="3001234567", nombre="Ana Maria"):
    p = announce(client.db, anunciante_telefono=tel, anunciante_nombre=nombre, destinatario=Destinatario.yo_mismo())
    client.db.commit()
    return p


def _login_admin(client):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": "admin@club.com", "password": _PW})
    return admin


def _variantes(texto):
    return [texto.lower(), texto.upper(), texto.lower().capitalize(), f"  {texto.lower()}  "]


@pytest.mark.parametrize("variante", range(4))
def test_consultar_encuentra_el_codigo_sin_importar_mayusculas(client, variante):
    p = _anunciar(client)

    r = client.get("/consultar", params={"q": _variantes(p.access_code)[variante]})

    assert r.status_code == 200 and "ANA MARIA" in r.text


@pytest.mark.parametrize("variante", range(4))
def test_consultar_encuentra_la_guia_sin_importar_mayusculas(client, variante):
    staff = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    p = _anunciar(client)
    receive(client.db, p, staff, "GUIA-XYZ-001")
    client.db.commit()

    r = client.get("/consultar", params={"q": _variantes("GUIA-XYZ-001")[variante]})

    assert r.status_code == 200 and "ANA MARIA" in r.text


@pytest.mark.parametrize("q", ["ana maria", "ANA MARIA", "  Ana Maria  "])
def test_paquetes_encuentra_por_nombre_sin_importar_mayusculas(client, q):
    _login_admin(client)
    p = _anunciar(client)

    r = client.get("/paquetes", params={"q": q, "estado": ""})

    assert p.access_code in r.text


def test_paquetes_encuentra_por_codigo_en_minusculas(client):
    _login_admin(client)
    p = _anunciar(client)

    assert p.access_code in client.get("/paquetes", params={"q": p.access_code.lower(), "estado": ""}).text


@pytest.mark.parametrize("q", ["ana maria", "ANA MARIA", "  ana maria  "])
def test_residentes_encuentra_por_nombre_sin_importar_mayusculas(client, q):
    _login_admin(client)
    _anunciar(client)

    assert "ANA MARIA" in client.get("/residentes", params={"q": q}).text
