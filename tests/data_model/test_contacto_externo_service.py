# -*- coding: utf-8 -*-
"""
Fusión pura de fuentes de contactos externos -- Seam 1 del módulo
"Consolidación de contactos externos" (.scratch/contactos-externos).

`fusionar_fuentes` no toca la base de datos: se prueba con filas armadas a
mano, sin sesión de BD.
"""

from app.domain.contacto_externo_service import (
    FUENTE_GOOGLE_CONTACTS,
    FUENTE_PRODUCCION_V1,
    FilaFuenteContacto,
    fusionar_fuentes,
)


def test_dos_filas_de_fuentes_distintas_con_el_mismo_telefono_se_fusionan():
    filas = [
        FilaFuenteContacto(nombre="Juan Perez", telefonos=("3001234567",), fuente=FUENTE_PRODUCCION_V1),
        FilaFuenteContacto(nombre="Juan Pérez", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS),
    ]
    consolidados = fusionar_fuentes(filas)
    assert len(consolidados) == 1
    assert consolidados[0].telefonos == frozenset({"+573001234567"})
    assert consolidados[0].fuentes == frozenset({FUENTE_PRODUCCION_V1, FUENTE_GOOGLE_CONTACTS})


def test_gana_el_nombre_de_google_contacts_cuando_difieren():
    filas = [
        FilaFuenteContacto(nombre="JUAN PEREZ", telefonos=("3001234567",), fuente=FUENTE_PRODUCCION_V1),
        FilaFuenteContacto(nombre="Juan Pérez Gómez", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS),
    ]
    consolidados = fusionar_fuentes(filas)
    assert consolidados[0].nombre == "Juan Pérez Gómez"


def test_sin_google_contacts_gana_produccion():
    filas = [
        FilaFuenteContacto(nombre="JUAN PEREZ", telefonos=("3001234567",), fuente=FUENTE_PRODUCCION_V1),
    ]
    consolidados = fusionar_fuentes(filas)
    assert consolidados[0].nombre == "JUAN PEREZ"


def test_fila_sin_nombre_se_descarta():
    filas = [
        FilaFuenteContacto(nombre="", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS),
        FilaFuenteContacto(nombre="   ", telefonos=("3009999999",), fuente=FUENTE_GOOGLE_CONTACTS),
    ]
    assert fusionar_fuentes(filas) == []


def test_fila_sin_ningun_telefono_valido_se_descarta():
    filas = [
        FilaFuenteContacto(nombre="Juan Perez", telefonos=(), fuente=FUENTE_GOOGLE_CONTACTS),
        FilaFuenteContacto(nombre="Ana", telefonos=("abc",), fuente=FUENTE_GOOGLE_CONTACTS),
    ]
    assert fusionar_fuentes(filas) == []


def test_contacto_con_dos_telefonos_queda_con_ambos():
    filas = [
        FilaFuenteContacto(
            nombre="Juan Perez", telefonos=("3001234567", "3009876543"), fuente=FUENTE_GOOGLE_CONTACTS
        ),
    ]
    consolidados = fusionar_fuentes(filas)
    assert len(consolidados) == 1
    assert consolidados[0].telefonos == frozenset({"+573001234567", "+573009876543"})


def test_telefono_colombiano_sin_mas_se_normaliza():
    filas = [FilaFuenteContacto(nombre="Ana", telefonos=("3001234567",), fuente=FUENTE_PRODUCCION_V1)]
    consolidados = fusionar_fuentes(filas)
    assert consolidados[0].telefonos == frozenset({"+573001234567"})


def test_telefono_no_reconocible_se_ignora_sin_descartar_la_fila():
    filas = [
        FilaFuenteContacto(
            nombre="Ana", telefonos=("no es un telefono", "3001234567"), fuente=FUENTE_GOOGLE_CONTACTS
        ),
    ]
    consolidados = fusionar_fuentes(filas)
    assert len(consolidados) == 1
    assert consolidados[0].telefonos == frozenset({"+573001234567"})


def test_dos_contactos_sin_telefono_en_comun_quedan_separados():
    filas = [
        FilaFuenteContacto(nombre="Juan", telefonos=("3001111111",), fuente=FUENTE_GOOGLE_CONTACTS),
        FilaFuenteContacto(nombre="Ana", telefonos=("3002222222",), fuente=FUENTE_GOOGLE_CONTACTS),
    ]
    consolidados = fusionar_fuentes(filas)
    assert len(consolidados) == 2


def test_puente_por_telefono_compartido_fusiona_tres_filas_en_un_contacto():
    """Fila A (tel X, Y) + fila B (tel Y, Z) -- comparten Y, así que las tres
    (A, B, y cualquier otra con Z) terminan en el mismo contacto."""
    filas = [
        FilaFuenteContacto(nombre="Juan", telefonos=("3001111111", "3002222222"), fuente=FUENTE_GOOGLE_CONTACTS),
        FilaFuenteContacto(nombre="J. Perez", telefonos=("3002222222", "3003333333"), fuente=FUENTE_PRODUCCION_V1),
    ]
    consolidados = fusionar_fuentes(filas)
    assert len(consolidados) == 1
    assert consolidados[0].telefonos == frozenset(
        {"+573001111111", "+573002222222", "+573003333333"}
    )
