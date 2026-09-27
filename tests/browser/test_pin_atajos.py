# -*- coding: utf-8 -*-
"""
Seam de navegador real — atajos del PIN de operador (`.scratch/pendientes-cliente`, issues 422 y 423).

- 422 (corregido): con el equipo bloqueado por PIN, un candado en el header (a la izquierda del ícono de ingresar) lleva
  a la pantalla de bloqueo desde cualquier vista, en escritorio y en móvil.
- 423: en escritorio el PIN se teclea sin depender del foco, en la pantalla de bloqueo y en la capa.
"""

from app.domain import operador_dispositivo_service as ods
from app.domain.configuracion_conjunto_service import actualizar_seguridad_sesion
from app.domain.staff_service import create_initial_admin

from _ayudantes import PASSWORD_STAFF, volver_a_iniciar_sesion


def _preparar(app_viva, pagina):
    admin = create_initial_admin(app_viva.db, "admin@club.com", "Admin", PASSWORD_STAFF)
    ods.definir_pin(app_viva.db, admin, "1357")
    actualizar_seguridad_sesion(app_viva.db, segundos_inactividad=60, dias_registro_dispositivo=15, actor=admin)
    app_viva.db.commit()
    volver_a_iniciar_sesion(pagina, app_viva, "admin@club.com")


def _candado(pagina):
    return pagina.locator("#site-header [data-desbloquear-equipo]")


def _bloquear(pagina, app_viva):
    """Deja el equipo bloqueado (lo mismo que "Bloquear" del menú de cuenta)."""
    pagina.context.request.post(f"{app_viva.url}/bloquear", max_redirects=0)


def test_con_el_equipo_bloqueado_el_candado_lleva_a_desbloquear(app_viva, pagina):
    _preparar(app_viva, pagina)
    pagina.goto(f"{app_viva.url}/paquetes")
    assert _candado(pagina).count() == 0  # con Operador activo no hay nada que desbloquear
    _bloquear(pagina, app_viva)
    pagina.goto(f"{app_viva.url}/anunciar")
    with pagina.expect_navigation():
        _candado(pagina).click()
    assert "/bloqueo" in pagina.url
    with pagina.expect_navigation():
        pagina.keyboard.type("1357")
    assert pagina.url.endswith("/paquetes")


def test_el_candado_tambien_se_ve_en_movil(app_viva, pagina):
    _preparar(app_viva, pagina)
    _bloquear(pagina, app_viva)
    pagina.set_viewport_size({"width": 390, "height": 844})
    pagina.goto(f"{app_viva.url}/anunciar")
    assert _candado(pagina).is_visible()


def test_sin_equipo_registrado_no_hay_candado(app_viva, pagina):
    pagina.goto(f"{app_viva.url}/anunciar")
    assert _candado(pagina).count() == 0


def test_en_la_pantalla_de_bloqueo_se_teclea_el_pin_sin_el_mouse(app_viva, pagina):
    _preparar(app_viva, pagina)
    _bloquear(pagina, app_viva)
    pagina.goto(f"{app_viva.url}/bloqueo")
    pagina.mouse.click(5, 5)  # el foco no está en el campo del PIN
    pagina.keyboard.type("13")
    pagina.keyboard.press("Backspace")
    with pagina.expect_navigation():
        pagina.keyboard.type("357")
    assert pagina.url.endswith("/paquetes")


def test_en_la_capa_se_teclea_el_pin_sin_el_mouse(app_viva, pagina):
    _preparar(app_viva, pagina)
    pagina.clock.install()
    pagina.goto(f"{app_viva.url}/paquetes")
    pagina.clock.fast_forward("01:05")
    capa = pagina.locator("#capa-bloqueo")
    capa.wait_for(state="visible")
    pagina.mouse.click(5, 5)
    pagina.keyboard.type("1357")
    capa.wait_for(state="hidden")
