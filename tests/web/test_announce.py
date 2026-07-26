# -*- coding: utf-8 -*-
"""
Capa web — ruta `/announce` (ticket 02).

Comportamiento observable por HTTP: el formulario, la creación (o no) del Paquete
`ANUNCIADO` con su snapshot, los tres casos de "a nombre de", y las validaciones
sin efecto en la BD. La verificación del efecto se hace contra `client.db`.
"""

from app.domain.apartamento_service import (
    get_or_create_apartamento,
    set_apartamento_actual,
)
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.persona import Persona
from app.domain.persona_service import get_or_create_persona


def _cuenta_paquetes(client) -> int:
    return client.db.query(Paquete).count()


def test_get_announce_renderiza_el_formulario_sin_guia(client):
    r = client.get("/anunciar")
    assert r.status_code == 200
    html = r.text.lower()
    assert 'name="nombre"' in html
    assert 'name="telefono"' in html
    assert 'name="acepta_tyc"' in html
    assert "yo_mismo" in html and "registrada" in html and "solo_nombre" in html
    # Sin captura de número de guía (la captura el staff al recibir).
    assert "guide" not in html and "guía" not in html and 'name="guia"' not in html


def test_post_para_si_mismo_crea_paquete_anunciado(client):
    r = client.post(
        "/anunciar",
        data={
            "nombre": "Ana",
            "telefono": "3001234567",
            "acepta_tyc": "on",
            "a_nombre_de": "yo_mismo",
        },
    )
    assert r.status_code == 200
    paquetes = client.db.query(Paquete).all()
    assert len(paquetes) == 1
    p = paquetes[0]
    assert p.estado == EstadoPaquete.ANUNCIADO
    assert p.recipient_name == "Ana"
    assert p.recipient_phone == "+573001234567"
    # La confirmación muestra el número de seguimiento.
    assert p.tracking_number in r.text


def test_post_a_nombre_de_persona_registrada(client):
    get_or_create_persona(client.db, "3019999999", "Beto")
    client.db.commit()

    r = client.post(
        "/anunciar",
        data={
            "nombre": "Ana",
            "telefono": "3001234567",
            "acepta_tyc": "on",
            "a_nombre_de": "registrada",
            "destinatario_telefono": "3019999999",
        },
    )
    assert r.status_code == 200
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "Beto"
    assert p.recipient_phone == "+573019999999"


def test_post_solo_nombre_sin_telefono_no_crea_persona_sin_llave(client):
    r = client.post(
        "/anunciar",
        data={
            "nombre": "Ana",
            "telefono": "3001234567",
            "acepta_tyc": "on",
            "a_nombre_de": "solo_nombre",
            "destinatario_nombre": "Carlos",
        },
    )
    assert r.status_code == 200
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "Carlos"
    assert p.recipient_phone is None
    # 'Carlos' no es una Persona: solo existe el anunciante (Ana).
    assert client.db.query(Persona).count() == 1


def test_post_sin_tyc_no_crea_paquete(client):
    r = client.post(
        "/anunciar",
        data={"nombre": "Ana", "telefono": "3001234567", "a_nombre_de": "yo_mismo"},
    )
    assert r.status_code == 400
    assert _cuenta_paquetes(client) == 0


def test_post_sin_telefono_no_crea_paquete(client):
    r = client.post(
        "/anunciar",
        data={"nombre": "Ana", "acepta_tyc": "on", "a_nombre_de": "yo_mismo"},
    )
    assert r.status_code == 400
    assert _cuenta_paquetes(client) == 0


def test_post_sin_nombre_no_crea_paquete(client):
    r = client.post(
        "/anunciar",
        data={"telefono": "3001234567", "acepta_tyc": "on", "a_nombre_de": "yo_mismo"},
    )
    assert r.status_code == 400
    assert _cuenta_paquetes(client) == 0


def test_post_registrada_no_existente_da_error_y_no_registra_al_anunciante(client):
    r = client.post(
        "/anunciar",
        data={
            "nombre": "Ana",
            "telefono": "3001234567",
            "acepta_tyc": "on",
            "a_nombre_de": "registrada",
            "destinatario_telefono": "3050000000",
        },
    )
    assert r.status_code == 400
    assert _cuenta_paquetes(client) == 0
    # La rama de error hizo rollback: el anunciante NO quedó registrado a medias.
    assert (
        client.db.query(Persona).filter(Persona.telefono == "+573001234567").count() == 0
    )


def test_a_nombre_de_casual_no_agrupa_apartamento(client):
    # Ana (con apartamento) anuncia a nombre de Beto (registrado, sin apartamento).
    get_or_create_persona(client.db, "3001234567", "Ana")
    apto = get_or_create_apartamento(client.db, "Las Flores", "Torre A", "101")
    set_apartamento_actual(client.db, "3001234567", apto)
    get_or_create_persona(client.db, "3019999999", "Beto")
    client.db.commit()

    r = client.post(
        "/anunciar",
        data={
            "nombre": "Ana",
            "telefono": "3001234567",
            "acepta_tyc": "on",
            "a_nombre_de": "registrada",
            "destinatario_telefono": "3019999999",
        },
    )
    assert r.status_code == 200

    beto = client.db.query(Persona).filter(Persona.telefono == "+573019999999").one()
    client.db.refresh(beto)
    # El favor puntual NO agrupó a Beto en el apartamento de Ana.
    assert beto.apartamento_actual_id is None
