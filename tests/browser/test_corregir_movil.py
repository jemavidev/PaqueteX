# -*- coding: utf-8 -*-
"""
Seam de navegador real — en móvil, sin apartamento, el modal "Ver" no muestra el ícono apagado de Corregir destinatario
(issue 421, `.scratch/pendientes-cliente`); en escritorio sí (gris, con el aviso).
"""

from _ayudantes import anunciar_paquete, iniciar_sesion_staff


def _icono_apagado_en_ver(app_viva, pagina, ancho):
    pagina.set_viewport_size({"width": ancho, "height": 800})
    iniciar_sesion_staff(pagina, app_viva)
    p = anunciar_paquete(app_viva)  # sin apartamento
    pagina.goto(f"{app_viva.url}/paquetes")
    pagina.locator(f'[data-open="modal-ver-{p.id}"]:visible').first.click()
    return pagina.locator(f'#modal-ver-{p.id} [title="Asigna un apartamento primero"]')


def test_en_movil_no_se_ve_el_icono_apagado(app_viva, pagina):
    assert not _icono_apagado_en_ver(app_viva, pagina, 390).is_visible()


def test_en_escritorio_se_ve_gris(app_viva, pagina):
    assert _icono_apagado_en_ver(app_viva, pagina, 1280).is_visible()
