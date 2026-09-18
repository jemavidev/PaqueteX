# -*- coding: utf-8 -*-
"""
Seam 2 — importación incremental de `ContactoExterno`, contra el Postgres
efímero construido con `alembic upgrade head`.
"""

import pytest

from app.domain.contacto_externo import ContactoExterno, ContactoExternoTelefono, ContactoExternoWhatsapp
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


def test_lote_nuevo_enriquece_y_actualiza_el_nombre_al_mas_reciente(db_session):
    """`.scratch/contactos-externos-import-export`: a diferencia del import
    histórico original (que nunca sobreescribía el nombre), los imports
    recurrentes hechos por el propio admin sí actualizan el nombre al valor
    de la fila más reciente -- decisión explícita del cliente (grilling,
    pregunta 10)."""
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
    assert contacto.nombre == "JUAN PEREZ GOMEZ"  # gana el más reciente
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


# --- WhatsApp como segunda llave (.scratch/contactos-externos-import-export) ---


def test_lote_enriquece_contacto_existente_por_coincidencia_de_whatsapp(db_session):
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

    # El teléfono de esta fila es NUEVO (no coincide con ninguno guardado) --
    # solo el WhatsApp coincide con el contacto ya existente.
    resumen = importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(
                nombre="Juan Perez",
                telefonos=("3009999999",),
                whatsapps=("juan.whatsapp",),
                fuente=FUENTE_GOOGLE_CONTACTS,
            )
        ],
    )
    db_session.commit()

    assert resumen.enriquecidos == 1
    assert db_session.query(ContactoExterno).count() == 1
    telefonos = {t.telefono for t in db_session.query(ContactoExternoTelefono).all()}
    assert telefonos == {"+573001234567", "+573009999999"}


def test_whatsapp_nuevo_se_suma_sin_tocar_los_que_ya_tenia(db_session):
    importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(
                nombre="Juan Perez",
                telefonos=("3001234567",),
                whatsapps=("juan.personal",),
                fuente=FUENTE_PRODUCCION_V1,
            )
        ],
    )
    db_session.commit()

    importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(
                nombre="Juan Perez",
                telefonos=("3001234567",),
                whatsapps=("juan.negocio",),
                fuente=FUENTE_GOOGLE_CONTACTS,
            )
        ],
    )
    db_session.commit()

    contacto = db_session.query(ContactoExterno).one()
    whatsapps = {
        w.whatsapp_usuario
        for w in db_session.query(ContactoExternoWhatsapp)
        .filter(ContactoExternoWhatsapp.contacto_externo_id == contacto.id)
        .all()
    }
    assert whatsapps == {"juan.personal", "juan.negocio"}


def test_teléfono_y_whatsapp_previos_nunca_desaparecen_aunque_el_lote_nuevo_no_los_traiga(db_session):
    importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(
                nombre="Juan Perez",
                telefonos=("3001234567", "3009999999"),
                whatsapps=("juan.personal",),
                fuente=FUENTE_PRODUCCION_V1,
            )
        ],
    )
    db_session.commit()

    # Fila nueva que solo trae UNO de los dos teléfonos y NADA de WhatsApp.
    importar_contactos_externos(
        db_session,
        [FilaFuenteContacto(nombre="Juan Perez Actualizado", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS)],
    )
    db_session.commit()

    contacto = db_session.query(ContactoExterno).one()
    telefonos = {
        t.telefono
        for t in db_session.query(ContactoExternoTelefono)
        .filter(ContactoExternoTelefono.contacto_externo_id == contacto.id)
        .all()
    }
    whatsapps = {
        w.whatsapp_usuario
        for w in db_session.query(ContactoExternoWhatsapp)
        .filter(ContactoExternoWhatsapp.contacto_externo_id == contacto.id)
        .all()
    }
    assert telefonos == {"+573001234567", "+573009999999"}  # ninguno desaparece
    assert whatsapps == {"juan.personal"}  # tampoco el whatsapp


def test_lote_que_conecta_dos_contactos_por_whatsapp_se_reporta_sin_fusionar(db_session):
    importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(nombre="Juan", telefonos=(), whatsapps=("juan.uno",), fuente=FUENTE_PRODUCCION_V1),
            FilaFuenteContacto(nombre="Ana", telefonos=(), whatsapps=("ana.dos",), fuente=FUENTE_PRODUCCION_V1),
        ],
    )
    db_session.commit()
    assert db_session.query(ContactoExterno).count() == 2

    resumen = importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(
                nombre="Juan y Ana", telefonos=(), whatsapps=("juan.uno", "ana.dos"), fuente=FUENTE_GOOGLE_CONTACTS
            )
        ],
    )
    db_session.commit()

    assert len(resumen.conflictos) == 1
    assert db_session.query(ContactoExterno).count() == 2


def test_lote_que_conecta_dos_contactos_cruzando_telefono_y_whatsapp_se_reporta_sin_fusionar(db_session):
    """El caso cruzado: el teléfono de la fila nueva coincide con un
    contacto (X) y su WhatsApp coincide con OTRO contacto distinto (Y)."""
    importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(nombre="Juan", telefonos=("3001111111",), fuente=FUENTE_PRODUCCION_V1),
            FilaFuenteContacto(nombre="Ana", telefonos=(), whatsapps=("ana.whatsapp",), fuente=FUENTE_PRODUCCION_V1),
        ],
    )
    db_session.commit()
    assert db_session.query(ContactoExterno).count() == 2

    resumen = importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(
                nombre="Juan y Ana",
                telefonos=("3001111111",),
                whatsapps=("ana.whatsapp",),
                fuente=FUENTE_GOOGLE_CONTACTS,
            )
        ],
    )
    db_session.commit()

    assert len(resumen.conflictos) == 1
    assert db_session.query(ContactoExterno).count() == 2


def test_fila_descartada_queda_contada_en_el_resumen(db_session):
    resumen = importar_contactos_externos(
        db_session,
        [
            FilaFuenteContacto(nombre="Juan", telefonos=("3001234567",), fuente=FUENTE_PRODUCCION_V1),
            FilaFuenteContacto(nombre="Sin identificador", telefonos=(), whatsapps=(), fuente=FUENTE_PRODUCCION_V1),
        ],
    )
    db_session.commit()

    assert resumen.creados == 1
    assert len(resumen.descartados) == 1
