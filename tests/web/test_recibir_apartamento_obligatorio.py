# -*- coding: utf-8 -*-
"""
Capa web — Recibir con apartamento obligatorio (pedido del cliente 2026-10-30, migración 0073).

Los Operadores se saltaban el apartamento del cliente al recibir. Si el destinatario NO tiene apartamento (y el
catálogo existe, si no el modal no ofrece el selector), Recibir exige Torre+Apartamento o, a propósito, un motivo
(`sin_apartamento_motivo`: 'etiqueta' | 'ilegible' | 'otro' + `sin_apartamento_otro` de al menos 5 letras), que queda
en `paquetes.sin_apartamento_motivo`. Sin ninguno de los dos: 400 y el modal se reabre con el mensaje, en /paquetes y
en /consultar. Un destinatario que YA tiene apartamento no cambia.
"""

import re

from app.domain.apartamento_service import resolver_apartamento
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin
from app.domain.usuario import Usuario
from app.web.routes.packages import (
    ERROR_MOTIVO_OTRO_CORTO,
    ERROR_MOTIVO_SIN_FOTOS,
    ERROR_RECIBIR_SIN_APARTAMENTO,
)

_PW = "Contrasena1"


def _preparar(client, con_catalogo=True):
    create_initial_admin(client.db, "staff@club.com", "Operador", _PW)
    if con_catalogo:
        resolver_apartamento(client.db, "TORRE 1", "101")
        resolver_apartamento(client.db, "TORRE 2", "101")
        resolver_apartamento(client.db, "TORRE 2", "202")
    client.db.commit()
    r = client.post("/ingresar", data={"email": "staff@club.com", "password": _PW})
    assert r.status_code == 200
    p = announce(client.db, anunciante_telefono="3001234567", anunciante_nombre="Ana",
                 destinatario=Destinatario.yo_mismo())
    client.db.commit()
    assert p.snapshot_apartamento is None
    return p


def _recibir(client, p, **campos):
    return client.post(
        f"/paquetes/{p.id}/recibir", data={"sin_fotos": "1", "sin_fotos_motivo": "camara", "posicion": "41", **campos}, follow_redirects=False
    )


def _recargar(client, p):
    client.db.expire_all()
    return client.db.get(Paquete, p.id)


def _modal_abierto(html, paquete_id):
    tag = re.search(rf'<div id="modal-receive-{paquete_id}"[^>]*>', html).group(0)
    return " hidden" not in tag


def test_sin_torre_ni_motivo_se_rechaza_y_reabre_el_modal_en_paquetes(client):
    p = _preparar(client)

    r = _recibir(client, p)

    assert r.status_code == 400
    assert _modal_abierto(r.text, p.id)
    assert ERROR_RECIBIR_SIN_APARTAMENTO in r.text
    assert _recargar(client, p).estado == EstadoPaquete.ANUNCIADO


def test_sin_torre_ni_motivo_desde_consultar_reabre_el_modal_ahi(client):
    p = _preparar(client)

    r = _recibir(client, p, origen="consultar", q=p.access_code)

    assert r.status_code == 400
    assert _modal_abierto(r.text, p.id)
    assert ERROR_RECIBIR_SIN_APARTAMENTO in r.text
    assert _recargar(client, p).estado == EstadoPaquete.ANUNCIADO


def test_con_torre_y_apartamento_recibe_y_asigna_el_apartamento(client):
    p = _preparar(client)

    r = _recibir(client, p, torre="TORRE 2", apartamento="202")

    assert r.status_code == 303
    recargado = _recargar(client, p)
    assert recargado.estado == EstadoPaquete.RECIBIDO
    assert recargado.snapshot_torre == "TORRE 2"
    assert recargado.snapshot_apartamento == "202"
    assert recargado.sin_apartamento_motivo is None


