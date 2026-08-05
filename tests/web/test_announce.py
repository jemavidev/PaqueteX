# -*- coding: utf-8 -*-
"""
Capa web — ruta `/anunciar` (Grupo 1 de ajustes-post-referencia-funcional).

Simplificada a 3 campos (nombre, teléfono, acepta_tyc) — el cliente ya NO
elige "a nombre de quién llega". Comportamiento observable por HTTP: el
formulario, la creación del Paquete `ANUNCIADO` con el nombre declarado tal
cual (coincida o no con el nombre ya registrado), la pantalla de éxito con
los datos nuevos, y las validaciones sin efecto en la BD.
"""

from app.domain.apartamento_service import (
    resolver_apartamento,
    set_apartamento_actual,
)
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.persona import Persona
from app.domain.persona_service import get_or_create_persona


def _cuenta_paquetes(client) -> int:
    return client.db.query(Paquete).count()


def test_get_announce_renderiza_el_formulario_de_3_campos(client):
    r = client.get("/anunciar")
    assert r.status_code == 200
    html = r.text.lower()
    assert 'name="nombre"' in html
    assert 'name="telefono"' in html
    assert 'name="acepta_tyc"' in html
    # Ya no se elige "a nombre de quién" en esta vista.
    assert "a_nombre_de" not in html
    # Sin captura de número de guía (la captura el staff al recibir).
    assert "guide" not in html and "guía" not in html and 'name="guia"' not in html


def test_post_crea_paquete_anunciado_con_el_nombre_declarado(client):
    r = client.post(
        "/anunciar",
        data={"nombre": "Ana", "telefono": "3001234567", "acepta_tyc": "on"},
    )
    assert r.status_code == 200
    paquetes = client.db.query(Paquete).all()
    assert len(paquetes) == 1
    p = paquetes[0]
    assert p.estado == EstadoPaquete.ANUNCIADO
    assert p.recipient_name == "ANA"
    # El teléfono anunciante queda como contacto por defecto de este paquete.
    assert p.recipient_phone == "+573001234567"
    assert p.announced_by_phone == "+573001234567"


def test_confirmacion_muestra_nombre_telefono_codigo_y_enlaces(client):
    r = client.post(
        "/anunciar",
        data={"nombre": "Ana", "telefono": "3001234567", "acepta_tyc": "on"},
    )
    assert r.status_code == 200
    p = client.db.query(Paquete).one()
    assert "ANA" in r.text
    assert "+573001234567" in r.text
    assert p.access_code in r.text
    assert 'href="/consultar"' in r.text
    assert 'href="/otp"' in r.text


def test_confirmacion_muestra_apartamento_cuando_el_anunciante_ya_tiene(client):
    get_or_create_persona(client.db, "3001234567", "Ana")
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    set_apartamento_actual(client.db, "3001234567", apto)
    client.db.commit()

    r = client.post(
        "/anunciar",
        data={"nombre": "Ana", "telefono": "3001234567", "acepta_tyc": "on"},
    )
    assert r.status_code == 200
    assert "EL CLUB" in r.text and "101" in r.text


def test_nombre_declarado_puede_no_coincidir_con_el_registrado(client):
    # Ana ya está registrada; alguien anuncia con su teléfono pero escribe mal
    # el nombre — el anuncio se crea igual (el staff lo resuelve después).
    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()

    r = client.post(
        "/anunciar",
        data={"nombre": "Ana Peres", "telefono": "3001234567", "acepta_tyc": "on"},
    )
    assert r.status_code == 200
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "ANA PERES"
    # No se crea una segunda Persona — el teléfono ya existía.
    assert client.db.query(Persona).count() == 1


def test_post_sin_tyc_no_crea_paquete(client):
    r = client.post(
        "/anunciar", data={"nombre": "Ana", "telefono": "3001234567"}
    )
    assert r.status_code == 400
    assert _cuenta_paquetes(client) == 0


def test_post_sin_telefono_no_crea_paquete(client):
    r = client.post("/anunciar", data={"nombre": "Ana", "acepta_tyc": "on"})
    assert r.status_code == 400
    assert _cuenta_paquetes(client) == 0


def test_post_sin_nombre_no_crea_paquete(client):
    r = client.post(
        "/anunciar", data={"telefono": "3001234567", "acepta_tyc": "on"}
    )
    assert r.status_code == 400
    assert _cuenta_paquetes(client) == 0
