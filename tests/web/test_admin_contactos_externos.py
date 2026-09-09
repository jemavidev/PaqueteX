# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/contactos-externos` (`.scratch/contactos-externos`,
ticket 03). Solo lectura, exclusiva de admin, buscador + paginación simple.
"""

from app.domain.contacto_externo_service import (
    FUENTE_GOOGLE_CONTACTS,
    FilaFuenteContacto,
    importar_contactos_externos,
)
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"


def _login_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _sembrar(client, filas):
    importar_contactos_externos(client.db, filas)
    client.db.commit()


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/administracion/contactos-externos", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/contactos-externos")
    assert r.status_code == 403


def test_admin_lista_los_contactos(client):
    _login_admin(client)
    _sembrar(
        client,
        [FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS)],
    )

    r = client.get("/administracion/contactos-externos")
    assert r.status_code == 200
    assert "Juan Perez" in r.text


def test_buscar_por_nombre_encuentra_el_contacto(client):
    _login_admin(client)
    _sembrar(
        client,
        [
            FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001111111",), fuente=FUENTE_GOOGLE_CONTACTS),
            FilaFuenteContacto(nombre="Ana Gomez", telefonos=("3002222222",), fuente=FUENTE_GOOGLE_CONTACTS),
        ],
    )

    r = client.get("/administracion/contactos-externos", params={"q": "Juan"})
    assert "Juan Perez" in r.text
    assert "Ana Gomez" not in r.text


def test_buscar_por_telefono_en_cualquier_formato_encuentra_el_contacto(client):
    _login_admin(client)
    _sembrar(
        client,
        [FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS)],
    )

    r = client.get("/administracion/contactos-externos", params={"q": "+57 300 123 4567"})
    assert "Juan Perez" in r.text


def test_sin_whatsapp_no_muestra_columna_forzada(client):
    _login_admin(client)
    _sembrar(
        client,
        [FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS)],
    )

    r = client.get("/administracion/contactos-externos")
    assert "None" not in r.text
