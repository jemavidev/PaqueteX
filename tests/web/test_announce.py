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


def test_nombre_declarado_con_typo_usa_el_nombre_registrado_del_anunciante(client):
    # Ana ya está registrada; alguien anuncia con su teléfono pero escribe mal
    # el nombre -- conversación 2026-08-15 (pedido explícito): el nombre
    # escrito solo se honra si coincide con un co-residente de la MISMA
    # unidad del anunciante; sin unidad (este caso) o sin esa coincidencia,
    # el anuncio queda a nombre del propio Anunciante YA REGISTRADO, no del
    # texto tal cual lo escribió.
    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()

    r = client.post(
        "/anunciar",
        data={"nombre": "Ana Peres", "telefono": "3001234567", "acepta_tyc": "on"},
    )
    assert r.status_code == 200
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "ANA PEREZ"
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


# --------------------------------------------------------------------------- #
# Foco condicional (versión móvil, `.scratch/pendientes-cliente`): autofocus
# SOLO en una carga limpia -- con error, activarlo dispara el teclado y tapa
# el mensaje de error en mobile.
# --------------------------------------------------------------------------- #
def test_get_announce_limpio_tiene_autofocus(client):
    r = client.get("/anunciar")
    assert "autofocus" in r.text


def test_post_announce_con_error_no_tiene_autofocus(client):
    r = client.post("/anunciar", data={"telefono": "3001234567"})
    assert r.status_code == 400
    assert "autofocus" not in r.text


# --------------------------------------------------------------------------- #
# Límite de anuncios activos por teléfono (`.scratch/pendientes-cliente`,
# grillado con el cliente) -- evita ráfagas de SMS por error o abuso.
# --------------------------------------------------------------------------- #
def _anunciar(client, telefono="3001234567", nombre="Ana", confirmar=False):
    data = {"nombre": nombre, "telefono": telefono, "acepta_tyc": "on"}
    if confirmar:
        data["confirmar_multiple"] = "1"
    return client.post("/anunciar", data=data)


def test_primer_anuncio_no_muestra_pantalla_intermedia(client):
    r = _anunciar(client)
    assert r.status_code == 200
    assert "¿Quieres anunciar otro" not in r.text
    assert _cuenta_paquetes(client) == 1


def test_segundo_anuncio_muestra_pantalla_intermedia_sin_crear_el_paquete(client):
    _anunciar(client)
    r = _anunciar(client)
    assert r.status_code == 200
    assert "Ya tienes 1" in r.text
    assert "¿Quieres anunciar otro" in r.text
    assert _cuenta_paquetes(client) == 1  # el segundo NO se creó todavía


def test_pantalla_intermedia_nunca_menciona_el_codigo_de_acceso_existente(client):
    r1 = _anunciar(client)
    p1 = client.db.query(Paquete).one()
    assert p1.access_code in r1.text  # sí aparece en SU propia confirmación

    r2 = _anunciar(client)
    assert p1.access_code not in r2.text  # pero NUNCA en el aviso del 2do intento


def test_confirmar_multiple_crea_el_segundo_paquete(client):
    _anunciar(client)
    r = _anunciar(client, confirmar=True)
    assert r.status_code == 200
    assert _cuenta_paquetes(client) == 2


def test_confirmar_multiple_de_otro_telefono_no_afecta_este(client):
    _anunciar(client, telefono="3001234567")
    r = _anunciar(client, telefono="3019999999", nombre="Beto")
    assert r.status_code == 200
    assert "¿Quieres anunciar otro" not in r.text  # Beto nunca ha anunciado
    assert _cuenta_paquetes(client) == 2


def test_llegar_al_maximo_bloquea_sin_opcion_de_confirmar(client):
    from app.domain.paquete_service import MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO

    for _ in range(MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO):
        r = _anunciar(client, confirmar=True)
    assert _cuenta_paquetes(client) == MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO

    r = _anunciar(client, confirmar=True)  # el 11vo, incluso confirmando
    assert r.status_code == 400
    assert "máximo" in r.text.lower()
    assert _cuenta_paquetes(client) == MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO  # sin cambios


def test_recibir_uno_libera_espacio_bajo_el_limite(client):
    from app.domain.paquete_lifecycle import receive
    from app.domain.paquete_service import MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO
    from app.domain.staff_service import create_initial_admin

    staff = create_initial_admin(client.db, "admin@club.com", "Admin", "Contrasena1")

    for _ in range(MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO):
        _anunciar(client, confirmar=True)

    primero = client.db.query(Paquete).order_by(Paquete.created_at.asc()).first()
    receive(client.db, primero, staff)
    client.db.commit()

    r = _anunciar(client, confirmar=True)
    assert r.status_code == 200
    assert _cuenta_paquetes(client) == MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO + 1
