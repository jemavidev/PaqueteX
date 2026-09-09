# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/tarifas-cobro` (`.scratch/cobro-bodegaje`, ticket 04).

Comportamiento observable por HTTP: gate require_admin, valores por defecto
sin fila previa, guardar persiste y no altera cobros ya registrados.
"""

from app.domain.cobro_service import obtener_tarifas_vigentes
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
    r = client.get("/administracion/tarifas-cobro", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/tarifas-cobro")
    assert r.status_code == 403


def test_admin_ve_los_valores_por_defecto(client):
    _login_admin(client)
    r = client.get("/administracion/tarifas-cobro")
    assert r.status_code == 200
    assert "1500" in r.text
    assert "2000" in r.text
    assert "1000" in r.text


def test_las_4_tarifas_tienen_etiqueta_siempre_visible(client):
    # Encontrado en pruebas manuales en navegador: `input_texto` solo
    # muestra su `label` como placeholder -- desaparece en cuanto el campo
    # tiene un valor (limitación del propio HTML, no del componente), y
    # este formulario SIEMPRE llega con los 4 campos ya llenos. Sin una
    # etiqueta persistente, un admin ve 4 números sueltos sin saber cuál es
    # cuál. Mismo patrón ya usado en `admin/proveedores.html` (corrección en
    # vivo del cliente): un <label> propio de esta pantalla, sin tocar
    # `_inputs.html`.
    _login_admin(client)
    r = client.get("/administracion/tarifas-cobro")
    assert r.status_code == 200
    # `placeholder`/`aria-label` de `input_texto` YA contienen este texto
    # (no desaparecen del HTML, solo de la vista una vez el campo tiene
    # valor) -- lo que hay que confirmar es un <label> real, persistente.
    assert ">Cargo base — Normal</label>" in r.text
    assert ">Cargo base — Extra-dimensionado" in r.text
    assert ">Bodegaje / 24h — Normal</label>" in r.text
    assert ">Bodegaje / 24h — Extra-dimensionado" in r.text


def test_admin_edita_las_4_tarifas(client):
    _login_admin(client)
    r = client.post(
        "/administracion/tarifas-cobro",
        data={
            "base_normal": "1800",
            "base_extra_dimensionado": "2500",
            "bodegaje_normal_24h": "1200",
            "bodegaje_extra_dimensionado_24h": "1700",
        },
    )
    assert r.status_code == 200

    tarifas = obtener_tarifas_vigentes(client.db)
    assert tarifas.base_normal == 1800
    assert tarifas.base_extra_dimensionado == 2500
    assert tarifas.bodegaje_normal_24h == 1200
    assert tarifas.bodegaje_extra_dimensionado_24h == 1700


def test_operador_no_puede_editar(client):
    _login_operador(client)
    r = client.post("/administracion/tarifas-cobro", data={"base_normal": "9999"})
    assert r.status_code == 403
