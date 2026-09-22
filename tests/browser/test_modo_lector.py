# -*- coding: utf-8 -*-
"""
Seam de navegador real — modo lector: interruptor por equipo y foco al abrir Recibir
(`.scratch/captura-guia-lector-camara`, ticket 10).

El F7 en modo "escribir en el campo enfocado" escribe donde esté el foco, y hoy al abrir Recibir el foco queda
en el botón de la fila. Sin volver a poner autofocus en todas partes (issue 284), cada equipo se marca UNA vez
con "Este equipo tiene lector" en el menú de cuenta: apagado por defecto y guardado en el propio equipo.
"""

from _ayudantes import (
    abrir_menu_de_cuenta,
    abrir_modal_recibir,
    alternar_modo_lector,
    anunciar_paquete,
    foco_en,
    iniciar_sesion_staff,
    volver_a_iniciar_sesion,
)

CLAVE = "paquetex.modoLector"


def _interruptor(pagina):
    return pagina.locator("#site-header [data-modo-lector]")


def _activo(pagina):
    return _interruptor(pagina).locator("[data-modo-lector-estado]").inner_text()


def _modo_lector_guardado(pagina):
    return pagina.evaluate(f"() => window.localStorage.getItem('{CLAVE}')")


def _preparar(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p = anunciar_paquete(app_viva)
    pagina.goto(f"{app_viva.url}/paquetes")
    return p


def test_por_defecto_esta_apagado_y_abrir_recibir_no_mueve_el_foco(app_viva, pagina):
    p = _preparar(app_viva, pagina)

    abrir_menu_de_cuenta(pagina)
    assert _activo(pagina) == "Desactivado"
    assert _modo_lector_guardado(pagina) in (None, "0")

    abrir_modal_recibir(pagina, app_viva, p)
    assert foco_en(pagina) != f"guia-{p.id}"  # issue 284: sin autofocus por defecto
    assert pagina.get_attribute(f"#guia-{p.id}", "inputmode") == "text"


def test_activarlo_desde_el_menu_lo_deja_activado_y_sobrevive_a_recargar_y_a_cerrar_sesion(
    app_viva, pagina
):
    p = _preparar(app_viva, pagina)

    alternar_modo_lector(pagina)
    assert _activo(pagina) == "Activado"
    assert _modo_lector_guardado(pagina) == "1"

    pagina.reload()
    abrir_menu_de_cuenta(pagina)
    assert _activo(pagina) == "Activado"

    # Cerrar sesión (cookies fuera) y volver a entrar: la preferencia es del EQUIPO, no de la sesión.
    pagina.context.clear_cookies()
    volver_a_iniciar_sesion(pagina, app_viva)
    pagina.goto(f"{app_viva.url}/paquetes")
    abrir_menu_de_cuenta(pagina)
    assert _activo(pagina) == "Activado"


def test_otro_equipo_o_navegador_arranca_apagado(app_viva, pagina, chromium):
    _preparar(app_viva, pagina)
    alternar_modo_lector(pagina)
    assert _activo(pagina) == "Activado"

    # Otro contexto de navegador = otro equipo: su almacenamiento es aparte.
    otro = chromium.new_context(viewport={"width": 1280, "height": 900})
    pagina_otra = otro.new_page()
    volver_a_iniciar_sesion(pagina_otra, app_viva)
    pagina_otra.goto(f"{app_viva.url}/paquetes")
    abrir_menu_de_cuenta(pagina_otra)
    assert _activo(pagina_otra) == "Desactivado"
    otro.close()


def test_con_el_modo_activo_al_abrir_recibir_el_foco_va_a_guia_sin_teclado_en_pantalla(
    app_viva, pagina
):
    p = _preparar(app_viva, pagina)
    alternar_modo_lector(pagina)

    abrir_modal_recibir(pagina, app_viva, p)

    assert foco_en(pagina) == f"guia-{p.id}"
    assert pagina.get_attribute(f"#guia-{p.id}", "inputmode") == "none"


def test_con_el_modo_activo_una_segunda_lectura_reemplaza_a_la_primera(app_viva, pagina):
    """Con `FOCUS` sin sobrescribir el F7 ANEXA a lo que ya hay: al enfocar, el contenido queda seleccionado."""
    p = _preparar(app_viva, pagina)
    alternar_modo_lector(pagina)
    abrir_modal_recibir(pagina, app_viva, p)

    pagina.keyboard.type("primera-1")
    assert pagina.input_value(f"#guia-{p.id}") == "PRIMERA-1"

    pagina.keyboard.press("Escape")  # cierra el modal
    pagina.locator(f'[data-open="modal-receive-{p.id}"]:visible').first.click()  # y lo reabre
    assert foco_en(pagina) == f"guia-{p.id}"
    pagina.keyboard.type("segunda-2")  # otra lectura, sin tocar el campo

    assert pagina.input_value(f"#guia-{p.id}") == "SEGUNDA-2"  # reemplazó, no se concatenó


def test_tocar_el_campo_devuelve_el_teclado_normal(app_viva, pagina):
    p = _preparar(app_viva, pagina)
    alternar_modo_lector(pagina)
    abrir_modal_recibir(pagina, app_viva, p)
    assert pagina.get_attribute(f"#guia-{p.id}", "inputmode") == "none"

    pagina.click(f"#guia-{p.id}")  # el Operador toca el campo para teclear a mano

    assert pagina.get_attribute(f"#guia-{p.id}", "inputmode") == "text"
    pagina.keyboard.type("a mano")
    assert "A MANO" in pagina.input_value(f"#guia-{p.id}")


def test_con_el_modo_activo_ya_no_hay_boton_de_camara_como_alternativa(app_viva, pagina):
    """Revisión en vivo (ticket 10 original decía lo contrario -- decisión revertida a propósito con evidencia
    real): en el F7 la cámara no entrega video utilizable y el gatillo físico con el campo enfocado ya
    resuelve la lectura, así que el botón deja de ofrecerse del todo (ver `test_con_el_modo_activo_el_boton_
    de_camara_de_recibir_se_oculta`); no queda ningún camino de cámara como alternativa en este modo."""
    p = _preparar(app_viva, pagina)
    alternar_modo_lector(pagina)
    abrir_modal_recibir(pagina, app_viva, p)

    assert pagina.locator(f"#modal-receive-{p.id} .scan-btn").count() == 1  # sigue en el DOM (solo CSS)
    assert pagina.locator(f"#modal-receive-{p.id} .scan-btn").is_hidden()


def test_apagar_el_interruptor_devuelve_el_comportamiento_normal(app_viva, pagina):
    p = _preparar(app_viva, pagina)
    alternar_modo_lector(pagina)
    alternar_modo_lector(pagina)  # apagado otra vez
    assert _modo_lector_guardado(pagina) == "0"

    abrir_modal_recibir(pagina, app_viva, p)

    assert foco_en(pagina) != f"guia-{p.id}"
    assert pagina.get_attribute(f"#guia-{p.id}", "inputmode") == "text"


def test_el_modo_lector_tambien_enfoca_el_recibir_de_consultar(app_viva, pagina):
    """El mismo componente en otra pantalla: Recibir de /consultar (Staff)."""
    p = _preparar(app_viva, pagina)
    alternar_modo_lector(pagina)

    pagina.goto(f"{app_viva.url}/consultar?q={p.access_code}")
    pagina.locator(f'[data-open="modal-receive-{p.id}"]:visible').first.click()

    assert foco_en(pagina) == f"guia-{p.id}"
    assert pagina.get_attribute(f"#guia-{p.id}", "inputmode") == "none"


def test_un_modal_que_llega_ya_abierto_tambien_recibe_el_foco(app_viva, pagina):
    """El servidor reabre Recibir tras un rechazo (guía de más de 50): no pasa por un clic, se revisa al cargar."""
    p = _preparar(app_viva, pagina)
    alternar_modo_lector(pagina)
    abrir_modal_recibir(pagina, app_viva, p)
    pagina.keyboard.insert_text("x" * 60)

    with pagina.expect_navigation():
        pagina.evaluate(
            "id => document.querySelector('#modal-receive-' + id + ' form').submit()", str(p.id)
        )

    assert pagina.locator(f"#modal-receive-{p.id}").is_visible()  # llegó abierto del servidor
    assert foco_en(pagina) == f"guia-{p.id}"
    assert pagina.get_attribute(f"#guia-{p.id}", "inputmode") == "none"


def test_un_enter_del_lector_deja_el_contenido_seleccionado_para_que_la_siguiente_lectura_lo_reemplace(
    app_viva, pagina
):
    """Revisión del ticket 10 (historia 27 del spec): dos disparos SEGUIDOS, sin reabrir el modal ni tocar el
    campo. Con el terminador Enter (la guardia lo absorbe) el contenido queda seleccionado: la segunda lectura
    reemplaza a la primera en vez de pegarse a ella."""
    p = _preparar(app_viva, pagina)
    alternar_modo_lector(pagina)
    abrir_modal_recibir(pagina, app_viva, p)

    pagina.keyboard.type("primera-1")
    pagina.keyboard.press("Enter")  # el terminador que manda el lector tras la primera lectura
    pagina.keyboard.type("segunda-2")  # segundo disparo del gatillo, sin tocar nada

    assert pagina.input_value(f"#guia-{p.id}") == "SEGUNDA-2"


def test_con_el_modo_apagado_un_enter_no_selecciona_nada(app_viva, pagina):
    """Sin modo lector el campo se comporta como siempre: teclear a mano tras un Enter no borra lo escrito."""
    p = _preparar(app_viva, pagina)
    abrir_modal_recibir(pagina, app_viva, p)
    pagina.click(f"#guia-{p.id}")

    pagina.keyboard.type("abc")
    pagina.keyboard.press("Enter")
    pagina.keyboard.type("def")

    assert pagina.input_value(f"#guia-{p.id}") == "ABCDEF"


def test_por_defecto_el_boton_de_camara_esta_visible(app_viva, pagina):
    """Celular normal (modo lector apagado, el default): el botón de la cámara sigue igual que hoy."""
    p = _preparar(app_viva, pagina)
    abrir_modal_recibir(pagina, app_viva, p)

    assert pagina.locator(f"#modal-receive-{p.id} .scan-btn").is_visible()


def test_con_el_modo_activo_el_boton_de_camara_de_recibir_se_oculta(app_viva, pagina):
    """Revisión en vivo (F7 real): la cámara del F7 no entrega video utilizable, y el gatillo físico con el
    campo enfocado ya captura la guía -- el botón de cámara sobra y confunde en ese equipo."""
    p = _preparar(app_viva, pagina)
    alternar_modo_lector(pagina)

    abrir_modal_recibir(pagina, app_viva, p)

    assert pagina.locator(f"#modal-receive-{p.id} .scan-btn").is_hidden()


def test_con_el_modo_activo_el_boton_se_oculta_en_una_fila_que_nunca_se_abrio(app_viva, pagina):
    """No es una decisión que se tome recién al abrir ESE modal puntual: aplica a TODAS las filas de la
    página, incluidas las que la búsqueda en vivo agregue después sin recargar."""
    iniciar_sesion_staff(pagina, app_viva)
    anunciar_paquete(app_viva, tel="3001110000", nombre="Marta")
    otro = anunciar_paquete(app_viva, tel="3002220000", nombre="Sofia")
    pagina.goto(f"{app_viva.url}/paquetes")
    alternar_modo_lector(pagina)

    assert pagina.locator(f"#modal-receive-{otro.id} .scan-btn").is_hidden()


def test_alternar_el_interruptor_oculta_o_muestra_el_boton_sin_recargar_la_pagina(app_viva, pagina):
    """Puro CSS (atributo en <html>): no hace falta recargar la página para que el cambio se refleje en la
    misma carga -- reabrir el modal alcanza, ninguna llamada a `pagina.reload()` de por medio."""
    p = _preparar(app_viva, pagina)
    boton = pagina.locator(f"#modal-receive-{p.id} .scan-btn")

    abrir_modal_recibir(pagina, app_viva, p)
    assert boton.is_visible()
    pagina.keyboard.press("Escape")  # cierra el modal: el menú de cuenta queda clickeable

    alternar_modo_lector(pagina)
    pagina.mouse.click(8, 400)  # cierra el menú de cuenta (clic fuera): deja de tapar la fila
    pagina.locator(f'[data-open="modal-receive-{p.id}"]:visible').first.click()
    assert boton.is_hidden()
    pagina.keyboard.press("Escape")

    alternar_modo_lector(pagina)
    pagina.mouse.click(8, 400)
    pagina.locator(f'[data-open="modal-receive-{p.id}"]:visible').first.click()
    assert boton.is_visible()
