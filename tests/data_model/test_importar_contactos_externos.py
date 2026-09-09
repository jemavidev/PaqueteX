# -*- coding: utf-8 -*-
"""
Seam 2 — importación incremental de `ContactoExterno`, contra el Postgres
efímero construido con `alembic upgrade head`.
"""

import pytest

from app.domain.contacto_externo import ContactoExterno, ContactoExternoTelefono
from app.domain.contacto_externo_service import (
    FUENTE_GOOGLE_CONTACTS,
    FUENTE_PRODUCCION_V1,
    FilaFuenteContacto,
    importar_contactos_externos,
)

pytestmark = pytest.mark.integration


def test_primera_importacion_crea_los_contactos(db_session):
    filas = [
        FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001234567",), fuente=FUENTE_PRODUCCION_V1),
        FilaFuenteContacto(nombre="Ana Gomez", telefonos=("3009876543",), fuente=FUENTE_GOOGLE_CONTACTS),
    ]
    resumen = importar_contactos_externos(db_session, filas)
    db_session.commit()

    assert resumen.creados == 2
    assert resumen.enriquecidos == 0
    assert db_session.query(ContactoExterno).count() == 2


def test_reimportar_el_mismo_lote_no_duplica(db_session):
    filas = [
        FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001234567",), fuente=FUENTE_PRODUCCION_V1),
    ]
    importar_contactos_externos(db_session, filas)
    db_session.commit()

    importar_contactos_externos(db_session, filas)
    db_session.commit()

    assert db_session.query(ContactoExterno).count() == 1
    assert db_session.query(ContactoExternoTelefono).count() == 1


def test_lote_nuevo_enriquece_sin_sobreescribir_nombre(db_session):
    importar_contactos_externos(
        db_session,
        [FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001234567",), fuente=FUENTE_PRODUCCION_V1)],
    )
    db_session.commit()

    resumen = importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(
                nombre="JUAN PEREZ GOMEZ",
                telefonos=("3001234567", "3009999999"),
                fuente=FUENTE_GOOGLE_CONTACTS,
            )
        ],
    )
    db_session.commit()

    assert resumen.enriquecidos == 1
    contacto = db_session.query(ContactoExterno).one()
    assert contacto.nombre == "Juan Perez"  # NO se sobreescribe
    assert set(contacto.fuentes) == {FUENTE_PRODUCCION_V1, FUENTE_GOOGLE_CONTACTS}
    telefonos = {
        t.telefono
        for t in db_session.query(ContactoExternoTelefono)
        .filter(ContactoExternoTelefono.contacto_externo_id == contacto.id)
        .all()
    }
    assert telefonos == {"+573001234567", "+573009999999"}


def test_lote_que_conecta_dos_contactos_existentes_se_reporta_sin_fusionar(db_session):
    importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(nombre="Juan", telefonos=("3001111111",), fuente=FUENTE_PRODUCCION_V1),
            FilaFuenteContacto(nombre="Ana", telefonos=("3002222222",), fuente=FUENTE_PRODUCCION_V1),
        ],
    )
    db_session.commit()
    assert db_session.query(ContactoExterno).count() == 2

    # Una fila nueva que trae AMBOS teléfonos -- conecta los 2 contactos ya
    # existentes y distintos.
    resumen = importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(
                nombre="Juan y Ana",
                telefonos=("3001111111", "3002222222"),
                fuente=FUENTE_GOOGLE_CONTACTS,
            )
        ],
    )
    db_session.commit()

    assert len(resumen.conflictos) == 1
    # No se fusionaron: siguen siendo 2 contactos separados.
    assert db_session.query(ContactoExterno).count() == 2
