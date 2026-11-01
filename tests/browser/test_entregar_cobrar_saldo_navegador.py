# -*- coding: utf-8 -*-
"""
Seam de navegador real — Entregar con deuda obliga a cobrarla (pedido del cliente 2026-10-31, migración 0074).

Si el destinatario debe: el modal Entregar trae la caja roja arriba, el resto en gris e `inert` y "Entregar"
deshabilitado. "Cobrar $D" llena el abono con toda la deuda y lo habilita; un abono parcial muestra "Queda debiendo
$R"; y entregar sin cobrar todo pasa por el diálogo con motivo y 3 s de espera. Sin deuda, el modal es el de siempre.
"""

from app.domain.paquete import EstadoPaquete
from app.domain.persona_service import get_or_create_persona
from app.domain.saldo_contra_entrega_service import registrar_movimiento_saldo, saldo_de_persona
from app.domain.usuario import Usuario

from _ayudantes import (
    EMAIL_STAFF,
    abrir_modal_entregar,
    anunciar_paquete,
    iniciar_sesion_staff,
    paquete_en_bd,
    recibir_paquete_en_bd,
)

_DEUDA = 5000


def _preparar(app_viva, pagina, deuda=_DEUDA):
    iniciar_sesion_staff(pagina, app_viva)
    staff = app_viva.db.query(Usuario).filter(Usuario.email == EMAIL_STAFF).one()
    persona = get_or_create_persona(app_viva.db, "3001234567", "Ana")
    if deuda:
        registrar_movimiento_saldo(app_viva.db, persona.id, -deuda, staff)
    app_viva.db.commit()
    p = anunciar_paquete(app_viva)
    recibir_paquete_en_bd(app_viva, p, "GUIA-9")
    return p, persona


def _bloqueado(pagina, prefijo):
    """El resto del modal (Posición, guía, cobro...) está en gris e `inert`."""
    return pagina.evaluate(
        """id => { const b = [...document.querySelectorAll('[data-entregar-bloqueado="' + id + '"]')];
                   return b.length > 0 && b.every(x => x.inert && x.classList.contains('opacity-40')); }""",
        prefijo,
    )


def test_con_deuda_el_modal_queda_bloqueado_y_cobrar_lo_habilita(app_viva, pagina):
    p, persona = _preparar(app_viva, pagina)
    abrir_modal_entregar(pagina, app_viva, p)
    boton = pagina.locator(f"#entregar-boton-{p.id}")

    assert pagina.locator(f"#modal-deliver-{p.id}").get_by_role("button", name="Cobrar $5,000").is_visible()
    assert boton.is_disabled()
    assert _bloqueado(pagina, str(p.id))

    pagina.click(f"#cobrar-saldo-{p.id}")

    assert pagina.locator(f"#pago-saldo-{p.id}").input_value() == "5000"
    assert boton.is_enabled()
    assert not _bloqueado(pagina, str(p.id))
    assert not pagina.locator(f"#no-cobrar-link-{p.id}").is_visible()
    with pagina.expect_navigation():
        boton.click()
    entregado = paquete_en_bd(app_viva, p.id)
    assert entregado.estado == EstadoPaquete.ENTREGADO
    assert entregado.saldo_no_cobrado is None
    assert saldo_de_persona(app_viva.db, persona.id) == 0


def test_abono_parcial_muestra_queda_debiendo_y_uno_mayor_que_la_deuda_un_error(app_viva, pagina):
    p, _ = _preparar(app_viva, pagina)
    abrir_modal_entregar(pagina, app_viva, p)
    boton = pagina.locator(f"#entregar-boton-{p.id}")
    enlace = pagina.locator(f"#no-cobrar-link-{p.id}")

    pagina.fill(f"#pago-saldo-{p.id}", "2000")
    assert pagina.locator(f"#queda-debiendo-{p.id}").inner_text() == "Queda debiendo $3,000"
    assert enlace.inner_text() == "Entregar debiendo $3,000"
    assert boton.is_disabled()  # parcial: hace falta el motivo

    pagina.fill(f"#pago-saldo-{p.id}", "6000")
    error = pagina.locator(f"#pago-saldo-error-{p.id}")
    assert error.is_visible()
    assert "mayor que la deuda ($5,000)" in error.inner_text()
    assert not enlace.is_visible()
    assert not pagina.locator(f"#queda-debiendo-{p.id}").is_visible()
    assert boton.is_disabled()

    pagina.fill(f"#pago-saldo-{p.id}", "5000")  # el abono completo resuelve solo
    assert boton.is_enabled()
    assert not error.is_visible()


