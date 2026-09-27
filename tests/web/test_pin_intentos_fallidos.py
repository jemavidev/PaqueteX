# -*- coding: utf-8 -*-
"""
Capa web — 5 PIN fallidos → contraseña → cambio de PIN, con aviso al ADMIN (`.scratch/pin-operador-dispositivo`,
ticket 07).

El PIN es único y no se elige Usuario, así que un PIN equivocado no se puede atribuir a nadie: el contador es del
equipo. Al quinto fallo seguido, el equipo deja de aceptar PIN (ni uno correcto) hasta que alguien entre con su
contraseña, y ese alguien pasa obligatoriamente a cambiar su PIN (puede dejar el mismo).
"""

import pytest

from app.domain import operador_dispositivo_service as ods
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

pytestmark = pytest.mark.pin_manual

_PW = "Contrasena1"


def _sembrar(client):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    ana = create_staff(client.db, admin, "ana@club.com", "Ana", _PW, RolUsuario.OPERADOR)
    ods.definir_pin(client.db, admin, "1000")
    ods.definir_pin(client.db, ana, "1111")
    client.db.commit()
    return admin, ana


def _ingresar(client, email):
    return client.post("/ingresar", data={"email": email, "password": _PW}, follow_redirects=False)


def _pin(client, pin):
    return client.post("/bloqueo", data={"pin": pin, "siguiente": "/paquetes"}, follow_redirects=False)


def _equipo_bloqueado_con_ana(client):
    _sembrar(client)
    _ingresar(client, "ana@club.com")
    client.post("/bloquear")


def test_cuatro_fallos_siguen_permitiendo_el_pin(client):
    _equipo_bloqueado_con_ana(client)
    for _ in range(4):
        assert _pin(client, "9999").status_code == 400
    assert _pin(client, "1111").headers["location"] == "/paquetes"


def test_el_quinto_fallo_exige_contrasena_aunque_despues_llegue_el_pin_correcto(client):
    _equipo_bloqueado_con_ana(client)
    for _ in range(4):
        _pin(client, "9999")
    r = _pin(client, "9999")
    assert r.status_code == 303
    assert r.headers["location"] == "/ingresar?aviso=intentos"
    assert "Demasiados PIN incorrectos" in client.get(r.headers["location"]).text

    r = _pin(client, "1111")
    assert r.headers["location"] == "/ingresar?aviso=intentos"
    assert client.get("/bloqueo", follow_redirects=False).headers["location"] == "/ingresar"
    assert client.get("/paquetes", follow_redirects=False).headers["location"].endswith("/ingresar")


def test_la_capa_recibe_la_orden_de_ir_a_contrasena(client):
    _equipo_bloqueado_con_ana(client)
    for _ in range(4):
        _pin(client, "9999")
    r = client.post("/bloqueo", data={"pin": "9999"}, headers={"Accept": "application/json"})
    assert r.status_code == 400
    assert r.json()["destino"] == "/ingresar?aviso=intentos"


def test_tras_la_contrasena_obliga_a_cambiar_el_pin_y_acepta_el_mismo(client):
    _equipo_bloqueado_con_ana(client)
    for _ in range(5):
        _pin(client, "9999")

    r = _ingresar(client, "ana@club.com")
    assert r.headers["location"] == "/mi-pin"
    assert client.get("/paquetes", follow_redirects=False).headers["location"] == "/mi-pin"
    assert "Cambia tu PIN" in client.get("/mi-pin").text

    r = client.post("/mi-pin", data={"pin": "1111", "pin_confirmacion": "1111"}, follow_redirects=False)
    assert r.headers["location"] == "/paquetes"
    assert client.get("/paquetes", follow_redirects=False).status_code == 200

    client.post("/bloquear")
    assert _pin(client, "1111").headers["location"] == "/paquetes"  # el equipo vuelve a aceptar PIN


def test_un_acierto_intermedio_pone_el_contador_en_cero(client):
    _equipo_bloqueado_con_ana(client)
    for _ in range(4):
        _pin(client, "9999")
    _pin(client, "1111")
    client.post("/bloquear")
    for _ in range(4):
        assert _pin(client, "9999").status_code == 400
    assert _pin(client, "1111").headers["location"] == "/paquetes"


def test_el_bloqueo_por_intentos_aparece_en_el_aviso_del_admin_y_no_para_el_operador(client):
    _equipo_bloqueado_con_ana(client)
    for _ in range(5):
        _pin(client, "9999")

    _ingresar(client, "admin@club.com")
    client.post("/mi-pin", data={"pin": "1000", "pin_confirmacion": "1000"})  # quien entra tras el bloqueo, cambia
    texto = client.get("/administracion/personal").text
    assert "Bloqueos por PIN incorrecto" in texto

    _ingresar(client, "ana@club.com")
    assert client.get("/administracion/personal", follow_redirects=False).status_code == 403


def test_el_cambio_obligatorio_no_se_salta_bloqueando_el_equipo(client):
    """Hallazgo de la revisión: el cambio pendiente vivía solo en la sesión y un Bloqueo lo borraba, así que el PIN
    viejo (posiblemente expuesto) volvía a desbloquear. Ahora vive en la BD y ese PIN no desbloquea hasta cambiarlo."""
    _equipo_bloqueado_con_ana(client)
    for _ in range(5):
        _pin(client, "9999")
    _ingresar(client, "ana@club.com")  # queda en "Cambia tu PIN"...

    client.post("/bloquear")  # ...pero en vez de cambiarlo, bloquea
    r = _pin(client, "1111")
    assert r.headers["location"] == "/ingresar?aviso=cambiar-pin"  # el PIN viejo no desbloquea

    _ingresar(client, "ana@club.com")
    assert client.get("/paquetes", follow_redirects=False).headers["location"] == "/mi-pin"
    client.post("/mi-pin", data={"pin": "1111", "pin_confirmacion": "1111"})
    client.post("/bloquear")
    assert _pin(client, "1111").headers["location"] == "/paquetes"
