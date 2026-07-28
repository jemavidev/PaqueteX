# -*- coding: utf-8 -*-
"""
Capa web — `/ayuda` (FAQ estática, Grupo 10 de la Ronda 2).

Pública, sin sesión, sin dependencia de base de datos.
"""


def test_get_ayuda_no_requiere_sesion(client):
    r = client.get("/ayuda", follow_redirects=False)
    assert r.status_code == 200
    assert "Preguntas frecuentes" in r.text


def test_ayuda_esta_enlazada_desde_el_footer_publico(client):
    r = client.get("/anunciar")
    assert 'href="/ayuda"' in r.text
