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
    contactos_externos_a_filas_plantilla,
    fila_plantilla_a_fila_fuente,
    filas_descartadas,
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


# --- WhatsApp como segunda llave de fusión (.scratch/contactos-externos-import-export) ---


def test_dos_filas_que_comparten_solo_whatsapp_se_fusionan():
    filas = [
        FilaFuenteContacto(nombre="Juan Perez", telefonos=(), whatsapps=("jesus.villalobos",), fuente=FUENTE_PRODUCCION_V1),
        FilaFuenteContacto(nombre="Juan Pérez", telefonos=(), whatsapps=("jesus.villalobos",), fuente=FUENTE_GOOGLE_CONTACTS),
    ]
    consolidados = fusionar_fuentes(filas)
    assert len(consolidados) == 1
    assert consolidados[0].whatsapps == frozenset({"jesus.villalobos"})
    assert consolidados[0].fuentes == frozenset({FUENTE_PRODUCCION_V1, FUENTE_GOOGLE_CONTACTS})


def test_fila_con_whatsapp_y_sin_telefono_no_se_descarta():
    filas = [FilaFuenteContacto(nombre="Ana", telefonos=(), whatsapps=("ana.gomez",), fuente=FUENTE_GOOGLE_CONTACTS)]
    consolidados = fusionar_fuentes(filas)
    assert len(consolidados) == 1
    assert consolidados[0].telefonos == frozenset()
    assert consolidados[0].whatsapps == frozenset({"ana.gomez"})


def test_fila_sin_nombre_sin_telefono_y_sin_whatsapp_se_descarta():
    filas = [FilaFuenteContacto(nombre="Ana", telefonos=(), whatsapps=(), fuente=FUENTE_GOOGLE_CONTACTS)]
    assert fusionar_fuentes(filas) == []


def test_whatsapp_invalido_se_ignora_sin_descartar_la_fila():
    filas = [
        FilaFuenteContacto(
            nombre="Ana", telefonos=(), whatsapps=("no válido!", "ana.gomez"), fuente=FUENTE_GOOGLE_CONTACTS
        ),
    ]
    consolidados = fusionar_fuentes(filas)
    assert len(consolidados) == 1
    assert consolidados[0].whatsapps == frozenset({"ana.gomez"})


def test_contacto_con_dos_whatsapps_queda_con_ambos():
    filas = [
        FilaFuenteContacto(
            nombre="Juan Perez", telefonos=(), whatsapps=("jesus.villalobos", "juan.negocio"), fuente=FUENTE_GOOGLE_CONTACTS
        ),
    ]
    consolidados = fusionar_fuentes(filas)
    assert len(consolidados) == 1
    assert consolidados[0].whatsapps == frozenset({"jesus.villalobos", "juan.negocio"})


def test_whatsapp_con_arroba_y_mayusculas_normaliza_igual():
    filas = [
        FilaFuenteContacto(nombre="Juan", telefonos=(), whatsapps=("@Jesus.Villalobos",), fuente=FUENTE_GOOGLE_CONTACTS),
        FilaFuenteContacto(nombre="Juan", telefonos=(), whatsapps=("jesus.villalobos",), fuente=FUENTE_PRODUCCION_V1),
    ]
    consolidados = fusionar_fuentes(filas)
    assert len(consolidados) == 1
    assert consolidados[0].whatsapps == frozenset({"jesus.villalobos"})


def test_fusion_cruzada_telefono_de_una_fila_con_whatsapp_de_otra():
    """Fila A trae teléfono X y WhatsApp W. Fila B trae SOLO W (sin teléfono)
    -- comparten W, así que se fusionan aunque B no traiga ningún teléfono."""
    filas = [
        FilaFuenteContacto(
            nombre="Juan", telefonos=("3001234567",), whatsapps=("jesus.villalobos",), fuente=FUENTE_PRODUCCION_V1
        ),
        FilaFuenteContacto(nombre="J. Perez", telefonos=(), whatsapps=("jesus.villalobos",), fuente=FUENTE_GOOGLE_CONTACTS),
    ]
    consolidados = fusionar_fuentes(filas)
    assert len(consolidados) == 1
    assert consolidados[0].telefonos == frozenset({"+573001234567"})
    assert consolidados[0].whatsapps == frozenset({"jesus.villalobos"})


def test_dos_contactos_sin_whatsapp_ni_telefono_en_comun_quedan_separados():
    filas = [
        FilaFuenteContacto(nombre="Juan", telefonos=(), whatsapps=("juan.uno",), fuente=FUENTE_GOOGLE_CONTACTS),
        FilaFuenteContacto(nombre="Ana", telefonos=(), whatsapps=("ana.dos",), fuente=FUENTE_GOOGLE_CONTACTS),
    ]
    consolidados = fusionar_fuentes(filas)
    assert len(consolidados) == 2


