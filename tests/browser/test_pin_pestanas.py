# -*- coding: utf-8 -*-
"""
Seam de navegador real — sincronización del Bloqueo entre pestañas (`.scratch/pin-operador-dispositivo`, ticket 06).

Dos pestañas del MISMO contexto de navegador = el mismo equipo (misma cookie de sesión, un solo Operador activo).
Reloj del contexto controlado: se adelanta en ambas pestañas a la vez.
"""

from app.domain import operador_dispositivo_service as ods
from app.domain.configuracion_conjunto_service import actualizar_seguridad_sesion
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

from _ayudantes import PASSWORD_STAFF, abrir_menu_de_cuenta, volver_a_iniciar_sesion


def _preparar(app_viva, pagina, *emails):
    admin = create_initial_admin(app_viva.db, "admin@club.com", "Admin", PASSWORD_STAFF)
    otro = create_staff(app_viva.db, admin, "otro@club.com", "Otro", PASSWORD_STAFF, RolUsuario.OPERADOR)
    ods.definir_pin(app_viva.db, admin, "1357")
    ods.definir_pin(app_viva.db, otro, "2468")
    actualizar_seguridad_sesion(app_viva.db, segundos_inactividad=60, dias_registro_dispositivo=15, actor=admin)
    app_viva.db.commit()
    for email in emails or ("admin@club.com",):
        volver_a_iniciar_sesion(pagina, app_viva, email)
    pagina.clock.install()
    pestana_b = pagina.context.new_page()
    pagina.goto(f"{app_viva.url}/paquetes")
    pestana_b.goto(f"{app_viva.url}/paquetes")
    return pestana_b


def _capa(pestana):
    return pestana.locator("#capa-bloqueo")


def test_bloquear_en_una_pestana_pone_la_capa_en_la_otra(app_viva, pagina):
    pestana_b = _preparar(app_viva, pagina)
    abrir_menu_de_cuenta(pagina)
    with pagina.expect_navigation():
        pagina.locator('#site-header .account-menu form[action="/bloquear"] button').click()
    assert "/bloqueo" in pagina.url
    _capa(pestana_b).wait_for(state="visible")


def test_desbloquear_como_otra_persona_recarga_la_otra_pestana(app_viva, pagina):
    pestana_b = _preparar(app_viva, pagina, "otro@club.com", "admin@club.com")  # activo: admin
    pestana_b.evaluate("() => { window.__sinRecargar = true; }")
    pagina.clock.fast_forward("01:05")
    _capa(pagina).wait_for(state="visible")
    _capa(pestana_b).wait_for(state="visible")

    pagina.bring_to_front()
    with pagina.expect_navigation():
        for digito in "2468":
            pagina.locator(f'#capa-bloqueo-form [data-digito="{digito}"]').click()
    pestana_b.wait_for_function("() => !window.__sinRecargar")  # la otra pestaña se recargó
    _capa(pestana_b).wait_for(state="hidden")


def test_desbloquear_como_la_misma_persona_quita_la_capa_en_la_otra_sin_recargar(app_viva, pagina):
    pestana_b = _preparar(app_viva, pagina)
    pestana_b.evaluate("() => { window.__sinRecargar = true; }")
    pagina.clock.fast_forward("01:05")
    _capa(pestana_b).wait_for(state="visible")

    pagina.bring_to_front()
    for digito in "1357":
        pagina.locator(f'#capa-bloqueo-form [data-digito="{digito}"]').click()
    _capa(pestana_b).wait_for(state="hidden")
    assert pestana_b.evaluate("() => window.__sinRecargar") is True


def test_la_actividad_en_una_pestana_mantiene_desbloqueada_la_otra(app_viva, pagina):
    pestana_b = _preparar(app_viva, pagina)
    pagina.clock.fast_forward("00:50")
    pagina.bring_to_front()
    pagina.keyboard.press("Shift")
    pagina.clock.fast_forward("00:50")
    assert _capa(pestana_b).is_hidden()
    pagina.clock.fast_forward("00:15")
    _capa(pestana_b).wait_for(state="visible")
