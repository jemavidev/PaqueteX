# -*- coding: utf-8 -*-
"""
Seam de navegador real — modo lector en Entregar: foco en "Confirmar guía"
(`.scratch/captura-guia-lector-camara`, ticket 11).

Con el modo lector encendido (ticket 10), al abrir Entregar de un paquete CON guía el foco va directo al campo
"Confirmar guía" (sin teclado en pantalla y con su contenido seleccionado), para verificar el paquete leyendo su
etiqueta sin tocar nada. El ✅/⚠️ que compara contra la guía registrada sigue igual y sigue sin bloquear.
"""

from _ayudantes import (
    abrir_modal_entregar,
    alternar_modo_lector,
    anunciar_paquete,
    foco_en,
    iniciar_sesion_staff,
    recibir_paquete_en_bd,
)


def _preparar(app_viva, pagina, guia="GUIA-9"):
    iniciar_sesion_staff(pagina, app_viva)
    p = anunciar_paquete(app_viva)
    recibir_paquete_en_bd(app_viva, p, guia)
    pagina.goto(f"{app_viva.url}/paquetes?estado=RECIBIDO")
    return p


def _campo(p):
    return f"#guia-confirmar-{p.id}"


def test_con_el_modo_activo_abrir_entregar_de_un_paquete_con_guia_enfoca_confirmar_guia(
    app_viva, pagina
):
    p = _preparar(app_viva, pagina)
    alternar_modo_lector(pagina)

    abrir_modal_entregar(pagina, app_viva, p)

    assert foco_en(pagina) == f"guia-confirmar-{p.id}"
    assert pagina.get_attribute(_campo(p), "inputmode") == "none"


def test_tocar_confirmar_guia_devuelve_el_teclado_normal(app_viva, pagina):
    p = _preparar(app_viva, pagina)
    alternar_modo_lector(pagina)
    abrir_modal_entregar(pagina, app_viva, p)
    assert pagina.get_attribute(_campo(p), "inputmode") == "none"

    pagina.click(_campo(p))

    assert pagina.get_attribute(_campo(p), "inputmode") == "text"


def test_un_paquete_sin_guia_no_muestra_el_campo_ni_enfoca_ningun_otro(app_viva, pagina):
    p = _preparar(app_viva, pagina, guia=None)
    alternar_modo_lector(pagina)

    abrir_modal_entregar(pagina, app_viva, p)

    assert pagina.locator(f"#modal-deliver-{p.id} [data-guia-esperada]").count() == 0
    campo_enfocado_en_el_modal = pagina.evaluate(
        "() => { const a = document.activeElement; return !!(a && a.matches('input') && a.closest('[role=dialog]')); }"
    )
    assert not campo_enfocado_en_el_modal


def test_con_el_modo_apagado_abrir_entregar_no_mueve_el_foco(app_viva, pagina):
    p = _preparar(app_viva, pagina)  # el modo lector queda apagado (por defecto)

    abrir_modal_entregar(pagina, app_viva, p)

    assert foco_en(pagina) != f"guia-confirmar-{p.id}"
    assert pagina.get_attribute(_campo(p), "inputmode") == "text"


def test_la_comparacion_de_la_guia_sigue_funcionando_con_el_modo_activo(app_viva, pagina):
    p = _preparar(app_viva, pagina, guia="GUIA-9")
    alternar_modo_lector(pagina)
    abrir_modal_entregar(pagina, app_viva, p)
    mensaje = pagina.locator(f"#modal-deliver-{p.id} .guia-check-msg")

    pagina.keyboard.type("guia-9")  # una lectura del lector: entra en el campo enfocado
    mensaje.wait_for(state="visible")
    assert "Coincide" in mensaje.inner_text()

    pagina.keyboard.type("x")  # seleccionado/no: ahora es otra guía distinta
    assert "distinta" in mensaje.inner_text()
    # Nunca bloquea: el botón de entregar del formulario sigue ahí y habilitado.
    assert pagina.locator(f"#modal-deliver-{p.id} form button[type=submit]").first.is_enabled()


def test_el_modo_lector_tambien_enfoca_confirmar_guia_en_el_entregar_de_consultar(app_viva, pagina):
    p = _preparar(app_viva, pagina)
    alternar_modo_lector(pagina)

    pagina.goto(f"{app_viva.url}/consultar?q={p.access_code}")
    pagina.locator('[data-open="modal-entregar-consultar"]:visible').first.click()

    assert foco_en(pagina) == "guia-confirmar-consultar"
    assert pagina.get_attribute("#guia-confirmar-consultar", "inputmode") == "none"
