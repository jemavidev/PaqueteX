# -*- coding: utf-8 -*-
"""
Capa web — `/ayuda`, `/terminos`, `/privacidad`, `/cookies`, `/como-funciona`
leen `ConfiguracionEmpresa`/`ConfiguracionConjunto` en vivo (grilling
2026-09-18, issue 350, `.scratch/pendientes-cliente/spec.md`) -- antes eran
estáticas con los datos de la empresa hardcodeados en cada plantilla.
"""

import pytest

from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"


def _login_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


@pytest.mark.parametrize("ruta", ["/ayuda", "/terminos", "/privacidad", "/cookies", "/como-funciona"])
def test_pagina_publica_sin_sesion_responde_200(client, ruta):
    r = client.get(ruta, follow_redirects=False)
    assert r.status_code == 200


@pytest.mark.parametrize("ruta", ["/terminos", "/privacidad"])
def test_pagina_publica_refleja_razon_social_editada_por_admin(client, ruta):
    """`/terminos` y `/privacidad` son las únicas 2 que mencionan la razón
    social como texto corrido ("operado por X" / "Responsable del
    Tratamiento") -- `/ayuda` y `/cookies` solo muestran datos de contacto,
    cubierto en el test de abajo."""
    _login_admin(client)
    r = client.post(
        "/administracion/conjunto/empresa",
        data={
            "razon_social": "Otra Razón Social S.A.S.",
            "nit": "900999888-1",
            "direccion": "Calle Nueva #1-1",
            "email_contacto": "nuevo@correo.com",
            "telefono_contacto": "3009999999",
        },
    )
    assert r.status_code == 200
    client.db.expire_all()

    r = client.get(ruta)
    assert r.status_code == 200
    assert "Papyrus Soluciones Integrales S.A.S." not in r.text
    assert "Otra Razón Social S.A.S." in r.text


@pytest.mark.parametrize("ruta", ["/ayuda", "/terminos", "/privacidad", "/cookies"])
def test_pagina_publica_refleja_contacto_editado_por_admin(client, ruta):
    _login_admin(client)
    r = client.post(
        "/administracion/conjunto/empresa",
        data={
            "razon_social": "Papyrus Soluciones Integrales S.A.S.",
            "nit": "900999888-1",
            "direccion": "Calle Nueva #1-1",
            "email_contacto": "nuevo@correo.com",
            "telefono_contacto": "3009999999",
        },
    )
    assert r.status_code == 200
    client.db.expire_all()

    r = client.get(ruta)
    assert r.status_code == 200
    assert "paquetex@papyrus.com.co" not in r.text
    assert "nuevo@correo.com" in r.text


def test_ayuda_refleja_horario_editado_por_admin(client):
    _login_admin(client)
    r = client.post(
        "/administracion/conjunto",
        data={"nombre": "El Club", "horario_lunes_viernes": "7:00 AM - 9:00 PM"},
    )
    assert r.status_code == 200
    client.db.expire_all()

    r = client.get("/ayuda")
    assert r.status_code == 200
    assert "7:00 AM - 9:00 PM" in r.text
