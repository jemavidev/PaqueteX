# -*- coding: utf-8 -*-
"""
Capa web — registro del dispositivo y PIN obligatorio al ingresar (`.scratch/pin-operador-dispositivo`, ticket 02).

Entrar con contraseña registra el equipo (cookie propia, distinta de la de sesión) para ese Usuario. Un Usuario sin PIN
queda restringido a "Crea tu PIN" hasta que lo cree. El PIN es de 4 dígitos, único entre Usuarios, y tras 3 rechazos
por "ya existe" en una hora no se aceptan más intentos. El registro vence según los días configurados.

Estas pruebas crean los PIN a mano (`pin_manual`): el arnés le asigna uno automático a todo Usuario nuevo del resto de
la suite.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain import operador_dispositivo_service as ods
from app.domain.configuracion_conjunto_service import actualizar_seguridad_sesion
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.pin_manual

_PW = "Contrasena1"


def _sembrar(client):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    op = create_staff(client.db, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    return admin, op


def _ingresar(cliente_http, email):
    return cliente_http.post("/ingresar", data={"email": email, "password": _PW}, follow_redirects=False)


def _crear_pin(cliente_http, pin, confirmacion=None):
    return cliente_http.post(
        "/mi-pin", data={"pin": pin, "pin_confirmacion": confirmacion or pin}, follow_redirects=False
    )


def test_ingresar_sin_pin_lleva_a_crear_el_pin_y_bloquea_las_demas_vistas(client):
    _sembrar(client)
    r = _ingresar(client, "op@club.com")
    assert r.status_code == 303
    assert r.headers["location"] == "/mi-pin"
    assert "paquetex_dispositivo" in client.cookies

    r = client.get("/paquetes", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/mi-pin"

    r = client.get("/mi-pin")
    assert r.status_code == 200
    assert "Crea tu PIN" in r.text


def test_crear_el_pin_deja_entrar_y_no_lo_guarda_en_claro(client):
    _, op = _sembrar(client)
    _ingresar(client, "op@club.com")
    r = _crear_pin(client, "4821")
    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes"
    assert client.get("/paquetes", follow_redirects=False).status_code == 200

    client.db.expire_all()
    guardado = client.db.get(Usuario, op.id)
    assert guardado.pin_huella
    assert "4821" not in guardado.pin_huella


def test_con_pin_ingresar_va_directo_a_paquetes(client):
    _, op = _sembrar(client)
    ods.definir_pin(client.db, op, "4821")
    client.db.commit()
    r = _ingresar(client, "op@club.com")
    assert r.headers["location"] == "/paquetes"


def test_pin_invalido_o_sin_confirmar_se_rechaza(client):
    _sembrar(client)
    _ingresar(client, "op@club.com")
    for pin, confirmacion in [("123", "123"), ("12345", "12345"), ("12a4", "12a4"), ("", ""), ("1234", "1243")]:
        r = _crear_pin(client, pin, confirmacion)
        assert r.status_code == 400, pin
    assert client.get("/paquetes", follow_redirects=False).headers["location"] == "/mi-pin"


def test_pin_de_otro_usuario_se_rechaza_y_el_cuarto_intento_en_una_hora_se_bloquea(client):
    admin, _ = _sembrar(client)
    ods.definir_pin(client.db, admin, "1111")
    otro = create_staff(client.db, admin, "otro@club.com", "Otro", _PW, RolUsuario.OPERADOR)
    ods.definir_pin(client.db, otro, "2222")
    client.db.commit()

    _ingresar(client, "op@club.com")
    for pin in ("1111", "2222", "1111"):
        r = _crear_pin(client, pin)
        assert r.status_code == 400
        assert "no está disponible" in r.text

    r = _crear_pin(client, "7777")  # libre, pero ya agotó sus 3 rechazos de la hora
    assert r.status_code == 429
    assert "Demasiados intentos" in r.text

    ahora = datetime.now(timezone.utc)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ods, "_ahora", lambda: ahora + timedelta(hours=1, minutes=1))
        assert _crear_pin(client, "7777").status_code == 303


def test_el_registro_vence_segun_los_dias_configurados(client):
    """Tras días sin uso, el Bloqueo por inactividad ya actuó: con registro vigente el equipo pide el PIN
    (`/bloqueo`); con registro vencido, usuario y contraseña (`/ingresar`)."""
    admin, op = _sembrar(client)
    ods.definir_pin(client.db, op, "4821")
    actualizar_seguridad_sesion(client.db, segundos_inactividad=300, dias_registro_dispositivo=3, actor=admin)
    client.db.commit()
    _ingresar(client, "op@club.com")

    ahora = datetime.now(timezone.utc)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ods, "_ahora", lambda: ahora + timedelta(days=2, hours=23))
        assert client.get("/paquetes", follow_redirects=False).headers["location"].startswith("/bloqueo")
        assert client.post("/bloqueo", data={"pin": "4821"}, follow_redirects=False).status_code == 303
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ods, "_ahora", lambda: ahora + timedelta(days=3, minutes=1))
        r = client.get("/paquetes", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"].endswith("/ingresar")


def test_acortar_los_dias_vence_los_registros_viejos_en_la_siguiente_peticion(client):
    admin, op = _sembrar(client)
    ods.definir_pin(client.db, op, "4821")
    client.db.commit()
    _ingresar(client, "op@club.com")

    ahora = datetime.now(timezone.utc)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ods, "_ahora", lambda: ahora + timedelta(days=5))
        assert client.post("/bloqueo", data={"pin": "4821"}, follow_redirects=False).status_code == 303
        assert client.get("/paquetes", follow_redirects=False).status_code == 200
        actualizar_seguridad_sesion(client.db, segundos_inactividad=300, dias_registro_dispositivo=4, actor=admin)
        client.db.commit()
        assert client.get("/paquetes", follow_redirects=False).headers["location"].endswith("/ingresar")


def test_sin_la_cookie_del_equipo_la_sesion_no_vale(client):
    _, op = _sembrar(client)
    ods.definir_pin(client.db, op, "4821")
    client.db.commit()
    _ingresar(client, "op@club.com")
    client.cookies.delete("paquetex_dispositivo")
    r = client.get("/paquetes", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_cookie_del_equipo_adulterada_no_vale(client):
    _, op = _sembrar(client)
    ods.definir_pin(client.db, op, "4821")
    client.db.commit()
    _ingresar(client, "op@club.com")
    client.cookies.set("paquetex_dispositivo", "00000000-0000-0000-0000-000000000000.firma-falsa")
    assert client.get("/paquetes", follow_redirects=False).status_code == 303
