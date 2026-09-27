# -*- coding: utf-8 -*-
"""
Capa web — vista de mi PIN y revocación (`.scratch/pin-operador-dispositivo`, ticket 08).

- Cambiar el PIN cuando se quiera, confirmando con la contraseña.
- "Salir de este dispositivo": quita el registro del Usuario solo en ESE equipo.
- "Cerrar en todos los dispositivos": sube la versión de registros del Usuario; en todo equipo vuelve a hacer falta la
  contraseña. Lo pueden usar el propio Usuario o un ADMIN (por Usuario, en `/administracion/personal`).
- Desactivar a un Usuario corta también su acceso por PIN (cambiar la contraseña ya lo hace desde el ticket 03).

Dos `TestClient` = dos equipos.
"""

import pytest
from fastapi.testclient import TestClient

from app.domain import operador_dispositivo_service as ods
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

pytestmark = pytest.mark.pin_manual

_PW = "Contrasena1"


def _sembrar(client):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    ana = create_staff(client.db, admin, "ana@club.com", "Ana", _PW, RolUsuario.OPERADOR)
    beto = create_staff(client.db, admin, "beto@club.com", "Beto", _PW, RolUsuario.OPERADOR)
    ods.definir_pin(client.db, admin, "1000")
    ods.definir_pin(client.db, ana, "1111")
    ods.definir_pin(client.db, beto, "2222")
    client.db.commit()
    return admin, ana, beto


def _ingresar(equipo, email):
    equipo.post("/ingresar", data={"email": email, "password": _PW})


def _pin(equipo, pin):
    return equipo.post("/bloqueo", data={"pin": pin, "siguiente": "/paquetes"}, follow_redirects=False)


def _dos_equipos_con_ana(client):
    otro = TestClient(client.app)
    _ingresar(client, "ana@club.com")
    _ingresar(otro, "ana@club.com")
    return otro


def test_la_vista_de_mi_pin_permite_cambiarlo_con_la_contrasena(client):
    _sembrar(client)
    _ingresar(client, "ana@club.com")
    texto = client.get("/mi-pin").text
    assert "Mi PIN" in texto
    assert 'name="password"' in texto

    r = client.post("/mi-pin", data={"password": "incorrecta1", "pin": "3333", "pin_confirmacion": "3333"})
    assert r.status_code == 400
    assert "Contraseña incorrecta" in r.text

    r = client.post("/mi-pin", data={"password": _PW, "pin": "3333", "pin_confirmacion": "3333"}, follow_redirects=False)
    assert r.status_code == 303
    client.post("/bloquear")
    assert _pin(client, "1111").status_code == 400
    assert _pin(client, "3333").headers["location"] == "/paquetes"


def test_cambiar_el_pin_respeta_la_unicidad(client):
    _sembrar(client)
    _ingresar(client, "ana@club.com")
    r = client.post("/mi-pin", data={"password": _PW, "pin": "2222", "pin_confirmacion": "2222"})
    assert r.status_code == 400
    assert "no está disponible" in r.text


def test_el_menu_trae_mi_pin_y_salir_de_este_dispositivo(client):
    _sembrar(client)
    _ingresar(client, "ana@club.com")
    texto = client.get("/paquetes").text
    assert 'href="/mi-pin"' in texto
    assert 'action="/salir-dispositivo"' in texto
    assert "Salir de este dispositivo" in texto


def test_salir_de_este_dispositivo_deja_vivo_el_registro_en_el_otro(client):
    _sembrar(client)
    otro = _dos_equipos_con_ana(client)
    _ingresar(client, "beto@club.com")
    _pin(client, "1111")

    r = client.post("/salir-dispositivo", follow_redirects=False)
    assert r.status_code == 303
    # En este equipo Ana ya no está registrada (Beto sí): su PIN la manda a entrar con contraseña (issue 425).
    assert _pin(client, "1111").headers["location"] == "/ingresar?aviso=sin-registro"
    assert _pin(client, "2222").headers["location"] == "/paquetes"
    # En el otro equipo, sí.
    otro.post("/bloquear")
    assert _pin(otro, "1111").headers["location"] == "/paquetes"


def test_salir_del_unico_registro_del_equipo_manda_a_ingresar(client):
    _sembrar(client)
    _ingresar(client, "ana@club.com")
    r = client.post("/salir-dispositivo", follow_redirects=False)
    assert r.headers["location"] == "/ingresar"
    assert client.get("/paquetes", follow_redirects=False).headers["location"].endswith("/ingresar")


def test_cerrar_en_todos_uno_mismo_exige_contrasena_en_todos_los_equipos(client):
    _sembrar(client)
    otro = _dos_equipos_con_ana(client)
    r = client.post("/mi-pin/cerrar-todos", follow_redirects=False)
    assert r.headers["location"] == "/ingresar"
    for equipo in (client, otro):
        assert equipo.get("/paquetes", follow_redirects=False).headers["location"].endswith("/ingresar")
        assert _pin(equipo, "1111").status_code in (303, 400)
        assert equipo.get("/paquetes", follow_redirects=False).status_code == 303


def test_el_admin_cierra_a_un_usuario_en_todos_sus_equipos(client):
    _sembrar(client)
    otro = _dos_equipos_con_ana(client)
    admin_equipo = TestClient(client.app)
    _ingresar(admin_equipo, "admin@club.com")

    ana_id = client.db.query(ods.Usuario).filter(ods.Usuario.email == "ana@club.com").one().id
    assert 'action="/administracion/personal/%s/cerrar-dispositivos"' % ana_id in admin_equipo.get(
        "/administracion/personal"
    ).text
    r = admin_equipo.post(f"/administracion/personal/{ana_id}/cerrar-dispositivos", follow_redirects=False)
    assert r.status_code == 303
    for equipo in (client, otro):
        assert equipo.get("/paquetes", follow_redirects=False).headers["location"].endswith("/ingresar")


def test_un_operador_no_puede_cerrar_a_otro_usuario(client):
    _, _, beto = _sembrar(client)
    _ingresar(client, "ana@club.com")
    r = client.post(f"/administracion/personal/{beto.id}/cerrar-dispositivos", follow_redirects=False)
    assert r.status_code == 403
    with pytest.raises(PermissionError):
        ods.cerrar_en_todos(client.db, beto, actor=client.db.query(ods.Usuario).filter(
            ods.Usuario.email == "ana@club.com").one())


def test_desactivar_a_un_usuario_hace_que_su_pin_deje_de_funcionar(client):
    _, ana, _ = _sembrar(client)
    _ingresar(client, "beto@club.com")
    _ingresar(client, "ana@club.com")
    client.post("/bloquear")
    admin_equipo = TestClient(client.app)
    _ingresar(admin_equipo, "admin@club.com")
    admin_equipo.post(f"/administracion/personal/{ana.id}/desactivar")

    assert _pin(client, "1111").status_code == 400
    assert _pin(client, "2222").headers["location"] == "/paquetes"


def test_salir_de_este_dispositivo_cierra_tambien_la_sesion_de_cliente(client):
    """Mismo alcance que el "Cerrar sesión" unificado al que reemplaza en el menú de staff (Grupo 10, Ronda 2)."""
    from test_layout import _login_cliente

    _sembrar(client)
    _login_cliente(client)
    assert client.get("/mis-paquetes", follow_redirects=False).status_code == 200
    _ingresar(client, "ana@club.com")

    r = client.post("/salir-dispositivo", follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/mis-paquetes", follow_redirects=False).headers["location"].endswith("/otp")
