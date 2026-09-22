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


def test_el_boton_de_la_camara_sigue_disponible_con_el_modo_activo(app_viva, pagina, camara):
    p = _preparar(app_viva, pagina)
    alternar_modo_lector(pagina)
    abrir_modal_recibir(pagina, app_viva, p)

    pagina.click(f"#modal-receive-{p.id} .scan-btn")

    pagina.locator(f"#video-{p.id}").wait_for(state="visible")
    assert camara.estado()["llamadas"] == 1


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