def test_con_torre_el_motivo_que_venga_se_ignora(client):
    p = _preparar(client)

    r = _recibir(client, p, torre="TORRE 2", apartamento="202", sin_apartamento_motivo="etiqueta")

    assert r.status_code == 303
    assert _recargar(client, p).sin_apartamento_motivo is None


def test_con_motivo_recibe_sin_apartamento_y_lo_guarda_a_nombre_del_operador(client):
    p = _preparar(client)

    r = _recibir(client, p, sin_apartamento_motivo="ilegible")

    assert r.status_code == 303
    recargado = _recargar(client, p)
    assert recargado.estado == EstadoPaquete.RECIBIDO
    assert recargado.snapshot_apartamento is None
    assert recargado.sin_apartamento_motivo == "ilegible"
    assert recargado.received_by_usuario_id == client.db.query(Usuario).one().id


def test_motivo_otro_guarda_el_texto(client):
    p = _preparar(client)

    r = _recibir(client, p, sin_apartamento_motivo="otro", sin_apartamento_otro="  Paquete sin   etiqueta ")

    assert r.status_code == 303
    assert _recargar(client, p).sin_apartamento_motivo == "otro: Paquete sin etiqueta"


def test_motivo_otro_con_texto_corto_se_rechaza(client):
    p = _preparar(client)

    r = _recibir(client, p, sin_apartamento_motivo="otro", sin_apartamento_otro="nop")

    assert r.status_code == 400
    assert _modal_abierto(r.text, p.id)
    assert ERROR_MOTIVO_OTRO_CORTO in r.text
    assert _recargar(client, p).estado == EstadoPaquete.ANUNCIADO


def test_motivo_desconocido_cuenta_como_ninguno(client):
    p = _preparar(client)

    r = _recibir(client, p, sin_apartamento_motivo="me dio pereza")

    assert r.status_code == 400
    assert _recargar(client, p).estado == EstadoPaquete.ANUNCIADO


def test_destinatario_con_apartamento_no_cambia(client):
    create_initial_admin(client.db, "staff@club.com", "Operador", _PW)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    client.db.commit()
    client.post("/ingresar", data={"email": "staff@club.com", "password": _PW})
    p = announce(client.db, anunciante_telefono="3001234567", anunciante_nombre="Ana",
                 destinatario=Destinatario.yo_mismo(), apartamento=apto)
    client.db.commit()

    r = _recibir(client, p)

    assert r.status_code == 303
    recargado = _recargar(client, p)
    assert recargado.estado == EstadoPaquete.RECIBIDO
    assert recargado.sin_apartamento_motivo is None


def test_sin_catalogo_no_se_exige_porque_el_modal_no_ofrece_el_selector(client, monkeypatch):
    from app.web.routes import packages

    monkeypatch.setattr(packages, "_hay_catalogo_de_apartamentos", lambda db: False)
    p = _preparar(client, con_catalogo=False)

    r = _recibir(client, p)

    assert r.status_code == 303
    assert _recargar(client, p).estado == EstadoPaquete.RECIBIDO


def test_el_modal_trae_el_aviso_el_boton_deshabilitado_y_el_dialogo(client):
    p = _preparar(client)

    html = client.get("/paquetes").text

    assert "Este cliente no tiene apartamento" in html
    boton = re.search(rf'<button type="submit" id="recibir-boton-{p.id}"[^>]*>', html).group(0)
    assert " disabled" in boton
    assert "Elige la torre para poder recibir." in html
    assert f'data-open="modal-sin-apto-{p.id}"' in html
    assert "¿Seguro que la etiqueta no trae el apartamento?" in html
    assert f'id="sin-apto-{p.id}-regresar"' in html
    assert html.count(f'name="sin_apartamento_motivo"') >= 4  # 3 radios del diálogo + el oculto del form
    assert f'id="picker-apto-input-recibir-{p.id}"' in html
    assert "data-picker-autoelegir-unica" in html


def test_el_picker_de_asignar_apartamento_no_elige_la_torre_solo(client):
    p = _preparar(client)

    html = client.get("/paquetes").text

    tag = re.search(rf'<input [^>]*id="picker-apto-input-asignar-{p.id}"[^>]*>', html).group(0)
    assert "data-picker-autoelegir-unica" not in tag


