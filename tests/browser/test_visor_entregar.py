# -*- coding: utf-8 -*-
"""
Seam de navegador real — botón Entregar dentro del visor de fotos de /consultar (pedido del cliente 2026-10-30:
botón Entregar junto a la Posición en el visor).

El staff amplía la foto del paquete, ve en la misma fila el 📍 + Posición y un botón "Entregar"; al tocarlo, el
visor se cierra (y devuelve el scroll del body) y queda abierto el modal Entregar de la página. Sin sesión, o con
el paquete ya entregado, el visor no trae el botón.
"""

import tempfile
from pathlib import Path

from app.domain.foto_storage import LocalFotoStorage
from app.domain.paquete_foto_service import agregar_foto
from app.domain.paquete_lifecycle import deliver, receive
from app.domain.usuario import Usuario

from _ayudantes import EMAIL_STAFF, anunciar_paquete, iniciar_sesion_staff

_VISOR = "#visor-recibido"
_BOTON = '[data-visor-entregar="recibido"]'
_MODAL = "modal-entregar-consultar"


def _recibido_con_foto(app_viva, posicion="41"):
    staff = app_viva.db.query(Usuario).filter(Usuario.email == EMAIL_STAFF).one()
    p = anunciar_paquete(app_viva)
    receive(app_viva.db, p, staff, posicion=posicion)
    agregar_foto(app_viva.db, p, LocalFotoStorage(Path(tempfile.mkdtemp())), "uno.jpg", b"uno")
    app_viva.db.commit()
    return p, staff


def _abrir_visor(pagina, app_viva, p):
    pagina.goto(f"{app_viva.url}/consultar?q={p.access_code}")
    pagina.locator('[data-visor-abrir="recibido"]').first.click()
    pagina.wait_for_selector(_VISOR, state="visible")


def test_el_staff_entrega_desde_el_visor_y_el_visor_se_cierra(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p, _ = _recibido_con_foto(app_viva)
    _abrir_visor(pagina, app_viva, p)

    boton = pagina.locator(f"{_VISOR} {_BOTON}")
    assert boton.is_visible()
    # Misma fila que la Posición: centros verticales alineados.
    caja_boton = boton.bounding_box()
    caja_pos = pagina.locator(f'{_VISOR} [title="Posición en el estante"]').bounding_box()
    assert abs((caja_boton["y"] + caja_boton["height"] / 2) - (caja_pos["y"] + caja_pos["height"] / 2)) < 4

    boton.click()

    pagina.wait_for_selector(_VISOR, state="hidden")
    pagina.wait_for_function("id => !document.getElementById(id).hidden", arg=_MODAL)
    assert pagina.locator(f"#{_MODAL}").is_visible()
    assert pagina.evaluate("document.body.style.overflow") == ""


def test_sin_sesion_el_visor_no_trae_el_boton_entregar(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p, _ = _recibido_con_foto(app_viva)
    pagina.context.clear_cookies()
    _abrir_visor(pagina, app_viva, p)

    assert pagina.locator(f"{_VISOR} {_BOTON}").count() == 0


def test_un_paquete_entregado_no_trae_el_boton_entregar_en_el_visor(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    p, staff = _recibido_con_foto(app_viva)
    deliver(app_viva.db, p, staff)
    app_viva.db.commit()
    _abrir_visor(pagina, app_viva, p)

    assert pagina.locator(f"{_VISOR} {_BOTON}").count() == 0
