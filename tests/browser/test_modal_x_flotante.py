# -*- coding: utf-8 -*-
"""
Seam de navegador real — la ✕ de los modales flota al hacer scroll (issue 417, `.scratch/pendientes-cliente`).

Solo un navegador calcula `position: sticky`: se abre el modal Recibir (el más largo), se hace scroll dentro del panel y
se mide que la ✕ siga en el mismo lugar de la pantalla, visible, y que siga cerrando el modal.
"""

from _ayudantes import preparar_recibir


def _caja_x(pagina, paquete):
    return pagina.locator(f'#modal-receive-{paquete.id} [aria-label="Cerrar"]').bounding_box()


def test_la_x_se_queda_en_su_lugar_al_hacer_scroll_y_cierra_el_modal(app_viva, pagina):
    pagina.set_viewport_size({"width": 390, "height": 700})
    p = preparar_recibir(app_viva, pagina)
    panel = pagina.locator(f"#modal-receive-{p.id} > div.relative")
    antes = _caja_x(pagina, p)

    desplazado = panel.evaluate("el => { el.scrollTop = el.scrollHeight; return el.scrollTop; }")
    assert desplazado > 100  # el contenido de verdad se desplazó

    despues = _caja_x(pagina, p)
    assert abs(despues["y"] - antes["y"]) < 1
    caja_panel = panel.bounding_box()
    assert caja_panel["y"] <= despues["y"] <= caja_panel["y"] + 40  # arriba, dentro del panel

    pagina.locator(f'#modal-receive-{p.id} [aria-label="Cerrar"]').click()
    pagina.wait_for_function("id => document.getElementById(id).hidden", arg=f"modal-receive-{p.id}")
