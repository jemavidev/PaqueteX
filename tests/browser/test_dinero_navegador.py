# -*- coding: utf-8 -*-
"""
Seam de navegador real — vista "Dinero" (pedido del cliente 2026-11-01): tocar un ícono de filtro (Entradas, Salidas…) filtra en vivo y
deja el filtro en la URL; tocar el nombre de un cliente filtra por él y muestra su saldo actual.
"""

from app.domain.persona_service import get_or_create_persona
from app.domain.saldo_contra_entrega_service import registrar_movimiento_saldo
from app.domain.usuario import Usuario

from _ayudantes import EMAIL_STAFF, iniciar_sesion_staff


def _preparar(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    staff = app_viva.db.query(Usuario).filter(Usuario.email == EMAIL_STAFF).one()
    ana = get_or_create_persona(app_viva.db, "3001234567", "Ana")
    beto = get_or_create_persona(app_viva.db, "3007654321", "Beto")
    registrar_movimiento_saldo(app_viva.db, ana.id, 5000, staff)
    registrar_movimiento_saldo(app_viva.db, beto.id, 4000, staff)
    registrar_movimiento_saldo(app_viva.db, beto.id, -7000, staff)
    app_viva.db.commit()
    return ana, beto


def test_filtrar_por_tipo_y_tocar_un_cliente(app_viva, pagina):
    ana, beto = _preparar(app_viva, pagina)
    pagina.set_viewport_size({"width": 1280, "height": 900})
    pagina.goto(f"{app_viva.url}/dinero")
    filas = pagina.locator("[data-fila-dinero]")
    assert filas.count() == 3

    with pagina.expect_response(lambda r: "/dinero?grupo=entradas" in r.url):
        pagina.click('[data-filtro-pildora="grupo"][data-valor="entradas"]')
    pagina.wait_for_function("document.querySelectorAll('[data-fila-dinero]').length === 2")
    assert "grupo=entradas" in pagina.url
    assert pagina.locator('[data-fila-dinero] [data-tipo-fila="ajuste"]').count() == 0
    assert pagina.get_attribute('[data-filtro-pildora="grupo"][data-valor="entradas"]', "aria-pressed") == "true"
    # Operador y fechas: escondidos hasta tocar el ícono del calendario.
    assert not pagina.locator("#filtro-desde").is_visible()
    pagina.click("[data-mas-filtros]")
    assert pagina.locator("#filtro-desde").is_visible()

    with pagina.expect_navigation():
        pagina.locator(f'[data-fila-dinero] [data-filtrar-cliente="{beto.id}"]').click()

    assert f"cliente={beto.id}" in pagina.url and "grupo=entradas" in pagina.url
    assert filas.count() == 1
    assert pagina.locator("[data-saldo-cliente]").inner_text() == "debe $3,000"

    # "Quitar filtros" limpia tipo y cliente en vivo.
    pagina.click("[data-estado-reset]")
    pagina.wait_for_function("document.querySelectorAll('[data-fila-dinero]').length === 3")
    assert "cliente=" not in pagina.url and "grupo=" not in pagina.url


def test_en_el_celular_se_ven_tarjetas(app_viva, pagina):
    _preparar(app_viva, pagina)
    pagina.set_viewport_size({"width": 375, "height": 800})
    pagina.goto(f"{app_viva.url}/dinero")

    assert pagina.locator("[data-tarjeta-dinero]").first.is_visible()
    assert not pagina.locator("[data-fila-dinero]").first.is_visible()
    assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
