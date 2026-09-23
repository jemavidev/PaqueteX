# -*- coding: utf-8 -*-
"""
Catálogo numerado de fuentes de Contactos externos (issue 362,
`.scratch/pendientes-cliente`): cada fuente tiene un número PERMANENTE que dice
cuál se agregó primero, cuál segundo, etc. Contra el Postgres efímero.

Dos seams: el servicio (`obtener_o_crear_fuente` / `listar_fuentes` /
`importar_contactos_externos` / `buscar_contactos_externos`) y el EFECTO de la
migración `0054` (relleno con las fuentes que ya existían), mismo espíritu que
`test_apartamento_seed.py`.
"""

import pytest
from sqlalchemy import create_engine, text

import _harness as H
from app.domain.contacto_externo import ContactoExterno
from app.domain.contacto_externo_service import (
    FUENTE_GOOGLE_CONTACTS,
    MAX_LARGO_FUENTE,
    FilaFuenteContacto,
    buscar_contactos_externos,
    importar_contactos_externos,
    listar_fuentes,
    obtener_o_crear_fuente,
)

pytestmark = pytest.mark.integration


def _pares(session):
    return [(f.numero, f.nombre) for f in listar_fuentes(session)]


# --- obtener_o_crear_fuente ---


def test_con_el_catalogo_vacio_la_primera_fuente_recibe_el_1(db_session):
    fuente = obtener_o_crear_fuente(db_session, "Whatsapp")
    assert fuente.numero == 1
    assert fuente.nombre == "Whatsapp"


def test_cada_fuente_nueva_recibe_el_siguiente_numero(db_session):
    for nombre in ("Whatsapp", "Paquetes", "ACTUALIZACION"):
        obtener_o_crear_fuente(db_session, nombre)

    assert _pares(db_session) == [(1, "Whatsapp"), (2, "Paquetes"), (3, "ACTUALIZACION")]


def test_la_misma_fuente_con_otra_grafia_reutiliza_la_existente(db_session):
    original = obtener_o_crear_fuente(db_session, "Whatsapp")

    for variante in ("whatsapp", "WHATSAPP", "  Whatsapp "):
        assert obtener_o_crear_fuente(db_session, variante).numero == original.numero

    assert _pares(db_session) == [(1, "Whatsapp")]


def test_espacios_internos_de_mas_no_crean_otra_fuente(db_session):
    a = obtener_o_crear_fuente(db_session, "Mi fuente")
    b = obtener_o_crear_fuente(db_session, "Mi    fuente")
    assert a.numero == b.numero == 1


def test_el_numero_de_una_fuente_no_cambia_al_agregar_otras(db_session):
    primera = obtener_o_crear_fuente(db_session, "Whatsapp").numero
    obtener_o_crear_fuente(db_session, "Paquetes")
    obtener_o_crear_fuente(db_session, "ACTUALIZACION")

    assert obtener_o_crear_fuente(db_session, "whatsapp").numero == primera == 1


def test_una_fuente_nueva_con_el_nombre_de_otra_pero_distinto_no_se_confunde(db_session):
    obtener_o_crear_fuente(db_session, "Paquetes")
    assert obtener_o_crear_fuente(db_session, "Paquetes 2").numero == 2


def test_fuente_vacia_se_rechaza(db_session):
    for vacia in ("", "   ", None):
        with pytest.raises(ValueError):
            obtener_o_crear_fuente(db_session, vacia)
    assert _pares(db_session) == []


def test_fuente_de_mas_de_40_caracteres_se_rechaza(db_session):
    with pytest.raises(ValueError):
        obtener_o_crear_fuente(db_session, "x" * (MAX_LARGO_FUENTE + 1))
    assert obtener_o_crear_fuente(db_session, "x" * MAX_LARGO_FUENTE).numero == 1


def test_listar_fuentes_va_en_orden_de_numero(db_session):
    for nombre in ("Zeta", "Alfa", "Medio"):
        obtener_o_crear_fuente(db_session, nombre)

    assert [f.nombre for f in listar_fuentes(db_session)] == ["Zeta", "Alfa", "Medio"]


# --- importar_contactos_externos registra las fuentes ---


def _fila(nombre, tel, fuente):
    return FilaFuenteContacto(nombre=nombre, telefonos=(tel,), fuente=fuente)


def test_importar_registra_las_fuentes_en_el_orden_en_que_aparecen(db_session):
    importar_contactos_externos(
        db_session,
        [
            _fila("Uno", "3001111111", "Segunda letra"),
            _fila("Dos", "3002222222", "Primera letra"),
        ],
    )

    assert _pares(db_session) == [(1, "Segunda letra"), (2, "Primera letra")]


