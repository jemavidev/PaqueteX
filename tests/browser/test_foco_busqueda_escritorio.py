# -*- coding: utf-8 -*-
"""
Seam de navegador real — foco en la búsqueda al cargar /paquetes, /residentes y /announce (pedido del cliente
2026-10-31), SOLO en escritorio: en móvil nada recibe el foco solo (issue 418).
"""
import pytest

from _ayudantes import iniciar_sesion_staff

VISTAS = ["/paquetes", "/residentes", "/announce"]


def _foco_al_cargar(app_viva, pagina, ruta, ancho):
    pagina.set_viewport_size({"width": ancho, "height": 800})
    pagina.goto(f"{app_viva.url}{ruta}")
    return pagina.evaluate("() => document.activeElement && document.activeElement.getAttribute('name')")


@pytest.mark.parametrize("ruta", VISTAS)
def test_en_escritorio_la_busqueda_toma_el_foco(app_viva, pagina, ruta):
    iniciar_sesion_staff(pagina, app_viva)
    assert _foco_al_cargar(app_viva, pagina, ruta, 1280) == "q"


@pytest.mark.parametrize("ruta", VISTAS)
def test_en_movil_la_busqueda_no_toma_el_foco(app_viva, pagina, ruta):
    iniciar_sesion_staff(pagina, app_viva)
    assert _foco_al_cargar(app_viva, pagina, ruta, 390) != "q"