def test_no_cobrar_ahora_pide_motivo_con_cuenta_regresiva_y_entrega(app_viva, pagina):
    p, persona = _preparar(app_viva, pagina)
    abrir_modal_entregar(pagina, app_viva, p)
    enlace = pagina.locator(f"#no-cobrar-link-{p.id}")
    assert enlace.inner_text() == "No cobrar ahora"

    enlace.click()
    dialogo = pagina.locator(f"#modal-no-cobro-{p.id}")
    confirmar = pagina.locator(f"#no-cobro-{p.id}-confirmar")
    assert dialogo.is_visible()
    assert "Entregar sin cobrar" in confirmar.inner_text()
    assert "(3)" in confirmar.inner_text()
    assert confirmar.is_disabled()
    assert pagina.locator(f"#no-cobro-{p.id}-otro").is_visible()  # la nota opcional se ve siempre

    dialogo.get_by_label("Paga después").check()
    pagina.fill(f"#no-cobro-{p.id}-otro", "el viernes")
    pagina.wait_for_timeout(3200)  # pasó la cuenta regresiva
    assert confirmar.is_enabled()
    with pagina.expect_navigation():
        confirmar.click()

    entregado = paquete_en_bd(app_viva, p.id)
    assert entregado.estado == EstadoPaquete.ENTREGADO
    assert entregado.motivo_no_cobro == "paga_despues: el viernes"
    assert entregado.saldo_no_cobrado == _DEUDA
    assert saldo_de_persona(app_viva.db, persona.id) == -_DEUDA


def test_regresar_cierra_el_dialogo_y_el_modal_sigue_bloqueado(app_viva, pagina):
    p, _ = _preparar(app_viva, pagina)
    abrir_modal_entregar(pagina, app_viva, p)
    pagina.click(f"#no-cobrar-link-{p.id}")

    pagina.click(f"#no-cobro-{p.id}-regresar")

    assert not pagina.locator(f"#modal-no-cobro-{p.id}").is_visible()
    assert pagina.locator(f"#modal-deliver-{p.id}").is_visible()
    assert pagina.locator(f"#entregar-boton-{p.id}").is_disabled()
    assert _bloqueado(pagina, str(p.id))


def test_abono_parcial_con_motivo_desde_consultar(app_viva, pagina):
    p, persona = _preparar(app_viva, pagina)
    pagina.goto(f"{app_viva.url}/consultar?q={p.access_code}")
    pagina.locator('[data-open="modal-entregar-consultar"]:visible').first.click()
    pagina.wait_for_function("() => !document.getElementById('modal-entregar-consultar').hidden")
    assert pagina.locator("#entregar-boton-consultar").is_disabled()
    assert _bloqueado(pagina, "consultar")

    pagina.fill("#pago-saldo-consultar", "2000")
    assert pagina.locator("#queda-debiendo-consultar").inner_text() == "Queda debiendo $3,000"
    pagina.click("#no-cobrar-link-consultar")
    dialogo = pagina.locator("#modal-no-cobro-consultar")
    confirmar = pagina.locator("#no-cobro-consultar-confirmar")
    assert "Entregar debiendo $3,000" in confirmar.inner_text()
    dialogo.get_by_label("No tiene efectivo").check()
    pagina.wait_for_timeout(3200)
    with pagina.expect_navigation():
        confirmar.click()

    entregado = paquete_en_bd(app_viva, p.id)
    assert entregado.estado == EstadoPaquete.ENTREGADO
    assert entregado.motivo_no_cobro == "sin_efectivo"
    assert entregado.saldo_no_cobrado == 3000
    assert saldo_de_persona(app_viva.db, persona.id) == -3000
    assert pagina.get_by_text("Entregado sin cobrar ($3,000)").is_visible()


def test_sin_deuda_el_modal_es_el_de_siempre(app_viva, pagina):
    p, _ = _preparar(app_viva, pagina, deuda=0)
    abrir_modal_entregar(pagina, app_viva, p)

    assert pagina.locator(f"#cobrar-saldo-{p.id}").count() == 0
    assert pagina.locator(f'[data-entregar-bloqueado="{p.id}"]').count() == 0
    boton = pagina.locator(f"#entregar-boton-{p.id}")
    assert boton.is_enabled()
    with pagina.expect_navigation():
        boton.click()
    assert paquete_en_bd(app_viva, p.id).estado == EstadoPaquete.ENTREGADO
