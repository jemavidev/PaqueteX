# -*- coding: utf-8 -*-
"""
Capa web — sección "Seguridad de sesión" de `/administracion/conjunto` (`.scratch/pin-operador-dispositivo`,
ticket 01).

Dos tiempos editables solo por ADMIN: segundos de inactividad antes del Bloqueo (60–3600, 300 por defecto) y días de
vigencia del registro de un dispositivo (1–90, 15 por defecto). Sin fila de configuración o sin valor guardado, rigen
los defaults.
"""

from app.domain.configuracion_conjunto_service import obtener_seguridad_sesion
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"
_URL = "/administracion/conjunto/seguridad"


def _login_admin(client):
    create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": "admin@club.com", "password": _PW})


def test_sin_configuracion_rigen_los_valores_por_defecto(client):
    seguridad = obtener_seguridad_sesion(client.db)
    assert seguridad.segundos_inactividad == 300
    assert seguridad.dias_registro_dispositivo == 15


def test_admin_ve_la_seccion_con_los_valores_vigentes(client):
    _login_admin(client)
    r = client.get("/administracion/conjunto")
    assert r.status_code == 200
    assert "Seguridad de sesión" in r.text
    assert 'name="segundos_inactividad"' in r.text
    assert 'value="300"' in r.text
    assert 'value="15"' in r.text


def test_guardar_valores_validos_aplica_en_la_siguiente_peticion(client):
    _login_admin(client)
    r = client.post(_URL, data={"segundos_inactividad": "600", "dias_registro_dispositivo": "30"})
    assert r.status_code == 200
    assert "Seguridad de sesión actualizada." in r.text

    client.db.expire_all()
    seguridad = obtener_seguridad_sesion(client.db)
    assert (seguridad.segundos_inactividad, seguridad.dias_registro_dispositivo) == (600, 30)
    assert 'value="600"' in client.get("/administracion/conjunto").text


def test_guardar_no_toca_los_datos_del_conjunto(client):
    _login_admin(client)
    client.post(
        "/administracion/conjunto",
        data={"nombre": "Torres del Parque", "horario_lunes_viernes": "8 AM - 6 PM"},
    )
    client.post(_URL, data={"segundos_inactividad": "120", "dias_registro_dispositivo": "7"})
    texto = client.get("/administracion/conjunto").text
    assert "TORRES DEL PARQUE" in texto
    assert "8 AM - 6 PM" in texto


def test_valores_fuera_de_rango_o_invalidos_se_rechazan_sin_guardar(client):
    _login_admin(client)
    casos = [
        ({"segundos_inactividad": "59", "dias_registro_dispositivo": "15"}, "60 y 3600"),
        ({"segundos_inactividad": "3601", "dias_registro_dispositivo": "15"}, "60 y 3600"),
        ({"segundos_inactividad": "300", "dias_registro_dispositivo": "0"}, "1 y 90"),
        ({"segundos_inactividad": "300", "dias_registro_dispositivo": "91"}, "1 y 90"),
        ({"segundos_inactividad": "", "dias_registro_dispositivo": "15"}, "60 y 3600"),
        ({"segundos_inactividad": "abc", "dias_registro_dispositivo": "15"}, "60 y 3600"),
    ]
    for datos, mensaje in casos:
        r = client.post(_URL, data=datos)
        assert r.status_code == 400, datos
        assert mensaje in r.text, datos

    client.db.expire_all()
    seguridad = obtener_seguridad_sesion(client.db)
    assert (seguridad.segundos_inactividad, seguridad.dias_registro_dispositivo) == (300, 15)


def test_operador_no_puede_guardar(client):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": "op@club.com", "password": _PW})

    r = client.post(_URL, data={"segundos_inactividad": "600", "dias_registro_dispositivo": "30"})
    assert r.status_code == 403
    client.db.expire_all()
    assert obtener_seguridad_sesion(client.db).segundos_inactividad == 300
