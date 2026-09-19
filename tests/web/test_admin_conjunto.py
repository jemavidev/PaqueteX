# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/conjunto` (`.scratch/apartamento-catalogo-
confirmacion`, ticket 01).

Comportamiento observable por HTTP: gate require_admin (mismo patrón que
`/administracion/personal` y `/administracion/notificaciones`); sin fila
previa, el campo muestra el nombre por defecto; guardar persiste el nombre y
lo refleja en la respuesta.
"""

from app.domain.configuracion_conjunto_service import (
    obtener_datos_operativos,
    obtener_nombre_conjunto,
)
from app.domain.configuracion_empresa_service import (
    RAZON_SOCIAL_POR_DEFECTO,
    obtener_datos_empresa,
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


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/administracion/conjunto", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/conjunto")
    assert r.status_code == 403


def test_admin_ve_el_nombre_vigente_por_defecto(client):
    _login_admin(client)
    r = client.get("/administracion/conjunto")
    assert r.status_code == 200
    assert "EL CLUB" in r.text


def test_admin_renombra_y_persiste(client):
    _login_admin(client)
    r = client.post("/administracion/conjunto", data={"nombre": "Reserva de Bosques"})
    assert r.status_code == 200
    assert "RESERVA DE BOSQUES" in r.text

    client.db.expire_all()
    assert obtener_nombre_conjunto(client.db) == "RESERVA DE BOSQUES"


def test_operador_no_puede_renombrar_por_post(client):
    _login_operador(client)
    r = client.post("/administracion/conjunto", data={"nombre": "Otro Nombre"})
    assert r.status_code == 403

    client.db.expire_all()
    assert obtener_nombre_conjunto(client.db) == "EL CLUB"


def test_nombre_vacio_no_se_guarda(client):
    _login_admin(client)
    r = client.post("/administracion/conjunto", data={"nombre": "   "})
    assert r.status_code == 400

    client.db.expire_all()
    assert obtener_nombre_conjunto(client.db) == "EL CLUB"


# --------------------------------------------------------------------------- #
# Datos operativos (horarios + WhatsApp) y Empresa operadora
# (grilling 2026-09-18, issue 350 -- .scratch/pendientes-cliente/spec.md)
# --------------------------------------------------------------------------- #


def test_admin_ve_los_horarios_por_defecto(client):
    _login_admin(client)
    r = client.get("/administracion/conjunto")
    assert r.status_code == 200
    assert "9:30 AM - 7:30 PM" in r.text
    assert RAZON_SOCIAL_POR_DEFECTO in r.text


def test_admin_actualiza_horarios_y_whatsapp(client):
    _login_admin(client)
    r = client.post(
        "/administracion/conjunto",
        data={
            "nombre": "El Club",
            "horario_lunes_viernes": "8:00 AM - 6:00 PM",
            "horario_sabados": "9:00 AM - 1:00 PM",
            "horario_domingos": "",
            "numero_whatsapp": "573001234567",
        },
    )
    assert r.status_code == 200
    assert "8:00 AM - 6:00 PM" in r.text

    client.db.expire_all()
    datos = obtener_datos_operativos(client.db)
    assert datos.horario_lunes_viernes == "8:00 AM - 6:00 PM"
    assert datos.horario_sabados == "9:00 AM - 1:00 PM"
    # Vacío -> cae al default en código, mismo criterio que `nombre`.
    assert datos.horario_domingos == "2:00 PM - 6:00 PM"
    assert datos.numero_whatsapp == "573001234567"


def test_operador_no_puede_editar_conjunto(client):
    _login_operador(client)
    r = client.post(
        "/administracion/conjunto",
        data={"nombre": "El Club", "numero_whatsapp": "573001234567"},
    )
    assert r.status_code == 403

    client.db.expire_all()
    assert obtener_datos_operativos(client.db).numero_whatsapp == ""


def test_admin_actualiza_datos_de_la_empresa(client):
    _login_admin(client)
    r = client.post(
        "/administracion/conjunto/empresa",
        data={
            "razon_social": "Nueva Razón Social S.A.S.",
            "nit": "900123456-7",
            "direccion": "Calle 1 #2-3",
            "email_contacto": "contacto@nueva.com",
            "telefono_contacto": "3001234567",
        },
    )
    assert r.status_code == 200
    assert "Nueva Razón Social S.A.S." in r.text

    client.db.expire_all()
    datos = obtener_datos_empresa(client.db)
    assert datos.razon_social == "Nueva Razón Social S.A.S."
    assert datos.nit == "900123456-7"


def test_empresa_nit_puede_quedar_vacio(client):
    _login_admin(client)
    r = client.post(
        "/administracion/conjunto/empresa",
        data={
            "razon_social": "Papyrus Soluciones Integrales S.A.S.",
            "nit": "",
            "direccion": "Cra. 91 #54-120, Local 12",
            "email_contacto": "paquetex@papyrus.com.co",
            "telefono_contacto": "(333) 400-4007",
        },
    )
    assert r.status_code == 200

    client.db.expire_all()
    assert obtener_datos_empresa(client.db).nit == ""


def test_empresa_razon_social_vacia_no_se_guarda(client):
    _login_admin(client)
    r = client.post(
        "/administracion/conjunto/empresa",
        data={"razon_social": "   "},
    )
    assert r.status_code == 400

    client.db.expire_all()
    assert obtener_datos_empresa(client.db).razon_social == RAZON_SOCIAL_POR_DEFECTO


def test_operador_no_puede_editar_empresa(client):
    _login_operador(client)
    r = client.post(
        "/administracion/conjunto/empresa",
        data={"razon_social": "Otra S.A.S."},
    )
    assert r.status_code == 403