# --- filas_descartadas ---


def test_filas_descartadas_reporta_fila_sin_nombre():
    filas = [FilaFuenteContacto(nombre="", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS)]
    motivos = filas_descartadas(filas)
    assert len(motivos) == 1
    assert "1" in motivos[0]


def test_filas_descartadas_reporta_fila_sin_ningun_identificador():
    filas = [FilaFuenteContacto(nombre="Ana", telefonos=(), whatsapps=(), fuente=FUENTE_GOOGLE_CONTACTS)]
    motivos = filas_descartadas(filas)
    assert len(motivos) == 1


def test_filas_descartadas_no_reporta_fila_valida():
    filas = [FilaFuenteContacto(nombre="Ana", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS)]
    assert filas_descartadas(filas) == []


def test_filas_descartadas_no_reporta_fila_valida_solo_por_whatsapp():
    filas = [FilaFuenteContacto(nombre="Ana", telefonos=(), whatsapps=("ana.gomez",), fuente=FUENTE_GOOGLE_CONTACTS)]
    assert filas_descartadas(filas) == []


def test_filas_descartadas_mezcla_filas_validas_e_invalidas():
    filas = [
        FilaFuenteContacto(nombre="Ana", telefonos=("3001234567",), fuente=FUENTE_GOOGLE_CONTACTS),
        FilaFuenteContacto(nombre="", telefonos=("3009999999",), fuente=FUENTE_GOOGLE_CONTACTS),
        FilaFuenteContacto(nombre="Sin identificador", telefonos=(), whatsapps=(), fuente=FUENTE_GOOGLE_CONTACTS),
    ]
    motivos = filas_descartadas(filas)
    assert len(motivos) == 2


# --- Plantilla de import/export (.scratch/contactos-externos-import-export) ---


def test_fila_plantilla_a_fila_fuente_separa_telefonos_y_whatsapp_por_punto_y_coma():
    fila = fila_plantilla_a_fila_fuente(
        {"Nombre": "Juan Perez", "Teléfonos": "3001234567;3009999999", "WhatsApp": "juan.whatsapp"},
        fuente="mi_fuente",
    )
    assert fila.nombre == "Juan Perez"
    assert fila.telefonos == ("3001234567", "3009999999")
    assert fila.whatsapps == ("juan.whatsapp",)
    assert fila.fuente == "mi_fuente"


def test_fila_plantilla_a_fila_fuente_columnas_vacias_quedan_como_tuplas_vacias():
    fila = fila_plantilla_a_fila_fuente({"Nombre": "Ana", "Teléfonos": "", "WhatsApp": ""}, fuente="x")
    assert fila.telefonos == ()
    assert fila.whatsapps == ()


def test_fila_plantilla_a_fila_fuente_recorta_espacios_alrededor_del_separador():
    fila = fila_plantilla_a_fila_fuente(
        {"Nombre": "  Juan  ", "Teléfonos": " 3001234567 ; 3009999999 ", "WhatsApp": ""}, fuente="x"
    )
    assert fila.nombre == "Juan"
    assert fila.telefonos == ("3001234567", "3009999999")


class _ContactoFalso:
    def __init__(self, nombre, telefonos_cargados, whatsapps_cargados):
        self.nombre = nombre
        self.telefonos_cargados = telefonos_cargados
        self.whatsapps_cargados = whatsapps_cargados


def test_contactos_externos_a_filas_plantilla_junta_multiples_con_punto_y_coma():
    contactos = [_ContactoFalso("Juan Perez", ["+573001234567", "+573009999999"], ["juan.whatsapp"])]
    filas = contactos_externos_a_filas_plantilla(contactos)
    assert filas == [
        {"Nombre": "Juan Perez", "Teléfonos": "+573001234567;+573009999999", "WhatsApp": "juan.whatsapp"}
    ]


def test_contactos_externos_a_filas_plantilla_sin_whatsapp_queda_columna_vacia():
    contactos = [_ContactoFalso("Ana", ["+573001234567"], [])]
    filas = contactos_externos_a_filas_plantilla(contactos)
    assert filas[0]["WhatsApp"] == ""


def test_export_e_import_son_round_trip():
    """El CSV exportado se puede volver a subir tal cual como import --
    decisión explícita del cliente (grilling, pregunta 8)."""
    contacto = _ContactoFalso("Juan Perez", ["+573001234567", "+573009999999"], ["juan.whatsapp"])
    fila_plantilla = contactos_externos_a_filas_plantilla([contacto])[0]
    fila_fuente = fila_plantilla_a_fila_fuente(fila_plantilla, fuente="reimport")
    assert fila_fuente.nombre == contacto.nombre
    assert set(fila_fuente.telefonos) == set(contacto.telefonos_cargados)
    assert set(fila_fuente.whatsapps) == set(contacto.whatsapps_cargados)
