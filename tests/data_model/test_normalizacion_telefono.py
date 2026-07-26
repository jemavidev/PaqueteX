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


def test_no_corrompe_nacional_que_empieza_por_57():
    # 10 dígitos que empiezan por 57 NO se tratan como indicativo país.
    assert normalizar_telefono("5700000000") == "+575700000000"


def test_telefono_vacio_lanza_valueerror():
    with pytest.raises(ValueError):
        normalizar_telefono("   ")


def test_telefono_none_lanza_valueerror():
    with pytest.raises(ValueError):
        normalizar_telefono(None)
