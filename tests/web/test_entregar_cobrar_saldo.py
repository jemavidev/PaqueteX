# -*- coding: utf-8 -*-
"""
Capa web — Entregar con deuda obliga a cobrarla (pedido del cliente 2026-10-31, migración 0074).

Antes el saldo contra entrega negativo era solo informativo al Entregar (ticket 04 de .scratch/dinero-contra-entrega:
"nunca bloquea"). Ahora, si el destinatario debe D: `pago_saldo` va de 0 a D (más que D: 400); si no cubre toda la
deuda, hace falta `motivo_no_cobro` ('sin_efectivo' | 'paga_despues' | 'cliente_vip' | 'otro', con `nota_no_cobro`
de al menos 5 letras para 'otro'), que queda en `paquetes.motivo_no_cobro` junto con lo que quedó debiendo
(`paquetes.saldo_no_cobrado`). Sin eso: 400 y el modal Entregar se reabre con el mensaje, en /paquetes y en
/consultar. Un destinatario sin deuda no cambia.
"""

import re

import pytest

from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_lifecycle import receive as dom_receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona_service import get_or_create_persona
from app.domain.saldo_contra_entrega import MovimientoSaldoContraEntrega
from app.domain.saldo_contra_entrega_service import registrar_movimiento_saldo, saldo_de_persona
from app.domain.staff_service import create_initial_admin
from app.domain.usuario import Usuario
from app.web.routes.packages import ERROR_MOTIVO_NO_COBRO, ERROR_MOTIVO_OTRO_CORTO

_PW = "Contrasena1"
_DEUDA = 5000


