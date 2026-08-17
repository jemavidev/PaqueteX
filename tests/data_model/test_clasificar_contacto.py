# -*- coding: utf-8 -*-
"""
Clasificador de contacto compartido (`.scratch/ocupante-principal-
escenarios`, ticket 01) — Teléfono/WhatsApp, sin el caso Torre+Apto (propio
de `/announce`, no generalizado acá).

Comportamiento observable: dado un valor tecleado en un solo campo, decide
si es Teléfono, WhatsApp, o ninguno de los dos todavía.
"""

import pytest

from app.domain.contacto import clasificar_contacto


@pytest.mark.parametrize("raw", ["3001234567", "3009999999"])
def test_diez_digitos_empezando_en_3_es_telefono(raw):
    assert clasificar_contacto(raw) == "telefono"


@pytest.mark.parametrize("raw", ["300123456", "30012345678", "300123456a"])
def test_no_son_diez_digitos_no_es_telefono(raw):
    assert clasificar_contacto(raw) == "ninguno"


@pytest.mark.parametrize(
    "raw",
    [
        "+573001234567",  # Colombia, con indicativo
        "573001234567",  # Colombia, indicativo sin '+' (normalizar_telefono también lo acepta)
        "+13002596319",  # EE.UU.
        "+584121234567",  # Venezuela
        "+34612345678",  # España
    ],
)
def test_con_mas_o_indicativo_colombiano_es_telefono_sin_importar_el_pais(raw):
    assert clasificar_contacto(raw) == "telefono"


@pytest.mark.parametrize(
    "raw",
    [
        "+57300",  # a medio teclear
        "+57",  # solo el indicativo
        "+123456789",  # 9 dígitos tras el '+', bajo el mínimo E.164 (10)
        "+1234567890123456",  # 16 dígitos tras el '+', sobre el máximo E.164 (15)
    ],
)
def test_con_mas_incompleto_o_fuera_de_rango_no_es_telefono(raw):
    assert clasificar_contacto(raw) == "ninguno"


@pytest.mark.parametrize("raw", ["ana.whats", "abc", "juan_perez"])
def test_empieza_en_letra_al_menos_3_caracteres_es_whatsapp(raw):
    assert clasificar_contacto(raw) == "whatsapp"


@pytest.mark.parametrize("raw", ["ab", "a"])
def test_letra_con_menos_de_3_caracteres_no_es_whatsapp(raw):
    assert clasificar_contacto(raw) == "ninguno"


@pytest.mark.parametrize("raw", ["@ana.whats", "@abc", "@juan_perez"])
def test_con_arroba_inicial_tambien_es_whatsapp(raw):
    # Conversación 2026-08-17 (pedido explícito): "@usuario" y "usuario"
    # deben llevar al mismo resultado, mismo principio que "+57" para
    # teléfono -- `persona_service.py` ya le hacía `.lstrip("@")` a la hora
    # de buscar/crear la Persona, pero la clasificación (acá) nunca llegaba
    # a esas funciones porque "@" no es una letra.
    assert clasificar_contacto(raw) == "whatsapp"


@pytest.mark.parametrize("raw", ["@ab", "@a", "@"])
def test_con_arroba_pero_menos_de_3_caracteres_de_usuario_no_es_whatsapp(raw):
    # El mínimo de 3 caracteres se mide sobre el USUARIO (sin contar el
    # "@") -- "@ab" tiene un usuario de 2 caracteres, igual de inválido
    # que "ab" sin arroba.
    assert clasificar_contacto(raw) == "ninguno"


@pytest.mark.parametrize("raw", ["", None, "   ", "2001234567", "0110105"])
def test_vacio_o_sin_forma_reconocible_es_ninguno(raw):
    assert clasificar_contacto(raw) == "ninguno"
