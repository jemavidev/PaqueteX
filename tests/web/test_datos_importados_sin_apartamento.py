# -*- coding: utf-8 -*-
"""
Capa web — las pantallas funcionan con los datos tal como los deja el
importador espejo v1 → v2 (`.scratch/importador-v1-espejo`, ticket 09): la v1
nunca guardó torre ni apartamento, así que TODA Persona importada llega sin
apartamento actual y todo Paquete importado con el snapshot vacío.

Los datos se siembran corriendo el importador de verdad (`sincronizar_desde_v1`)
sobre una instantánea de la v1 en memoria, para que la forma de los datos sea
exactamente la que verá producción (usuarios técnicos inactivos, cobros
históricos, anuncios pendientes).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.importador_v1_service import (
    AnuncioV1,
    ClienteV1,
    HistorialV1,
    InstantaneaV1,
    PaqueteV1,
    UsuarioV1,
    sincronizar_desde_v1,
)
from app.domain.otp_sender import DevOtpSender
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.persona import Persona
from app.domain.staff_service import create_initial_admin
from app.web.otp import get_otp_sender

_PW = "Contrasena1"
T0 = datetime.now(timezone.utc) - timedelta(days=3)
CLIENTES = [
    ClienteV1(id="c-1", telefono="+573001112233", nombre="JUAN IMPORTADO"),
    ClienteV1(id="c-2", telefono="+573004445566", nombre="ANA IMPORTADA"),
]


def _paquete(id_v1, codigo, estado, cliente_id="c-1", nombre=None):
    return PaqueteV1(
        id=id_v1, cliente_id=cliente_id, tracking_number=codigo, guide_number=f"GUIA-{codigo}",
        display_name=nombre, estado=estado, package_type="NORMAL", package_condition="BUENO",
        announced_at=T0, received_at=T0 + timedelta(hours=1),
        delivered_at=T0 + timedelta(days=1) if estado == "ENTREGADO" else None,
        cancelled_at=T0 + timedelta(days=1) if estado == "CANCELADO" else None,
        total_amount=Decimal("1500") if estado == "ENTREGADO" else None,
    )


PAQUETES = [
    _paquete(1, "RCB1", "RECIBIDO", nombre="MARIA A NOMBRE DE"),
    _paquete(2, "RCB2", "RECIBIDO", cliente_id="c-2"),
    _paquete(3, "ENT1", "ENTREGADO"),
    _paquete(4, "CAN1", "CANCELADO"),
]
HISTORIAL = [
    HistorialV1(paquete_id=p.id, estado="RECIBIDO", changed_by="rafael", changed_at=T0 + timedelta(hours=1))
    for p in PAQUETES
] + [
    HistorialV1(paquete_id=3, estado="ENTREGADO", changed_by="operator_1", changed_at=T0 + timedelta(days=1)),
    HistorialV1(paquete_id=4, estado="CANCELADO", changed_by="operator_1", changed_at=T0 + timedelta(days=1),
                motivo_cancelacion="otro"),
]
ANUNCIO = AnuncioV1(id="a-1", cliente_id="c-2", tracking_code="ANU1", guide_number=None,
                    nombre_destinatario="ANA IMPORTADA", announced_at=T0)


@pytest.fixture()
def importados(client):
    reporte = sincronizar_desde_v1(
        client.db,
        InstantaneaV1(
            clientes=CLIENTES,
            usuarios=[UsuarioV1(username="rafael", email="rafael@v1.test", nombre="Rafael v1", rol="OPERADOR")],
            paquetes=PAQUETES,
            historial=HISTORIAL,
            anuncios=[ANUNCIO],
        ),
    )
    client.db.commit()
    assert not reporte.errores and not reporte.choques
    assert client.db.query(Persona).filter(Persona.apartamento_actual_id.isnot(None)).count() == 0
    return {p.access_code: p for p in client.db.query(Paquete)}


def _login_staff(client):
    create_initial_admin(client.db, "admin@club.test", "Admin", _PW)
    client.db.commit()
    assert client.post("/ingresar", data={"email": "admin@club.test", "password": _PW}).status_code == 200


def _login_residente(client, telefono="3001112233"):
    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    client.post("/otp/solicitar", data={"telefono": telefono})
    codigo = sender.enviados["+57" + telefono]
    client.post("/otp/verificar", data={"telefono": telefono, "codigo": codigo})


def _ok(client, url):
    r = client.get(url)
    assert r.status_code == 200, f"{url} → {r.status_code}"
    return r.text


# --------------------------------------------------------------------------- #
# Staff
# --------------------------------------------------------------------------- #


def test_el_listado_de_paquetes_muestra_los_importados_sin_apartamento(client, importados):
    _login_staff(client)

    html = _ok(client, "/paquetes")

    for codigo in ("RCB1", "RCB2", "ANU1"):
        assert codigo in html
    assert "Sin apartamento" in html


@pytest.mark.parametrize("filtro", ["", "?estado=ENTREGADO", "?estado=CANCELADO", "?estado=ANUNCIADO",
                                    "?q=JUAN", "?q=ENT1", "?q=GUIA-RCB2", "?q=3001112233"])
def test_busquedas_y_filtros_de_paquetes(client, importados, filtro):
    _login_staff(client)

    _ok(client, f"/paquetes{filtro}")


@pytest.mark.parametrize("codigo", ["RCB1", "ENT1", "CAN1", "ANU1"])
def test_la_linea_de_tiempo_de_cada_estado(client, importados, codigo):
    _login_staff(client)

    html = _ok(client, f"/paquetes/{importados[codigo].id}/timeline")

    if codigo in ("ENT1", "CAN1"):
        assert "Operador v1 (sin identificar)" in html


def test_residentes_listado_y_detalle(client, importados):
    _login_staff(client)
    persona = client.db.query(Persona).filter_by(origen_v1_id="c-1").one()

    assert "JUAN IMPORTADO" in _ok(client, "/residentes?q=JUAN")
    _ok(client, "/residentes")
    _ok(client, f"/residentes/{persona.id}")
    _ok(client, "/residentes/saldos-contra-entrega")
    _ok(client, "/residentes/movimientos-saldo-contra-entrega")


def test_estadisticas_de_cobro_y_personal(client, importados):
    _login_staff(client)

    _ok(client, "/administracion/estadisticas-cobro")
    assert "Operador v1 (sin identificar)" in _ok(client, "/administracion/personal")


def test_recibir_un_anunciado_importado(client, importados):
    _login_staff(client)
    paquete = importados["ANU1"]

    r = client.post(f"/paquetes/{paquete.id}/recibir", data={"guide_number": "GUIA-ANU1"}, follow_redirects=False)

    assert r.status_code == 303
    client.db.expire_all()
    assert client.db.get(Paquete, paquete.id).estado == EstadoPaquete.RECIBIDO


def test_entregar_un_recibido_importado(client, importados):
    _login_staff(client)
    paquete = importados["RCB2"]

    r = client.post(f"/paquetes/{paquete.id}/entregar", follow_redirects=False)

    assert r.status_code == 303
    client.db.expire_all()
    assert client.db.get(Paquete, paquete.id).estado == EstadoPaquete.ENTREGADO


def test_cancelar_un_recibido_importado(client, importados):
    _login_staff(client)
    paquete = importados["RCB1"]

    r = client.post(f"/paquetes/{paquete.id}/cancelar", data={"motivo": "Otro"}, follow_redirects=False)

    assert r.status_code == 303
    client.db.expire_all()
    assert client.db.get(Paquete, paquete.id).estado == EstadoPaquete.CANCELADO


# --------------------------------------------------------------------------- #
# Público y residente
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("q", ["RCB1", "ENT1", "CAN1", "ANU1", "GUIA-RCB2"])
def test_consultar_publico_encuentra_los_importados(client, importados, q):
    html = _ok(client, f"/consultar?q={q}")

    assert "Sin apartamento" in html or "sin apartamento" in html.lower()


def test_el_residente_importado_entra_y_ve_sus_paquetes_y_sus_datos(client, importados):
    _login_residente(client)

    # Sin términos aceptados (llegan así de la v1): el portal debe llevarlo a
    # aceptarlos sin romperse.
    r = client.get("/mis-paquetes")
    assert r.status_code in (200, 303)
    _ok(client, "/mis-datos/aceptar-terminos")
    persona = client.db.query(Persona).filter_by(origen_v1_id="c-1").one()
    persona.terminos_aceptados_en = datetime.now(timezone.utc)
    client.db.commit()

    html = _ok(client, "/mis-paquetes")
    assert "RCB1" in html
    assert "Sin apartamento" in html
    _ok(client, "/mis-datos")
