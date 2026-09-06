# -*- coding: utf-8 -*-
"""
Capa web — ruta `/anunciar` (Grupo 1 de ajustes-post-referencia-funcional).

Simplificada a teléfono + acepta_tyc siempre, más nombre CONDICIONAL (issue
.scratch/anunciar-atajo-telefono-conocido) — el cliente ya NO elige "a
nombre de quién llega". El formulario arranca con solo Teléfono + Términos;
el campo Nombre aparece recién si el teléfono no tiene ningún paquete
`ENTREGADO` histórico (si lo tiene, se anuncia directo a su nombre ya
registrado, `Destinatario.yo_mismo()`). Comportamiento observable por HTTP:
el formulario, la creación del Paquete `ANUNCIADO` con el nombre correcto
según el camino, la pantalla de éxito con los datos nuevos, y las
validaciones sin efecto en la BD.
"""

import re

from app.domain.apartamento_service import (
    resolver_apartamento,
    set_apartamento_actual,
)
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.persona import Persona
from app.domain.persona_service import get_or_create_persona


def _acepta_tyc_marcado(html: str) -> bool:
    match = re.search(r'<input[^>]*id="acepta_tyc"[^>]*>', html)
    assert match, "no se encontró el checkbox de Términos y Condiciones"
    return "checked" in match.group(0)


def _cuenta_paquetes(client) -> int:
    return client.db.query(Paquete).count()