def test_chip_sin_apartamento_en_paquetes_y_consultar(client):
    p = _preparar(client)
    _recibir(client, p, sin_apartamento_motivo="etiqueta")

    lista = client.get("/paquetes?estado=RECIBIDO").text
    consulta = client.get(f"/consultar?q={p.access_code}").text

    for html in (lista, consulta):
        assert "data-etiqueta-sin-apartamento" in html
        assert 'title="Se recibió sin apartamento: La etiqueta no lo trae"' in html


def test_sin_chip_si_luego_se_le_asigna_apartamento(client):
    p = _preparar(client)
    _recibir(client, p, sin_apartamento_motivo="etiqueta")
    recargado = _recargar(client, p)
    recargado.snapshot_conjunto, recargado.snapshot_torre, recargado.snapshot_apartamento = (
        "EL CLUB", "TORRE 1", "101"
    )
    client.db.commit()

    assert "data-etiqueta-sin-apartamento" not in client.get("/paquetes?estado=RECIBIDO").text


# --- "Recibir sin fotos" con motivo (mismo pedido, misma migración 0073_motivos_recibir) ---


def test_sin_fotos_sin_motivo_se_rechaza(client):
    p = _preparar(client)

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"posicion": "41", "sin_fotos": "1", "sin_apartamento_motivo": "etiqueta"},
        follow_redirects=False,
    )

    assert r.status_code == 400
    assert _modal_abierto(r.text, p.id)
    assert ERROR_MOTIVO_SIN_FOTOS in r.text
    assert _recargar(client, p).estado == EstadoPaquete.ANUNCIADO


def test_sin_fotos_motivo_otro_corto_se_rechaza(client):
    p = _preparar(client)

    r = _recibir(client, p, sin_apartamento_motivo="etiqueta", sin_fotos_motivo="otro", sin_fotos_otro="ab")

    assert r.status_code == 400
    assert _recargar(client, p).estado == EstadoPaquete.ANUNCIADO


def test_sin_fotos_con_motivo_lo_guarda_y_sale_en_el_chip(client):
    p = _preparar(client)

    r = _recibir(client, p, sin_apartamento_motivo="etiqueta", sin_fotos_motivo="no_fotografiable")

    assert r.status_code == 303
    recargado = _recargar(client, p)
    assert recargado.recibido_sin_fotos is True
    assert recargado.sin_fotos_motivo == "no_fotografiable"
    assert 'title="Se recibió sin fotos: El paquete no se puede fotografiar"' in client.get("/paquetes?estado=RECIBIDO").text


def test_con_fotos_en_cola_el_motivo_sin_fotos_no_se_guarda(client):
    p = _preparar(client)

    r = _recibir(client, p, sin_apartamento_motivo="etiqueta", fotos_en_cola="1")

    assert r.status_code == 303
    recargado = _recargar(client, p)
    assert recargado.recibido_sin_fotos is False
    assert recargado.sin_fotos_motivo is None


def test_sin_apartamento_ni_fotos_el_apartamento_va_primero(client):
    p = _preparar(client)

    r = client.post(f"/paquetes/{p.id}/recibir", data={"posicion": "41"}, follow_redirects=False)

    assert r.status_code == 400
    assert ERROR_RECIBIR_SIN_APARTAMENTO in r.text


def test_el_modal_trae_el_dialogo_sin_fotos_sin_la_casilla(client):
    p = _preparar(client)

    html = client.get("/paquetes").text

    assert f'data-open="modal-sin-fotos-{p.id}"' in html
    assert "¿Seguro que quieres recibir sin fotos?" in html
    assert 'type="checkbox" name="sin_fotos"' not in html
    for valor in ("camara", "no_fotografiable", "otro"):
        assert f'name="sin_fotos_motivo" value="{valor}"' in html