def _preparar(client, saldo=-_DEUDA, tel="3001234567"):
    create_initial_admin(client.db, "staff@club.com", "Operador", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": "staff@club.com", "password": _PW})
    staff = client.db.query(Usuario).filter(Usuario.email == "staff@club.com").one()
    persona = get_or_create_persona(client.db, tel, "Ana")
    if saldo:
        registrar_movimiento_saldo(client.db, persona.id, saldo, staff)
    p = announce(client.db, anunciante_telefono=tel, anunciante_nombre="Ana", destinatario=Destinatario.yo_mismo())
    dom_receive(client.db, p, staff)
    client.db.commit()
    return p, persona


def _entregar(client, p, desde_consultar=False, **campos):
    datos = dict(campos)
    if desde_consultar:
        datos.update({"origen": "consultar", "q": p.access_code})
    return client.post(f"/paquetes/{p.id}/entregar", data=datos, follow_redirects=False)


def _recargar(client, p):
    client.db.expire_all()
    return client.db.get(Paquete, p.id)


def _movimientos_del_paquete(client, p):
    return client.db.query(MovimientoSaldoContraEntrega).filter(MovimientoSaldoContraEntrega.paquete_id == p.id).all()


def _modal_abierto(html, modal_id):
    tag = re.search(rf'<div id="modal-{modal_id}"[^>]*>', html).group(0)
    return " hidden" not in tag


@pytest.mark.parametrize("desde_consultar", [False, True])
def test_con_deuda_sin_pago_ni_motivo_se_rechaza_y_reabre_el_modal(client, desde_consultar):
    p, persona = _preparar(client)

    r = _entregar(client, p, desde_consultar)

    assert r.status_code == 400
    assert ERROR_MOTIVO_NO_COBRO in r.text
    assert _modal_abierto(r.text, "entregar-consultar" if desde_consultar else f"deliver-{p.id}")
    assert _recargar(client, p).estado == EstadoPaquete.RECIBIDO
    assert saldo_de_persona(client.db, persona.id) == -_DEUDA


@pytest.mark.parametrize("desde_consultar", [False, True])
def test_pago_completo_entrega_sin_motivo(client, desde_consultar):
    p, persona = _preparar(client)

    r = _entregar(client, p, desde_consultar, pago_saldo=str(_DEUDA))

    assert r.status_code == 303
    entregado = _recargar(client, p)
    assert entregado.estado == EstadoPaquete.ENTREGADO
    assert entregado.motivo_no_cobro is None
    assert entregado.saldo_no_cobrado is None
    assert saldo_de_persona(client.db, persona.id) == 0
    assert [m.monto for m in _movimientos_del_paquete(client, p)] == [_DEUDA]


@pytest.mark.parametrize("desde_consultar", [False, True])
def test_pago_parcial_con_motivo_entrega_y_guarda_lo_que_quedo_debiendo(client, desde_consultar):
    p, persona = _preparar(client)

    r = _entregar(client, p, desde_consultar, pago_saldo="2000", motivo_no_cobro="sin_efectivo",
                  nota_no_cobro="  trae el resto   el lunes ")

    assert r.status_code == 303
    entregado = _recargar(client, p)
    assert entregado.estado == EstadoPaquete.ENTREGADO
    assert entregado.motivo_no_cobro == "sin_efectivo: trae el resto el lunes"
    assert entregado.saldo_no_cobrado == 3000
    assert saldo_de_persona(client.db, persona.id) == -3000


def test_pago_parcial_sin_motivo_se_rechaza(client):
    p, persona = _preparar(client)

    r = _entregar(client, p, pago_saldo="2000")

    assert r.status_code == 400
    assert ERROR_MOTIVO_NO_COBRO in r.text
    assert _recargar(client, p).estado == EstadoPaquete.RECIBIDO
    assert _movimientos_del_paquete(client, p) == []


def test_sin_pago_con_motivo_sin_nota_entrega(client):
    p, persona = _preparar(client)

    r = _entregar(client, p, motivo_no_cobro="cliente_vip")

    assert r.status_code == 303
    entregado = _recargar(client, p)
    assert entregado.motivo_no_cobro == "cliente_vip"
    assert entregado.saldo_no_cobrado == _DEUDA
    assert _movimientos_del_paquete(client, p) == []


@pytest.mark.parametrize("desde_consultar", [False, True])
def test_pago_mayor_que_la_deuda_se_rechaza(client, desde_consultar):
    p, persona = _preparar(client)

    r = _entregar(client, p, desde_consultar, pago_saldo=str(_DEUDA + 1), motivo_no_cobro="sin_efectivo")

    assert r.status_code == 400
    assert "El abono no puede ser mayor que la deuda ($5,000)." in r.text
    assert _recargar(client, p).estado == EstadoPaquete.RECIBIDO
    assert saldo_de_persona(client.db, persona.id) == -_DEUDA


def test_pago_negativo_se_rechaza(client):
    p, _ = _preparar(client)

    r = _entregar(client, p, pago_saldo="-100", motivo_no_cobro="sin_efectivo")

    assert r.status_code == 400
    assert _recargar(client, p).estado == EstadoPaquete.RECIBIDO


@pytest.mark.parametrize("nota", ["", "abc"])
def test_otro_motivo_sin_nota_suficiente_se_rechaza(client, nota):
    p, _ = _preparar(client)

    r = _entregar(client, p, motivo_no_cobro="otro", nota_no_cobro=nota)

    assert r.status_code == 400
    assert ERROR_MOTIVO_OTRO_CORTO in r.text
    assert _recargar(client, p).estado == EstadoPaquete.RECIBIDO


def test_otro_motivo_con_nota_entrega(client):
    p, _ = _preparar(client)

    r = _entregar(client, p, motivo_no_cobro="otro", nota_no_cobro="Lo paga el hermano")

    assert r.status_code == 303
    assert _recargar(client, p).motivo_no_cobro == "otro: Lo paga el hermano"


def test_motivo_desconocido_se_rechaza(client):
    p, _ = _preparar(client)

    r = _entregar(client, p, motivo_no_cobro="porque si")

    assert r.status_code == 400
    assert _recargar(client, p).estado == EstadoPaquete.RECIBIDO


def test_sin_deuda_entrega_igual_que_antes(client):
    p, persona = _preparar(client, saldo=0)

    html = client.get("/paquetes").text
    assert "data-cobrar-saldo=" not in html
    assert f'id="entregar-boton-{p.id}" class=' in html  # sin `disabled`

    r = _entregar(client, p)

    assert r.status_code == 303
    entregado = _recargar(client, p)
    assert entregado.estado == EstadoPaquete.ENTREGADO
    assert entregado.motivo_no_cobro is None
    assert entregado.saldo_no_cobrado is None


def test_saldo_a_favor_entrega_igual_que_antes(client):
    p, persona = _preparar(client, saldo=4000)

    r = _entregar(client, p)

    assert r.status_code == 303
    assert _recargar(client, p).estado == EstadoPaquete.ENTREGADO


@pytest.mark.parametrize("url", ["/paquetes", "/consultar?q={codigo}"])
def test_con_deuda_el_modal_trae_la_caja_y_el_boton_deshabilitado(client, url):
    p, _ = _preparar(client)

    html = client.get(url.format(codigo=p.access_code)).text

    assert "Cobrar $5,000" in html
    assert "Cobrar $5,000" in html
    assert "No cobrar ahora" in html
    assert "¿Entregar sin cobrar todo?" in html
    prefijo = "consultar" if "consultar" in url else str(p.id)
    assert re.search(rf'<button type="submit" id="entregar-boton-{prefijo}" disabled', html)
    assert f'data-entregar-bloqueado="{prefijo}"' in html


@pytest.mark.parametrize("url", ["/paquetes?estado=ENTREGADO", "/consultar?q={codigo}"])
def test_chip_entregado_sin_cobrar_con_motivo_en_el_title(client, url):
    p, _ = _preparar(client)
    assert _entregar(client, p, pago_saldo="1500", motivo_no_cobro="paga_despues", nota_no_cobro="el viernes").status_code == 303

    html = client.get(url.format(codigo=p.access_code)).text

    assert "Entregado sin cobrar ($3,500)" in html
    assert 'title="Se entregó debiendo $3,500: Paga después — el viernes"' in html


def test_sin_sesion_consultar_no_muestra_el_chip(client):
    p, _ = _preparar(client)
    _entregar(client, p, motivo_no_cobro="sin_efectivo")
    client.cookies.clear()

    html = client.get(f"/consultar?q={p.access_code}").text

    assert "Entregado sin cobrar" not in html