def _crear_paquete_historico(client, estado, telefono="3001234567", nombre="Ana"):
    """Deja un paquete de `telefono` en el `estado` pedido (ANUNCIADO,
    RECIBIDO, ENTREGADO o CANCELADO) -- sin pasar por HTTP, ya que la
    elección de Destinatario en sí no es lo que este archivo prueba. Usada
    para poblar el historial que decide si un teléfono es "conocido"
    (.scratch/anunciar-atajo-telefono-conocido: solo ENTREGADO cuenta)."""
    from app.domain.paquete_lifecycle import cancel, deliver, receive
    from app.domain.paquete_service import Destinatario, announce
    from app.domain.staff_service import create_initial_admin

    paquete = announce(
        client.db,
        anunciante_telefono=telefono,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    if estado == EstadoPaquete.ANUNCIADO:
        return paquete

    staff = create_initial_admin(client.db, "admin@club.com", "Admin", "Contrasena1")
    if estado == EstadoPaquete.CANCELADO:
        cancel(client.db, paquete, staff, "Ya no llegó")
        client.db.commit()
        return paquete

    receive(client.db, paquete, staff)
    if estado == EstadoPaquete.RECIBIDO:
        client.db.commit()
        return paquete

    deliver(client.db, paquete, staff)
    client.db.commit()
    return paquete


def _crear_entregado(client, telefono="3001234567", nombre="Ana"):
    return _crear_paquete_historico(client, EstadoPaquete.ENTREGADO, telefono, nombre)


def test_get_announce_renderiza_el_formulario_de_2_campos_iniciales(client):
    r = client.get("/anunciar")
    assert r.status_code == 200
    html = r.text.lower()
    assert 'name="telefono"' in html
    assert 'name="acepta_tyc"' in html
    # El Nombre solo aparece si el teléfono no resulta "conocido" (ver
    # tests de más abajo) -- .scratch/anunciar-atajo-telefono-conocido.
    assert 'name="nombre"' not in html
    # Ya no se elige "a nombre de quién" en esta vista.
    assert "a_nombre_de" not in html
    # Sin captura de número de guía (la captura el staff al recibir).
    assert "guide" not in html and "guía" not in html and 'name="guia"' not in html


def test_post_crea_paquete_anunciado_con_el_nombre_declarado(client):
    r = client.post(
        "/anunciar",
        data={"nombre": "Ana", "telefono": "3001234567", "acepta_tyc": "on"},
    )
    assert r.status_code == 200
    paquetes = client.db.query(Paquete).all()
    assert len(paquetes) == 1
    p = paquetes[0]
    assert p.estado == EstadoPaquete.ANUNCIADO
    assert p.recipient_name == "ANA"
    # El teléfono anunciante queda como contacto por defecto de este paquete.
    assert p.recipient_phone == "+573001234567"
    assert p.announced_by_phone == "+573001234567"


def test_confirmacion_muestra_nombre_telefono_y_enlaces_pero_nunca_el_codigo(client):
    r = client.post(
        "/anunciar",
        data={"nombre": "Ana", "telefono": "3001234567", "acepta_tyc": "on"},
    )
    assert r.status_code == 200
    p = client.db.query(Paquete).one()
    assert "ANA" in r.text
    assert "+573001234567" in r.text
    # El código de acceso NUNCA se muestra en esta vista pública (pedido
    # explícito del cliente) -- llega solo por SMS/WhatsApp/Email. Tampoco
    # debe viajar en la URL de la propia página (queda en historial del
    # navegador y en logs de acceso del servidor).
    assert p.access_code not in r.text
    assert p.access_code not in str(r.url)
    assert str(p.id) in str(r.url)
    assert 'href="/consultar"' in r.text
    assert 'href="/otp"' in r.text


def test_confirmacion_muestra_apartamento_cuando_el_anunciante_ya_tiene(client):
    get_or_create_persona(client.db, "3001234567", "Ana")
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    set_apartamento_actual(client.db, "3001234567", apto)
    client.db.commit()

    r = client.post(
        "/anunciar",
        data={"nombre": "Ana", "telefono": "3001234567", "acepta_tyc": "on"},
    )
    assert r.status_code == 200
    assert "EL CLUB" in r.text and "101" in r.text
    # Bug real reportado en vivo: "T TORRE 1" (T de más antes de TORRE) --
    # mismo patrón que issue 152 (`snapshot_torre` ya trae el prefijo del
    # catálogo, "TORRE 1"), pero acá era un "T " literal concatenado antes
    # de pegarlo, sin pasar por `torre_sin_prefijo`.
    assert "T TORRE 1" not in r.text
    assert "TORRE 1" in r.text


def test_nombre_declarado_con_typo_usa_el_nombre_registrado_del_anunciante(client):
    # Ana ya está registrada; alguien anuncia con su teléfono pero escribe mal
    # el nombre -- conversación 2026-08-15 (pedido explícito): el nombre
    # escrito solo se honra si coincide con un co-residente de la MISMA
    # unidad del anunciante; sin unidad (este caso) o sin esa coincidencia,
    # el anuncio queda a nombre del propio Anunciante YA REGISTRADO, no del
    # texto tal cual lo escribió.
    get_or_create_persona(client.db, "3001234567", "Ana Perez")
    client.db.commit()

    r = client.post(
        "/anunciar",
        data={"nombre": "Ana Peres", "telefono": "3001234567", "acepta_tyc": "on"},
    )
    assert r.status_code == 200
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "ANA PEREZ"
    # No se crea una segunda Persona — el teléfono ya existía.
    assert client.db.query(Persona).count() == 1


def test_post_sin_tyc_no_crea_paquete(client):
    r = client.post(
        "/anunciar", data={"nombre": "Ana", "telefono": "3001234567"}
    )
    assert r.status_code == 400
    assert _cuenta_paquetes(client) == 0


# --------------------------------------------------------------------------- #
# Bug real reportado en vivo: el checkbox de Términos aparecía destildado en
# CUALQUIER re-render con error, aunque ya se hubiera aceptado -- notorio
# ahora que pedir el Nombre (teléfono no conocido) es el camino esperado
# tras aceptar Términos, no solo un error de usuario.
# --------------------------------------------------------------------------- #
def test_acepta_tyc_permanece_marcado_al_pedir_nombre(client):
    r = client.post(
        "/anunciar", data={"telefono": "3001234567", "acepta_tyc": "on"}
    )
    assert r.status_code == 400
    assert 'name="nombre"' in r.text.lower()
    assert _acepta_tyc_marcado(r.text)


def test_acepta_tyc_no_marcado_en_carga_limpia(client):
    r = client.get("/anunciar")
    assert not _acepta_tyc_marcado(r.text)


def test_acepta_tyc_no_marcado_si_no_se_acepto(client):
    r = client.post("/anunciar", data={"telefono": "3001234567"})
    assert r.status_code == 400
    assert not _acepta_tyc_marcado(r.text)


def test_post_sin_telefono_no_crea_paquete(client):
    r = client.post("/anunciar", data={"nombre": "Ana", "acepta_tyc": "on"})
    assert r.status_code == 400
    assert _cuenta_paquetes(client) == 0


def test_post_sin_nombre_no_crea_paquete(client):
    # Teléfono NO conocido (nunca se le entregó nada) -- sigue exigiendo
    # Nombre, exactamente como antes de .scratch/anunciar-atajo-telefono-
    # conocido. Ver el caso hermano (conocido) más abajo.
    r = client.post(
        "/anunciar", data={"telefono": "3001234567", "acepta_tyc": "on"}
    )
    assert r.status_code == 400
    assert _cuenta_paquetes(client) == 0
    # El campo aparece recién ahora, pidiéndolo.
    assert 'name="nombre"' in r.text.lower()


def test_post_sin_nombre_pero_telefono_conocido_si_anuncia(client):
    # issue .scratch/anunciar-atajo-telefono-conocido: al menos 1 paquete
    # ENTREGADO histórico a este teléfono -- el atajo deja anunciar sin
    # pedir Nombre, usando el nombre YA REGISTRADO.
    _crear_entregado(client, telefono="3001234567", nombre="Ana")

    r = client.post(
        "/anunciar", data={"telefono": "3001234567", "acepta_tyc": "on"}
    )
    assert r.status_code == 200
    anunciados = (
        client.db.query(Paquete)
        .filter(Paquete.estado == EstadoPaquete.ANUNCIADO)
        .all()
    )
    assert len(anunciados) == 1
    assert anunciados[0].recipient_name == "ANA"


def test_post_sin_nombre_con_solo_anunciado_sigue_pidiendo_nombre(client):
    # Un paquete ANUNCIADO (nunca entregado) NO cuenta como "conocido".
    _crear_paquete_historico(client, EstadoPaquete.ANUNCIADO)

    r = client.post(
        "/anunciar", data={"telefono": "3001234567", "acepta_tyc": "on"}
    )
    assert r.status_code == 400
    assert 'name="nombre"' in r.text.lower()
    assert _cuenta_paquetes(client) == 1  # sigue siendo solo el ANUNCIADO previo


def test_post_sin_nombre_con_solo_recibido_sigue_pidiendo_nombre(client):
    # Un paquete RECIBIDO (todavía no entregado al residente) tampoco cuenta.
    _crear_paquete_historico(client, EstadoPaquete.RECIBIDO)

    r = client.post(
        "/anunciar", data={"telefono": "3001234567", "acepta_tyc": "on"}
    )
    assert r.status_code == 400
    assert 'name="nombre"' in r.text.lower()
    assert _cuenta_paquetes(client) == 1  # sigue siendo solo el RECIBIDO previo


def test_post_sin_nombre_con_solo_cancelado_sigue_pidiendo_nombre(client):
    # Un paquete CANCELADO (nunca llegó a entregarse) tampoco cuenta.
    _crear_paquete_historico(client, EstadoPaquete.CANCELADO)

    r = client.post(
        "/anunciar", data={"telefono": "3001234567", "acepta_tyc": "on"}
    )
    assert r.status_code == 400
    assert 'name="nombre"' in r.text.lower()
    assert _cuenta_paquetes(client) == 1  # sigue siendo solo el CANCELADO previo


def test_con_nombre_provisto_y_telefono_desconocido_funciona_igual_que_antes(client):
    # 2do intento: campo Nombre ya visible y diligenciado -- flujo de
    # siempre (`Destinatario.declarado_por_cliente`), sin cambios.
    r = client.post(
        "/anunciar",
        data={"nombre": "Ana", "telefono": "3001234567", "acepta_tyc": "on"},
    )
    assert r.status_code == 200
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "ANA"


# --------------------------------------------------------------------------- #
# Foco condicional (versión móvil, `.scratch/pendientes-cliente`): autofocus
# SOLO en una carga limpia -- con error, activarlo dispara el teclado y tapa
# el mensaje de error en mobile.
# --------------------------------------------------------------------------- #
def test_get_announce_limpio_tiene_autofocus(client):
    r = client.get("/anunciar")
    assert "autofocus" in r.text


def test_post_announce_con_error_no_tiene_autofocus(client):
    r = client.post("/anunciar", data={"telefono": "3001234567"})
    assert r.status_code == 400
    assert "autofocus" not in r.text


# --------------------------------------------------------------------------- #
# Límite de anuncios activos por teléfono (`.scratch/pendientes-cliente`,
# grillado con el cliente) -- evita ráfagas de SMS por error o abuso.
# --------------------------------------------------------------------------- #
def _anunciar(client, telefono="3001234567", nombre="Ana", confirmar=False):
    data = {"nombre": nombre, "telefono": telefono, "acepta_tyc": "on"}
    if confirmar:
        data["confirmar_multiple"] = "1"
    return client.post("/anunciar", data=data)


def _anunciar_sin_nombre(client, telefono="3001234567", confirmar=False):
    # Camino del atajo de cliente conocido -- sin el campo Nombre en el
    # POST, tal como lo manda el formulario cuando `mostrar_nombre` es
    # False (.scratch/anunciar-atajo-telefono-conocido).
    data = {"telefono": telefono, "acepta_tyc": "on"}
    if confirmar:
        data["confirmar_multiple"] = "1"
    return client.post("/anunciar", data=data)


def test_primer_anuncio_no_muestra_pantalla_intermedia(client):
    r = _anunciar(client)
    assert r.status_code == 200
    assert "¿Quieres anunciar otro" not in r.text
    assert _cuenta_paquetes(client) == 1


def test_segundo_anuncio_muestra_pantalla_intermedia_sin_crear_el_paquete(client):
    _anunciar(client)
    r = _anunciar(client)
    assert r.status_code == 200
    assert "Ya tienes 1" in r.text
    assert "¿Quieres anunciar otro" in r.text
    assert _cuenta_paquetes(client) == 1  # el segundo NO se creó todavía


def test_pantalla_intermedia_nunca_menciona_el_codigo_de_acceso_existente(client):
    r1 = _anunciar(client)
    p1 = client.db.query(Paquete).one()
    # El código de acceso no aparece en NINGUNA vista pública -- ni en la
    # propia confirmación (llega por SMS/WhatsApp/Email, no en pantalla) ni,
    # con más razón, en el aviso del segundo intento sobre un paquete ajeno.
    assert p1.access_code not in r1.text

    r2 = _anunciar(client)
    assert p1.access_code not in r2.text  # pero NUNCA en el aviso del 2do intento


def test_confirmar_multiple_crea_el_segundo_paquete(client):
    _anunciar(client)
    r = _anunciar(client, confirmar=True)
    assert r.status_code == 200
    assert _cuenta_paquetes(client) == 2


def test_confirmar_multiple_de_otro_telefono_no_afecta_este(client):
    _anunciar(client, telefono="3001234567")
    r = _anunciar(client, telefono="3019999999", nombre="Beto")
    assert r.status_code == 200
    assert "¿Quieres anunciar otro" not in r.text  # Beto nunca ha anunciado
    assert _cuenta_paquetes(client) == 2


def test_llegar_al_maximo_bloquea_sin_opcion_de_confirmar(client):
    from app.domain.paquete_service import MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO

    for _ in range(MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO):
        r = _anunciar(client, confirmar=True)
    assert _cuenta_paquetes(client) == MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO

    r = _anunciar(client, confirmar=True)  # el 11vo, incluso confirmando
    assert r.status_code == 400
    assert "máximo" in r.text.lower()
    assert _cuenta_paquetes(client) == MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO  # sin cambios


def test_recibir_uno_libera_espacio_bajo_el_limite(client):
    from app.domain.paquete_lifecycle import receive
    from app.domain.paquete_service import MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO
    from app.domain.staff_service import create_initial_admin

    staff = create_initial_admin(client.db, "admin@club.com", "Admin", "Contrasena1")

    for _ in range(MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO):
        _anunciar(client, confirmar=True)

    primero = client.db.query(Paquete).order_by(Paquete.created_at.asc()).first()
    receive(client.db, primero, staff)
    client.db.commit()

    r = _anunciar(client, confirmar=True)
    assert r.status_code == 200
    assert _cuenta_paquetes(client) == MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO + 1


# --------------------------------------------------------------------------- #
# El límite de activos (arriba) sigue aplicando igual por el camino sin
# Nombre del atajo de cliente conocido -- .scratch/anunciar-atajo-telefono-
# conocido, Testing Decisions.
# --------------------------------------------------------------------------- #
def test_conocido_sin_nombre_respeta_pantalla_intermedia_del_limite(client):
    _crear_entregado(client, telefono="3001234567", nombre="Ana")

    r1 = _anunciar_sin_nombre(client)
    assert r1.status_code == 200
    assert _cuenta_paquetes(client) == 2  # el ENTREGADO previo + este ANUNCIADO

    r2 = _anunciar_sin_nombre(client)
    assert r2.status_code == 200
    assert "¿Quieres anunciar otro" in r2.text
    assert _cuenta_paquetes(client) == 2  # el segundo NO se creó todavía

    r3 = _anunciar_sin_nombre(client, confirmar=True)
    assert r3.status_code == 200
    assert _cuenta_paquetes(client) == 3


def test_conocido_sin_nombre_tambien_llega_al_tope_duro(client):
    from app.domain.paquete_service import MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO

    _crear_entregado(client, telefono="3001234567", nombre="Ana")

    for _ in range(MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO):
        _anunciar_sin_nombre(client, confirmar=True)

    r = _anunciar_sin_nombre(client, confirmar=True)  # el siguiente, incluso confirmando
    assert r.status_code == 400
    assert "máximo" in r.text.lower()


# --------------------------------------------------------------------------- #
# `mostrar_nombre` (oculto en el template) es "pegajoso" una vez revelado --
# no debe desaparecer si el cliente tropieza con OTRO campo antes de llegar
# a escribir su nombre (.scratch/anunciar-atajo-telefono-conocido).
# --------------------------------------------------------------------------- #
def test_mostrar_nombre_no_desaparece_si_falla_otro_campo_primero(client):
    r1 = client.post(
        "/anunciar", data={"telefono": "3001234567", "acepta_tyc": "on"}
    )
    assert r1.status_code == 400
    assert 'name="nombre"' in r1.text.lower()

    # 2do intento: destildó Términos sin haber escrito su nombre todavía --
    # el navegador reenvía el hidden `mostrar_nombre` que el 1er render ya
    # había agregado.
    r2 = client.post(
        "/anunciar", data={"telefono": "3001234567", "mostrar_nombre": "1"}
    )
    assert r2.status_code == 400
    assert 'name="nombre"' in r2.text.lower()
    assert _cuenta_paquetes(client) == 0
