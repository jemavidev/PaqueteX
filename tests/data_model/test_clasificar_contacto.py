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


@pytest.mark.parametrize("raw", ["ana.whats", "abc", "juan_perez"])
def test_empieza_en_letra_al_menos_3_caracteres_es_whatsapp(raw):
    assert clasificar_contacto(raw) == "whatsapp"


@pytest.mark.parametrize("raw", ["ab", "a"])
def test_letra_con_menos_de_3_caracteres_no_es_whatsapp(raw):
    assert clasificar_contacto(raw) == "ninguno"


@pytest.mark.parametrize("raw", ["", None, "   ", "2001234567", "0110105"])
def test_vacio_o_sin_forma_reconocible_es_ninguno(raw):
    assert clasificar_contacto(raw) == "ninguno"
