# -*- coding: utf-8 -*-
"""
Normalización canónica del usuario de WhatsApp (unidad).

Promovida desde `persona_service.py` (donde vivía privada, usada solo por
`Persona`) a un módulo compartido -- mismo patrón que `telefono.py` ya
establece para el teléfono (`.scratch/contactos-externos-import-export`,
que la reutiliza para el motor de fusión de contactos externos).

Comportamiento observable: cualquier formato del mismo usuario (con/sin `@`
inicial, cualquier combinación de mayúsculas/minúsculas) normaliza a la
misma forma canónica -- reglas publicadas por Meta (issue 67/162 de
`.scratch/pendientes-cliente`): 3-35 caracteres, letras latinas, números,
puntos o guion bajo.
"""

import pytest

from app.domain.whatsapp import normalizar_whatsapp_usuario, validar_whatsapp_usuario

CANONICAL = "jesus.villalobos"


@pytest.mark.parametrize(
    "raw",
    [
        "jesus.villalobos",
        "Jesus.Villalobos",
        "@jesus.villalobos",
        "  jesus.villalobos  ",
        "JESUS.VILLALOBOS",
        "  @Jesus.Villalobos  ",
    ],
)
def test_formatos_equivalentes_normalizan_al_mismo_valor(raw):
    assert normalizar_whatsapp_usuario(raw) == CANONICAL


def test_normalizar_no_lanza_con_valor_vacio():
    assert normalizar_whatsapp_usuario("") == ""
    assert normalizar_whatsapp_usuario(None) == ""
    assert normalizar_whatsapp_usuario("   ") == ""


@pytest.mark.parametrize(
    "valido",
    ["abc", "jesus.villalobos", "a" * 35, "a_b.c123", "300"],
)
def test_validar_acepta_formatos_correctos(valido):
    validar_whatsapp_usuario(valido)  # no lanza


@pytest.mark.parametrize(
    "invalido",
    [
        "ab",  # menos de 3 caracteres
        "a" * 36,  # más de 35 caracteres
        "usuario con espacio",
        "usuario@dominio",  # @ solo se recorta al INICIO, no en medio
        "",
    ],
)
def test_validar_rechaza_formatos_invalidos(invalido):
    with pytest.raises(ValueError):
        validar_whatsapp_usuario(invalido)
