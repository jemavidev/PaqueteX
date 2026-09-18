# -*- coding: utf-8 -*-
"""
`fuentes_existentes` / `listar_todos_los_contactos_externos` --
`.scratch/contactos-externos-import-export`, contra el Postgres efímero.
"""

import pytest

from app.domain.contacto_externo_service import (
    FUENTE_GOOGLE_CONTACTS,
    FUENTE_PRODUCCION_V1,
    FilaFuenteContacto,
    fuentes_existentes,
    importar_contactos_externos,
    listar_todos_los_contactos_externos,
)

pytestmark = pytest.mark.integration


def test_fuentes_existentes_sin_contactos_es_lista_vacia(db_session):
    assert fuentes_existentes(db_session) == []


def test_fuentes_existentes_sin_duplicados_y_ordenadas(db_session):
    importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(nombre="Juan", telefonos=("3001111111",), fuente=FUENTE_PRODUCCION_V1),
            FilaFuenteContacto(nombre="Ana", telefonos=("3002222222",), fuente=FUENTE_GOOGLE_CONTACTS),
        ],
    )
    db_session.commit()

    assert fuentes_existentes(db_session) == sorted({FUENTE_PRODUCCION_V1, FUENTE_GOOGLE_CONTACTS})


def test_listar_todos_trae_todos_sin_paginar(db_session):
    importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(nombre=f"Contacto {i}", telefonos=(f"300111{i:04d}",), fuente=FUENTE_PRODUCCION_V1)
            for i in range(25)
        ],
    )
    db_session.commit()

    contactos = listar_todos_los_contactos_externos(db_session)
    assert len(contactos) == 25


def test_listar_todos_trae_telefonos_y_whatsapps_precargados(db_session):
    importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(
                nombre="Juan Perez",
                telefonos=("3001234567",),
                whatsapps=("juan.whatsapp",),
                fuente=FUENTE_PRODUCCION_V1,
            )
        ],
    )
    db_session.commit()

    contacto = listar_todos_los_contactos_externos(db_session)[0]
    assert contacto.telefonos_cargados == ["+573001234567"]
    assert contacto.whatsapps_cargados == ["juan.whatsapp"]
