# -*- coding: utf-8 -*-
"""
Capa web — `/administracion/estadisticas-cobro` (`.scratch/cobro-bodegaje`
ticket 06, rediseño interactivo en `.scratch/estadisticas-cobro-
interactivas`). Solo lectura, exclusiva de admin -- cablea
`FiltrosEstadisticasCobro` a partir de los query params. La aritmética de
los agregados/filtros/paginación ya está probada a fondo contra Postgres
real en `tests/data_model/test_cobro_service_integration.py` (Seam A); acá
solo se cubre que la ruta HTTP arme los filtros correctos, use el rango por
defecto correcto, y que el mecanismo de fetch en vivo devuelva solo el
fragmento (mismo convenio que `/paquetes`/`/administracion/contactos-
externos`).
"""

from datetime import datetime, timedelta, timezone

from app.domain.cobro import Cobro
from app.domain.cobro_service import DesgloseCobro, crear_motivo_anulacion, registrar_cobro
from app.domain.paquete import TipoPaquete
from app.domain.paquete_lifecycle import deliver as dom_deliver
from app.domain.paquete_lifecycle import receive as dom_receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario, Usuario

_PW = "Contrasena1"


def _login_admin(client, email="admin@club.com"):
    admin = create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return admin


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _entregar_con_cobro(client, staff, monto_total, tel, tipo=None, motivo_anulacion=None):
    p = announce(
        client.db,
        anunciante_telefono=tel,
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    dom_receive(client.db, p, staff, package_type=tipo)
    dom_deliver(client.db, p, staff)
    registrar_cobro(
        client.db,
        p,
        DesgloseCobro(
            monto_base=monto_total, bloques_bodegaje=0, monto_bodegaje=0, monto_total=monto_total
        ),
        staff,
        motivo_anulacion=motivo_anulacion,
    )
    client.db.commit()
    return p


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/administracion/estadisticas-cobro", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_es_rechazado_403(client):
    _login_operador(client)
    r = client.get("/administracion/estadisticas-cobro")
    assert r.status_code == 403


def test_admin_ve_ultimos_30_dias_por_defecto(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1500, tel="3001234567")
    p_viejo = _entregar_con_cobro(client, admin, 9999, tel="3009999999")
    cobro_viejo = client.db.query(Cobro).filter(Cobro.paquete_id == p_viejo.id).one()
    cobro_viejo.cobrado_en = datetime.now(timezone.utc) - timedelta(days=40)
    client.db.commit()

    r = client.get("/administracion/estadisticas-cobro")
    assert r.status_code == 200
    assert "1,500" in r.text
    assert "9,999" not in r.text


def test_filtra_por_tipo_de_paquete(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1234, tel="3001111111", tipo=TipoPaquete.NORMAL)
    _entregar_con_cobro(client, admin, 5678, tel="3002222222", tipo=TipoPaquete.EXTRA_DIMENSIONADO)

    r = client.get("/administracion/estadisticas-cobro", params={"tipo": "EXTRA_DIMENSIONADO"})
    assert r.status_code == 200
    assert "5,678" in r.text
    assert "1,234" not in r.text


def test_filtra_cobrados_vs_anulados(client):
    admin = _login_admin(client)
    crear_motivo_anulacion(client.db, "Reclamo")
    client.db.commit()
    _entregar_con_cobro(client, admin, 1234, tel="3001111111")
    _entregar_con_cobro(client, admin, 0, tel="3002222222", motivo_anulacion="Reclamo")

    r = client.get("/administracion/estadisticas-cobro", params={"estado_cobro": "anulado"})
    assert r.status_code == 200
    assert "1,234" not in r.text


def test_filtra_por_usuario_pero_la_tabla_por_usuario_no_se_acota(client):
    admin = _login_admin(client)
    operador = create_staff(client.db, admin, "op2@club.com", "Operador Dos", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    _entregar_con_cobro(client, admin, 1111, tel="3001111111")
    _entregar_con_cobro(client, operador, 2222, tel="3002222222")

    r = client.get("/administracion/estadisticas-cobro", params={"usuario_id": str(operador.id)})
    assert r.status_code == 200
    # El resto de la vista (monto total, "Por apartamento") queda acotado a
    # `operador` -- el teléfono del paquete de `admin` no debería aparecer.
    assert "+573001111111" not in r.text
    assert "+573002222222" in r.text
    # La tabla "Por usuario" sigue mostrando a AMBOS aunque el resto de la
    # vista esté acotado a `operador` -- historia 11 del spec: esa tabla
    # ignora a propósito su propio filtro de Usuario, para servir de
    # comparación. `Usuario.nombre` se guarda en mayúsculas (ver
    # `staff_service`).
    assert "ADMIN" in r.text
    assert "OPERADOR DOS" in r.text


def test_usuario_id_invalido_se_ignora_sin_error(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1500, tel="3001234567")

    r = client.get("/administracion/estadisticas-cobro", params={"usuario_id": "no-es-un-uuid"})
    assert r.status_code == 200
    assert "1,500" in r.text


def test_pagina_fuera_de_rango_no_rompe_ni_combinada_con_filtros(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 1500, tel="3001234567", tipo=TipoPaquete.NORMAL)

    r = client.get(
        "/administracion/estadisticas-cobro",
        params={"tipo": "NORMAL", "pagina_apartamento": 999, "pagina_usuario": 999, "pagina_diario": 999},
    )
    assert r.status_code == 200


def test_peticion_en_vivo_devuelve_solo_el_fragmento(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 4321, tel="3001111111")

    fragmento = client.get(
        "/administracion/estadisticas-cobro", headers={"X-Requested-With": "fetch"}
    )
    assert fragmento.status_code == 200
    assert "<h1" not in fragmento.text
    assert "<html" not in fragmento.text
    assert "4,321" in fragmento.text


def test_carga_completa_incluye_la_barra_de_filtros_y_el_fragmento(client):
    admin = _login_admin(client)
    _entregar_con_cobro(client, admin, 4321, tel="3001111111")

    r = client.get("/administracion/estadisticas-cobro")
    assert r.status_code == 200
    assert "<h1" in r.text
    assert "4,321" in r.text
