# -*- coding: utf-8 -*-
"""
Seam de navegador real — Recibir con apartamento obligatorio (pedido del cliente 2026-10-30, migración 0073).

Destinatario sin apartamento + catálogo cargado: "Recibir" queda deshabilitado hasta elegir Torre; un número que
existe en UNA sola Torre la elige solo; y saltarse el apartamento pasa por un diálogo con motivo y 3 s de espera.
"""

import pytest

from app.domain.apartamento import Apartamento
from app.domain.apartamento_service import resolver_apartamento
from app.domain.paquete import EstadoPaquete

from _ayudantes import abrir_modal_recibir, anunciar_paquete, foco_en, iniciar_sesion_staff, paquete_en_bd


# El catálogo real repite cada número en varias Torres: una unidad que solo existe en TORRE 2, para la elección
# automática. El catálogo no se trunca entre pruebas, así que se quita al terminar cada una.
_SOLO_TORRE_2 = "9902"


@pytest.fixture(autouse=True)
def _sin_unidad_de_prueba(app_viva):
    yield
    app_viva.db.rollback()
    app_viva.db.query(Apartamento).filter(Apartamento.apartamento == _SOLO_TORRE_2).delete()
    app_viva.db.commit()


def _preparar(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    resolver_apartamento(app_viva.db, "TORRE 1", "101")
    resolver_apartamento(app_viva.db, "TORRE 2", "101")
    app_viva.db.add(Apartamento(conjunto=resolver_apartamento(app_viva.db, "TORRE 2", "101").conjunto,
                                torre="TORRE 2", apartamento=_SOLO_TORRE_2))
    app_viva.db.commit()
    p = anunciar_paquete(app_viva)
    abrir_modal_recibir(pagina, app_viva, p, sin_apartamento=None)
    return p


def _boton_recibir(pagina, p):
    return pagina.locator(f"#recibir-boton-{p.id}")


def _resto_inerte(pagina, p):
    """El resto del form (guía, tipo, condición, fotos, Posición...) está en gris e `inert`."""
    return pagina.evaluate(
        "id => { const r = document.getElementById('recibir-resto-' + id); return r.inert && r.classList.contains('opacity-40'); }",
        str(p.id),
    )


def test_recibir_deshabilitado_hasta_elegir_torre(app_viva, pagina):
    p = _preparar(app_viva, pagina)
    boton = _boton_recibir(pagina, p)
    assert boton.is_disabled()
    assert pagina.locator(f"#recibir-hint-torre-{p.id}").is_visible()
    assert foco_en(pagina) == f"picker-apto-input-recibir-{p.id}"  # el campo de apartamento toma el foco
    assert _resto_inerte(pagina, p)
    pagina.locator(f"#guia-{p.id}").click(force=True, timeout=2000)  # un clic sobre la guía no la enfoca
    assert foco_en(pagina) != f"guia-{p.id}"

    pagina.fill(f"#picker-apto-input-recibir-{p.id}", "101")  # existe en 2 Torres: hay que elegir
    assert boton.is_disabled()
    pagina.locator(f"#picker-torres-posibles-recibir-{p.id}").get_by_role("button", name="1", exact=True).click()

    assert boton.is_enabled()
    assert not _resto_inerte(pagina, p)
    assert not pagina.locator(f"#recibir-hint-torre-{p.id}").is_visible()
    with pagina.expect_navigation():
        boton.click()
    recibido = paquete_en_bd(app_viva, p.id)
    assert recibido.estado == EstadoPaquete.RECIBIDO
    assert (recibido.snapshot_torre, recibido.snapshot_apartamento) == ("TORRE 1", "101")


def test_un_numero_de_una_sola_torre_la_elige_solo(app_viva, pagina):
    p = _preparar(app_viva, pagina)

    pagina.fill(f"#picker-apto-input-recibir-{p.id}", _SOLO_TORRE_2)  # solo existe en TORRE 2

    assert pagina.locator(f"#picker-hid-torre-recibir-{p.id}").input_value() == "TORRE 2"
    assert pagina.locator(f"#picker-confirmacion-recibir-{p.id}").is_visible()
    assert _boton_recibir(pagina, p).is_enabled()
    assert pagina.locator(f"#recibir-candidato-nuevo-{p.id}").is_enabled()  # mismo evento que tocar la tarjeta
    assert not _resto_inerte(pagina, p)

    pagina.fill(f"#picker-apto-input-recibir-{p.id}", "")  # limpiar el apartamento vuelve a apagar el resto
    assert _resto_inerte(pagina, p)
    assert _boton_recibir(pagina, p).is_disabled()


def test_dialogo_sin_apartamento_cuenta_regresiva_y_motivo_habilitan_recibir(app_viva, pagina):
    p = _preparar(app_viva, pagina)
    pagina.click(f"#recibir-sin-apto-link-{p.id}")
    dialogo = pagina.locator(f"#modal-sin-apto-{p.id}")
    confirmar = pagina.locator(f"#sin-apto-{p.id}-confirmar")
    assert dialogo.is_visible()
    assert "(3)" in confirmar.inner_text()
    assert confirmar.is_disabled()

    dialogo.get_by_role("radio", name="Otro motivo").check()
    pagina.fill(f"#sin-apto-{p.id}-otro", "abc")  # muy corto
    pagina.wait_for_timeout(3200)  # pasó la cuenta regresiva
    assert "(" not in confirmar.inner_text()
    assert confirmar.is_disabled()  # el texto todavía no alcanza

    dialogo.get_by_label("La etiqueta no lo trae").check()
    assert confirmar.is_enabled()
    with pagina.expect_navigation():
        confirmar.click()

    recibido = paquete_en_bd(app_viva, p.id)
    assert recibido.estado == EstadoPaquete.RECIBIDO
    assert recibido.snapshot_apartamento is None
    assert recibido.sin_apartamento_motivo == "etiqueta"


def test_regresar_cierra_el_dialogo_y_enfoca_el_campo(app_viva, pagina):
    p = _preparar(app_viva, pagina)
    pagina.click(f"#recibir-sin-apto-link-{p.id}")

    pagina.click(f"#sin-apto-{p.id}-regresar")

    assert not pagina.locator(f"#modal-sin-apto-{p.id}").is_visible()
    assert pagina.locator(f"#modal-receive-{p.id}").is_visible()
    assert foco_en(pagina) == f"picker-apto-input-recibir-{p.id}"


def test_escape_cierra_solo_el_dialogo_de_arriba(app_viva, pagina):
    p = _preparar(app_viva, pagina)
    pagina.click(f"#recibir-sin-apto-link-{p.id}")

    pagina.keyboard.press("Escape")

    assert not pagina.locator(f"#modal-sin-apto-{p.id}").is_visible()
    assert pagina.locator(f"#modal-receive-{p.id}").is_visible()


def test_sin_apartamento_ni_fotos_primero_el_apartamento_y_despues_las_fotos(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    resolver_apartamento(app_viva.db, "TORRE 1", "101")
    app_viva.db.commit()
    p = anunciar_paquete(app_viva)
    abrir_modal_recibir(pagina, app_viva, p, sin_fotos=None, sin_apartamento=None)
    assert pagina.locator(f"#recibir-sin-apto-link-{p.id}").is_visible()
    assert not pagina.locator(f"#recibir-sin-fotos-link-{p.id}").is_visible()  # primero el apartamento

    pagina.click(f"#recibir-sin-apto-link-{p.id}")
    pagina.locator(f"#modal-sin-apto-{p.id}").get_by_label("Etiqueta ilegible o dañada").check()
    pagina.wait_for_function("id => !document.getElementById(id).disabled", arg=f"sin-apto-{p.id}-confirmar")
    pagina.click(f"#sin-apto-{p.id}-confirmar")
    pagina.wait_for_timeout(300)

    assert paquete_en_bd(app_viva, p.id).estado == EstadoPaquete.ANUNCIADO  # todavía faltan las fotos
    assert pagina.locator(f"#recibir-sin-apto-motivo-{p.id}").input_value() == "ilegible"
    assert not _resto_inerte(pagina, p)  # con el motivo confirmado el resto del form se usa normal
    assert _boton_recibir(pagina, p).is_disabled()
    assert not pagina.locator(f"#recibir-sin-apto-link-{p.id}").is_visible()
    assert pagina.locator(f"#recibir-sin-fotos-link-{p.id}").is_visible()

    pagina.click(f"#recibir-sin-fotos-link-{p.id}")
    pagina.locator(f"#modal-sin-fotos-{p.id}").get_by_label("La cámara no funciona").check()
    pagina.wait_for_function("id => !document.getElementById(id).disabled", arg=f"sin-fotos-{p.id}-confirmar")
    with pagina.expect_navigation():
        pagina.click(f"#sin-fotos-{p.id}-confirmar")
    recibido = paquete_en_bd(app_viva, p.id)
    assert recibido.estado == EstadoPaquete.RECIBIDO
    assert (recibido.sin_apartamento_motivo, recibido.sin_fotos_motivo) == ("ilegible", "camara")
