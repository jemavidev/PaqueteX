# -*- coding: utf-8 -*-
"""
Seam de navegador real — Recibir sin apartamento: "Nuevo residente" deshabilitado hasta elegir unidad (issue 377,
`.scratch/pendientes-cliente`).

Sin unidad, "Nuevo residente" no tiene dónde registrar a nadie: la tarjeta queda deshabilitada (con una nota) hasta
que el selector de Torre/Apartamento del mismo modal tenga una unidad elegida, y vuelve a deshabilitarse (cerrando lo
que se hubiera abierto) si se cambia el apartamento. Recibir en sí nunca se bloquea -- eso lo cubre la prueba HTTP.
"""

from app.domain.apartamento_service import resolver_apartamento

from _ayudantes import abrir_modal_recibir, anunciar_paquete, iniciar_sesion_staff


def test_nuevo_residente_se_habilita_solo_con_un_apartamento_elegido(app_viva, pagina):
    iniciar_sesion_staff(pagina, app_viva)
    resolver_apartamento(app_viva.db, "TORRE 1", "101")
    app_viva.db.commit()
    p = anunciar_paquete(app_viva)
    abrir_modal_recibir(pagina, app_viva, p)

    radio = pagina.locator(f"#recibir-candidato-nuevo-{p.id}")
    nota = pagina.locator(f"#recibir-nuevo-ocupante-nota-{p.id}")
    seccion = pagina.locator(f"#recibir-nuevo-ocupante-{p.id}")
    assert radio.is_disabled()
    assert nota.is_visible()

    pagina.fill(f"#picker-apto-input-recibir-{p.id}", "101")
    pagina.locator(f"#picker-torres-posibles-recibir-{p.id}").get_by_role("button", name="1", exact=True).click()

    assert radio.is_enabled()
    assert not nota.is_visible()
    pagina.locator(f'label:has(#recibir-candidato-nuevo-{p.id})').click()
    assert radio.is_checked()
    assert seccion.is_visible()

    pagina.fill(f"#picker-apto-input-recibir-{p.id}", "10")  # otro apartamento: ya no hay unidad elegida

    assert radio.is_disabled()
    assert not radio.is_checked()
    assert not seccion.is_visible()
    assert nota.is_visible()
