# -*- coding: utf-8 -*-
"""
Regla de normalización del Teléfono (unidad).

Comportamiento observable: cualquier formato del mismo número colombiano
resuelve a la misma forma canónica `+57` + 10 dígitos.
"""

import pytest

from app.domain.telefono import normalizar_telefono

CANONICAL = "+573001234567"


@pytest.mark.parametrize(
    "raw",
    [
        "3001234567",
        "300 123 4567",
        "300-123-4567",
        "(300) 123-4567",
        "300.123.4567",
        "+57 300 123 4567",
        "+573001234567",
        "57 300 123 4567",
        "573001234567",
        "  3001234567  ",
    ],
)
def test_todos_los_formatos_del_mismo_numero_son_canonicos(raw):
    assert normalizar_telefono(raw) == CANONICAL


def test_numeros_distintos_siguen_distintos():
    assert normalizar_telefono("3001234567") != normalizar_telefono("3009999999")


def test_nacional_sin_mas_que_no_empieza_por_3_es_invalido():
    # Sin "+", solo se acepta celular colombiano (empieza por 3) — no hay
    # forma de saber el país de un número nacional que no calce ahí.
    with pytest.raises(ValueError):
        normalizar_telefono("5700000000")


@pytest.mark.parametrize(
    "raw,esperado",
    [
        ("+13002596319", "+13002596319"),
        ("+1 300 259 6319", "+13002596319"),
        ("+913002596319", "+913002596319"),
    ],
)
def test_internacional_con_mas_se_acepta_tal_cual(raw, esperado):
    assert normalizar_telefono(raw) == esperado


def test_internacional_con_mas_pero_muy_corto_es_invalido():
    with pytest.raises(ValueError):
        normalizar_telefono("+123")


def test_internacional_con_mas_pero_demasiado_largo_es_invalido():
    with pytest.raises(ValueError):
        normalizar_telefono("+1234567890123456")


def test_sin_mas_que_no_es_celular_colombiano_es_invalido():
    with pytest.raises(ValueError):
        normalizar_telefono("13002596319")


def test_telefono_vacio_lanza_valueerror():
    with pytest.raises(ValueError):
        normalizar_telefono("   ")


def test_telefono_none_lanza_valueerror():
    with pytest.raises(ValueError):
        normalizar_telefono(None)
