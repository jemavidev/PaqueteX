# -*- coding: utf-8 -*-
"""
Regla de normalización de la terna del Apartamento (unidad).

Comportamiento observable: cualquier variante de casing/espacios de la misma
terna (conjunto, torre, apartamento) resuelve a la MISMA forma canónica, para
que el dedup único no genere duplicados. Cada componente se normaliza por
separado: strip + colapso de espacios internos + MAYÚSCULAS.
"""

import pytest

from app.domain.apartamento import normalizar_terna

CANONICA = ("LAS FLORES", "TORRE A", "101")


@pytest.mark.parametrize(
    "conjunto,torre,apartamento",
    [
        ("LAS FLORES", "TORRE A", "101"),
        ("Las Flores", "Torre A", "101"),
        ("las flores", "torre a", "101"),
        ("  Las Flores  ", " Torre A ", " 101 "),
        ("Las   Flores", "Torre   A", "101"),
        ("las flores ", "  torre a", "101 "),
    ],
)
def test_variantes_de_la_misma_terna_son_canonicas(conjunto, torre, apartamento):
    assert normalizar_terna(conjunto, torre, apartamento) == CANONICA


def test_ternas_distintas_siguen_distintas():
    assert normalizar_terna("Las Flores", "Torre A", "101") != normalizar_terna(
        "Las Flores", "Torre A", "102"
    )


@pytest.mark.parametrize(
    "conjunto,torre,apartamento",
    [
        ("", "A", "101"),
        ("   ", "A", "101"),
        ("Flores", "", "101"),
        ("Flores", "A", "   "),
    ],
)
def test_componente_vacio_lanza_valueerror(conjunto, torre, apartamento):
    with pytest.raises(ValueError):
        normalizar_terna(conjunto, torre, apartamento)


@pytest.mark.parametrize(
    "conjunto,torre,apartamento",
    [
        (None, "A", "101"),
        ("Flores", None, "101"),
        ("Flores", "A", None),
    ],
)
def test_componente_none_lanza_valueerror(conjunto, torre, apartamento):
    with pytest.raises(ValueError):
        normalizar_terna(conjunto, torre, apartamento)