def test_importar_una_fuente_nueva_recibe_el_siguiente_numero(db_session):
    importar_contactos_externos(db_session, [_fila("Uno", "3001111111", "Whatsapp")])
    importar_contactos_externos(db_session, [_fila("Dos", "3002222222", "Paquetes")])

    assert _pares(db_session) == [(1, "Whatsapp"), (2, "Paquetes")]


def test_importar_con_otra_grafia_reutiliza_la_fuente_y_guarda_la_grafia_del_catalogo(db_session):
    importar_contactos_externos(db_session, [_fila("Uno", "3001111111", "Whatsapp")])
    importar_contactos_externos(db_session, [_fila("Dos", "3002222222", "whatsapp")])

    assert _pares(db_session) == [(1, "Whatsapp")]
    dos = db_session.query(ContactoExterno).filter_by(nombre="Dos").one()
    assert dos.fuentes == ["Whatsapp"]


def test_importar_un_archivo_sin_filas_validas_no_consume_un_numero(db_session):
    # Fila sin nombre ni identificador: se descarta -- su fuente nueva no debe
    # quedar registrada (el siguiente archivo válido seguiría en el 1).
    importar_contactos_externos(
        db_session, [FilaFuenteContacto(nombre="", telefonos=(), fuente="Fuente fantasma")]
    )
    assert _pares(db_session) == []

    importar_contactos_externos(db_session, [_fila("Uno", "3001111111", "Real")])
    assert _pares(db_session) == [(1, "Real")]


def test_reimportar_el_mismo_archivo_no_crea_fuentes_nuevas(db_session):
    filas = [_fila("Uno", "3001111111", "Whatsapp")]
    importar_contactos_externos(db_session, filas)
    importar_contactos_externos(db_session, filas)

    assert _pares(db_session) == [(1, "Whatsapp")]


def test_un_contacto_enriquecido_por_otra_fuente_suma_su_fuente_una_sola_vez(db_session):
    importar_contactos_externos(db_session, [_fila("Uno", "3001111111", "Whatsapp")])
    importar_contactos_externos(db_session, [_fila("Uno", "3001111111", "Paquetes")])
    importar_contactos_externos(db_session, [_fila("Uno", "3001111111", "whatsapp")])

    uno = db_session.query(ContactoExterno).one()
    assert sorted(uno.fuentes) == ["Paquetes", "Whatsapp"]


# --- buscar_contactos_externos numera las fuentes de cada contacto ---


def test_fuentes_numeradas_van_de_menor_a_mayor_aunque_el_nombre_ordene_distinto(db_session):
    importar_contactos_externos(db_session, [_fila("Uno", "3001111111", "Whatsapp")])
    importar_contactos_externos(db_session, [_fila("Uno", "3001111111", "Paquetes")])
    importar_contactos_externos(db_session, [_fila("Uno", "3001111111", "ACTUALIZACION")])

    (contacto,), _, _ = buscar_contactos_externos(db_session)

    # Guardadas alfabéticamente (ACTUALIZACION, Paquetes, Whatsapp) pero
    # numeradas por antigüedad.
    assert contacto.fuentes_numeradas == [(1, "Whatsapp"), (2, "Paquetes"), (3, "ACTUALIZACION")]


def test_fuentes_numeradas_no_repite_una_fuente_guardada_con_dos_grafias(db_session):
    obtener_o_crear_fuente(db_session, "Whatsapp")
    db_session.add(ContactoExterno(nombre="Uno", fuentes=["Whatsapp", "whatsapp"]))
    db_session.flush()

    (contacto,), _, _ = buscar_contactos_externos(db_session)

    assert contacto.fuentes_numeradas == [(1, "Whatsapp")]


def test_una_fuente_fuera_del_catalogo_queda_al_final_sin_numero(db_session):
    obtener_o_crear_fuente(db_session, "Whatsapp")
    db_session.add(ContactoExterno(nombre="Uno", fuentes=["Rara", "Whatsapp"]))
    db_session.flush()

    (contacto,), _, _ = buscar_contactos_externos(db_session)

    assert contacto.fuentes_numeradas == [(1, "Whatsapp"), (None, "Rara")]


def test_el_catalogo_no_depende_de_que_haya_contactos(db_session):
    obtener_o_crear_fuente(db_session, FUENTE_GOOGLE_CONTACTS)
    assert buscar_contactos_externos(db_session) == ([], 1, 0)
    assert _pares(db_session) == [(1, FUENTE_GOOGLE_CONTACTS)]


# --- Migración 0054: relleno con las fuentes que ya existían ---

_ANTERIOR = "0053_indices_contactos_telefono"


def _sembrar_contactos(url, contactos):
    """`contactos`: lista de `(nombre, [fuentes], created_at_iso)` -- SQL
    directo sobre el esquema anterior a la migración."""
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for nombre, fuentes, creado in contactos:
                conn.execute(
                    text(
                        "INSERT INTO contactos_externos (id, nombre, fuentes, created_at, updated_at) "
                        "VALUES (gen_random_uuid(), :nombre, :fuentes, :creado, :creado)"
                    ),
                    {"nombre": nombre, "fuentes": fuentes, "creado": creado},
                )
    finally:
        engine.dispose()


