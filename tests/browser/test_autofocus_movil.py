# -*- coding: utf-8 -*-
"""
Seam de navegador real — en móvil nada recibe el foco solo (issue 418, `.scratch/pendientes-cliente`).

Los campos marcados con `data-enfocar` se enfocan al cargar SOLO desde 640 px (escritorio); en un celular el teclado en
pantalla no salta. Ninguna página lleva el `autofocus` nativo del navegador.
"""


def _foco_al_cargar(app_viva, pagina, ancho):
    pagina.set_viewport_size({"width": ancho, "height": 800})
    pagina.goto(f"{app_viva.url}/ingresar")
    return pagina.evaluate("() => document.activeElement.getAttribute('name')")


def test_en_movil_ningun_campo_toma_el_foco_al_cargar(app_viva, pagina):
    assert _foco_al_cargar(app_viva, pagina, 390) is None
    assert pagina.locator("[autofocus]").count() == 0


def test_en_escritorio_el_campo_marcado_toma_el_foco(app_viva, pagina):
    assert _foco_al_cargar(app_viva, pagina, 1280) == "email"
