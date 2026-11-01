# -*- coding: utf-8 -*-
"""
Seam de navegador real — Cambiar posición tocando el chip 📍 (pedido del cliente 2026-10-31).

Lo que solo un navegador decide: que el chip abre el modal, que el modal queda ENCIMA de Entregar y al cerrarlo
Entregar sigue abierto, que el piso "00" se elige y se guarda, y que todo sigue funcionando tras el re-render de la
búsqueda en vivo de /paquetes.
"""

from app.domain.paquete_lifecycle import receive
from app.domain.usuario import Usuario

from _ayudantes import EMAIL_STAFF, anunciar_paquete, iniciar_sesion_staff, paquete_en_bd


def _recibido(app_viva, posicion="41", tel="3001234567", nombre="Ana"):
    staff = app_viva.db.query(Usuario).filter(Usuario.email == EMAIL_STAFF).one()
    p = anunciar_paquete(app_viva, tel=tel, nombre=nombre)
    receive(app_viva.db, p, staff, posicion=posicion)
    app_viva.db.commit()
    return p


def _modal(p):
    return f"modal-posicion-{p.id}"


def _esperar_abierto(pagina, modal_id):
    pagina.wait_for_function("id => !document.getElementById(id).hidden", arg=modal_id)


def _elegir_piso_y_guardar(pagina, p):
    pagina.locator(f'#{_modal(p)} input[name="posicion"][value="00"] + span').click()
    with pagina.expect_navigation():
        pagina.click(f"#{_modal(p)} button[type=submit]")


def test_tocar_el_chip_en_paquetes_abre_el_modal_y_guarda_el_piso(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p = _recibido(app_viva)
    pagina.goto(f"{app_viva.url}/paquetes")

    pagina.locator(f'[data-open="{_modal(p)}"]:visible').first.click()
    _esperar_abierto(pagina, _modal(p))
    assert pagina.locator(f'#{_modal(p)} input[name="posicion"][value="41"]').is_checked()

    _elegir_piso_y_guardar(pagina, p)

    assert paquete_en_bd(app_viva, p.id).posicion == "00"
    assert pagina.locator(f'[data-open="{_modal(p)}"]:visible', has_text="📍 00").count() >= 1
    assert pagina.get_by_text("Posición cambiada a 📍 00.").is_visible()


def test_desde_el_modal_entregar_queda_encima_y_al_cerrarlo_vuelve_a_entregar(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p = _recibido(app_viva)
    entregar = f"modal-deliver-{p.id}"
    pagina.goto(f"{app_viva.url}/paquetes?estado=RECIBIDO")
    pagina.locator(f'[data-open="{entregar}"]:visible').first.click()
    _esperar_abierto(pagina, entregar)

    pagina.locator(f'#{entregar} [data-open="{_modal(p)}"]').click()
    _esperar_abierto(pagina, _modal(p))
    # Encima: el centro del botón Guardar es del modal Cambiar posición, no de Entregar.
    guardar = pagina.locator(f"#{_modal(p)} button[type=submit]")
    caja = guardar.bounding_box()
    encima = pagina.evaluate(
        "([x, y, id]) => !!document.elementFromPoint(x, y).closest('#' + id)",
        [caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2, _modal(p)],
    )
    assert encima

    pagina.keyboard.press("Escape")
    pagina.wait_for_function("id => document.getElementById(id).hidden", arg=_modal(p))
    assert pagina.locator(f"#{entregar}").is_visible()

    pagina.locator(f'#{entregar} [data-open="{_modal(p)}"]').click()
    _esperar_abierto(pagina, _modal(p))
    _elegir_piso_y_guardar(pagina, p)
    assert paquete_en_bd(app_viva, p.id).posicion == "00"


def test_sigue_funcionando_tras_la_busqueda_en_vivo(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p = _recibido(app_viva)
    _recibido(app_viva, posicion="12", tel="3009998888", nombre="Beto")
    pagina.goto(f"{app_viva.url}/paquetes")

    with pagina.expect_response(lambda r: "/paquetes" in r.url and r.request.headers.get("x-requested-with")):
        pagina.locator('input[name="q"]:visible').first.fill("Ana")
    pagina.wait_for_function("() => !document.body.innerText.includes('Beto')")

    pagina.locator(f'[data-open="{_modal(p)}"]:visible').first.click()
    _esperar_abierto(pagina, _modal(p))
    _elegir_piso_y_guardar(pagina, p)

    assert paquete_en_bd(app_viva, p.id).posicion == "00"
    assert "q=Ana" in pagina.url


def test_desde_consultar_cambia_la_posicion_y_vuelve_a_la_misma_consulta(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p = _recibido(app_viva)
    pagina.goto(f"{app_viva.url}/consultar?q={p.access_code}")

    pagina.locator(f'[data-open="{_modal(p)}"]:visible').first.click()
    _esperar_abierto(pagina, _modal(p))
    _elegir_piso_y_guardar(pagina, p)

    assert paquete_en_bd(app_viva, p.id).posicion == "00"
    assert f"q={p.access_code}" in pagina.url
    assert pagina.locator(f'[data-open="{_modal(p)}"]:visible', has_text="📍 00").count() == 1