def _catalogo(url):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT numero, nombre FROM fuentes_contactos_externos ORDER BY numero")
            ).all()
    finally:
        engine.dispose()
    return [tuple(r) for r in rows]


def test_migracion_respeta_el_orden_confirmado_por_el_cliente(empty_db_url):
    # Los datos reales: las tres fuentes comparten el mismo instante de primera
    # aparición, así que el orden NO sale de los datos -- 1 Whatsapp, 2
    # Paquetes, 3 ACTUALIZACION lo confirmó el cliente.
    H.run_alembic(empty_db_url, "upgrade", _ANTERIOR)
    t = "2026-09-17T12:43:43+00:00"
    _sembrar_contactos(
        empty_db_url,
        [
            ("A", ["ACTUALIZACION", "Whatsapp"], t),
            ("B", ["ACTUALIZACION", "Paquetes"], t),
            ("C", ["ACTUALIZACION", "Paquetes", "Whatsapp"], t),
        ],
    )

    H.run_alembic(empty_db_url, "upgrade", "head")

    assert _catalogo(empty_db_url) == [(1, "Whatsapp"), (2, "Paquetes"), (3, "ACTUALIZACION")]


def test_migracion_ignora_mayusculas_al_aplicar_el_orden_confirmado(empty_db_url):
    H.run_alembic(empty_db_url, "upgrade", _ANTERIOR)
    t = "2026-09-17T12:43:43+00:00"
    _sembrar_contactos(empty_db_url, [("A", ["actualizacion", "PAQUETES", "whatsapp"], t)])

    H.run_alembic(empty_db_url, "upgrade", "head")

    assert _catalogo(empty_db_url) == [(1, "whatsapp"), (2, "PAQUETES"), (3, "actualizacion")]


def test_migracion_pone_las_otras_fuentes_despues_por_fecha_y_luego_nombre(empty_db_url):
    H.run_alembic(empty_db_url, "upgrade", _ANTERIOR)
    _sembrar_contactos(
        empty_db_url,
        [
            ("A", ["Whatsapp", "produccion_v1"], "2026-09-10T10:00:00+00:00"),
            ("B", ["google_contacts"], "2026-09-01T10:00:00+00:00"),
            ("C", ["Paquetes", "zeta", "alfa"], "2026-09-20T10:00:00+00:00"),
        ],
    )

    H.run_alembic(empty_db_url, "upgrade", "head")

    # Las confirmadas primero (Whatsapp, Paquetes); luego el resto por fecha
    # de su primer contacto -- google_contacts (1 sep), produccion_v1 (10 sep),
    # y las dos del 20 sep desempatan por nombre (alfa, zeta).
    assert _catalogo(empty_db_url) == [
        (1, "Whatsapp"),
        (2, "Paquetes"),
        (3, "google_contacts"),
        (4, "produccion_v1"),
        (5, "alfa"),
        (6, "zeta"),
    ]


def test_migracion_junta_las_grafias_de_una_misma_fuente_y_gana_la_mas_usada(empty_db_url):
    H.run_alembic(empty_db_url, "upgrade", _ANTERIOR)
    t = "2026-09-17T12:43:43+00:00"
    _sembrar_contactos(
        empty_db_url,
        [
            ("A", ["whatsapp"], t),
            ("B", ["whatsapp"], t),
            ("C", ["Whatsapp"], t),
        ],
    )

    H.run_alembic(empty_db_url, "upgrade", "head")

    assert _catalogo(empty_db_url) == [(1, "whatsapp")]


def test_migracion_sin_contactos_deja_el_catalogo_vacio(empty_db_url):
    H.run_alembic(empty_db_url, "upgrade", "head")
    assert _catalogo(empty_db_url) == []


def test_migracion_downgrade_quita_el_catalogo_y_conserva_las_fuentes_de_los_contactos(empty_db_url):
    H.run_alembic(empty_db_url, "upgrade", _ANTERIOR)
    _sembrar_contactos(empty_db_url, [("A", ["Whatsapp"], "2026-09-17T12:43:43+00:00")])
    H.run_alembic(empty_db_url, "upgrade", "head")
    assert _catalogo(empty_db_url) == [(1, "Whatsapp")]

    H.run_alembic(empty_db_url, "downgrade", _ANTERIOR)

    engine = create_engine(empty_db_url)
    try:
        with engine.connect() as conn:
            existe = conn.execute(
                text("SELECT to_regclass('public.fuentes_contactos_externos')")
            ).scalar()
            fuentes = conn.execute(text("SELECT fuentes FROM contactos_externos")).scalar()
    finally:
        engine.dispose()
    assert existe is None
    assert fuentes == ["Whatsapp"]
