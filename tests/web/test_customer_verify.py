# -*- coding: utf-8 -*-
"""
Capa web — `/mis-datos` (autoedición del cliente, ticket único).

Comportamiento observable por HTTP: exige sesión de cliente; guarda datos
personales de forma PARCIAL; email inválido rechaza TODO el request
(rollback); el snapshot de paquetes ya anunciados no cambia.

Torre/Apartamento/Conjunto (pedido del cliente, `.scratch/pendientes-cliente`,
ajuste posterior a `apartamento-catalogo-confirmacion`): son de SOLO LECTURA
acá, sin excepción -- ni siquiera para declarar por primera vez. El servidor
ignora por completo cualquier `torre`/`apartamento`/`conjunto` que venga en el
POST. La asignación es exclusiva del personal de Papyrus desde
`/residentes/{id}` (ver `test_customers_manage.py`). `documento`/
`tipo_documento` tampoco se aceptan en este formulario (Grupo 12, Ronda 2).
"""

from app.domain.apartamento import Apartamento
from app.domain.apartamento_service import declare_unit, resolver_apartamento
from app.domain.ocupante import Ocupante
from app.domain.ocupante_service import MAX_OCUPANTES_ACTIVOS, agregar_ocupante
from app.domain.otp_sender import DevOtpSender
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.domain.persona_service import update_datos_personales
from app.domain.telefono import normalizar_telefono
from app.domain.usuario import RolUsuario, Usuario
from app.web.otp import get_otp_sender

_CANON = "+573001234567"


def _confirmar_principal(client, apto):
    """Confirma (por staff) al único Ocupante activo de `apto` -- lo
    promueve a principal en el mismo acto (ticket 06). Fixture de
    conveniencia para tests que no son SOBRE el flujo de confirmación en sí,
    pero necesitan un principal ya establecido (p.ej. para gestionar otros
    Ocupantes, que exige `es_principal`).

    Idempotente (`.scratch/ocupante-principal-escenarios`, ticket 04): desde
    que `receive()` puede promover automáticamente, el paquete de
    elegibilidad que siembra `_login_cliente` (mismo teléfono, misma unidad)
    a menudo ya deja al Ocupante confirmado y principal antes de que este
    helper se llame -- no hay nada que corregir en ese caso."""
    ocupante = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.desvinculado_en.is_(None)
    ).one()
    if ocupante.confirmado_en is not None:
        return

    from app.domain.ocupante_service import confirmar_ocupante
    from app.domain.staff_service import create_initial_admin

    admin = client.db.query(Usuario).filter(Usuario.rol == RolUsuario.ADMIN).first()
    if admin is None:
        admin = create_initial_admin(client.db, "admin@club.com", "Admin", "Contrasena1")
    confirmar_ocupante(client.db, ocupante, admin)
    client.db.commit()


def _login_cliente(client, telefono="3001234567"):
    # Corrección en vivo 2026-08-02: pedir OTP ahora exige que el teléfono
    # sea elegible (tenga un Paquete Recibido) -- se siembra uno antes de
    # pedir el código (cada test arranca con BD limpia, no hace falta
    # revisar si ya existe). Ya no necesita ser el propio anunciante (ticket
    # 05, .scratch/mis-datos): si esta Persona ya existe (p.ej. un Ocupante
    # creado por el principal), reusa su elegibilidad si ya la tiene.
    canon = normalizar_telefono(telefono)
    ya_elegible = (
        client.db.query(Paquete)
        .filter(
            Paquete.estado == EstadoPaquete.RECIBIDO,
            (Paquete.announced_by_phone == canon) | (Paquete.recipient_phone == canon),
        )
        .first()
        is not None
    )
    if not ya_elegible:
        staff = Usuario(nombre="ActorElegibilidad", rol=RolUsuario.OPERADOR)
        client.db.add(staff)
        client.db.flush()
        p = announce(
            client.db,
            anunciante_telefono=telefono,
            anunciante_nombre="Cliente de prueba",
            destinatario=Destinatario.yo_mismo(),
        )
        receive(client.db, p, staff)
        client.db.commit()

    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    client.post("/otp/solicitar", data={"telefono": telefono})
    codigo = sender.enviados[canon]
    client.post(
        "/otp/verificar", data={"telefono": telefono, "codigo": codigo}
    )
    return client.db.query(Persona).filter(Persona.telefono == canon).one()


def test_sin_sesion_redirige_a_login_de_cliente(client):
    r = client.get("/mis-datos", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/otp")


def test_con_sesion_sin_apartamento_muestra_estado_vacio_de_solo_lectura(client):
    # Torre/Apartamento son de solo lectura para el residente (pedido del
    # cliente, .scratch/pendientes-cliente) -- sin selects, sin picker, sin
    # excepción para quien todavía no tiene unidad asignada.
    _login_cliente(client)
    r = client.get("/mis-datos")
    assert r.status_code == 200
    assert 'name="nombre"' in r.text
    assert 'name="torre"' not in r.text and 'name="apartamento"' not in r.text
    assert "Aún no tienes un apartamento asignado por el personal de Papyrus." in r.text


def test_mis_datos_query_param_tab_abre_directo_en_esa_tab(client):
    # Issue 267 (.scratch/pendientes-cliente, pedido explícito del
    # cliente): refrescar (F5) debe mantener la tab activa -- el JS
    # sincroniza `?tab=` en cada click (`history.replaceState`), y el
    # server ahora lo respeta al renderizar, mismo criterio que ya usa
    # `/residentes` (conversación 2026-08-17).
    _login_cliente(client)
    r = client.get("/mis-datos", params={"tab": "notif"})
    assert r.status_code == 200
    assert "activar('notif')" in r.text


def test_mis_datos_tab_desconocida_cae_al_default(client):
    _login_cliente(client)
    r = client.get("/mis-datos", params={"tab": "no-existe"})
    assert r.status_code == 200
    assert "activar('datos')" in r.text


def test_mis_datos_ocupante_guardado_gana_sobre_query_param_tab(client):
    # `ocupante_guardado=1` sigue ganando sobre `?tab=` -- mismo orden de
    # prioridad que ya tenía esta vista antes de issue 267.
    _login_cliente(client)
    r = client.get("/mis-datos", params={"tab": "notif", "ocupante_guardado": "1"})
    assert r.status_code == 200
    assert "activar('ocup')" in r.text


def test_autoriza_recepcion_automatica_desactivado_por_default(client):
    _login_cliente(client)
    r = client.get("/mis-datos")
    assert 'name="autoriza_recepcion_automatica"' in r.text
    assert "checked" not in r.text.split('name="autoriza_recepcion_automatica"')[1][:50]


def test_marcar_autoriza_recepcion_automatica(client):
    persona = _login_cliente(client)
    client.post(
        "/mis-datos",
        data={"nombre": "Ana", "autoriza_recepcion_automatica": "on"},
    )

    client.db.expire_all()
    assert client.db.get(Persona, persona.id).autoriza_recepcion_automatica is True


def test_desmarcar_autoriza_recepcion_automatica(client):
    persona = _login_cliente(client)
    client.post(
        "/mis-datos", data={"nombre": "Ana", "autoriza_recepcion_automatica": "on"}
    )
    client.post("/mis-datos", data={"nombre": "Ana"})  # sin el checkbox -> se apaga

    client.db.expire_all()
    assert client.db.get(Persona, persona.id).autoriza_recepcion_automatica is False


def test_con_apartamento_asignado_se_muestra_de_solo_lectura(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    r = client.get("/mis-datos")
    assert r.status_code == 200
    # Sin selects -- de solo lectura, sin excepción (ni para el principal).
    assert 'name="torre"' not in r.text and 'name="apartamento"' not in r.text
    assert "TORRE 1" in r.text
    assert "EL CLUB" in r.text  # Conjunto, siempre visible


def test_guardar_datos_personales_es_parcial(client):
    persona = _login_cliente(client)

    r = client.post(
        "/mis-datos",
        data={"nombre": "Ana", "email": "ana@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.nombre == "ANA" and p.email == "ana@example.com"

    # Segunda vez: solo email, sin nombre -> nombre NO se pierde (parcial).
    client.post("/mis-datos", data={"email": "otro@example.com"})
    client.db.expire_all()
    p2 = client.db.get(Persona, persona.id)
    assert p2.nombre == "ANA" and p2.email == "otro@example.com"


def test_email_vacio_en_mis_datos_lo_borra(client):
    # Issue 261 (.scratch/pendientes-cliente): mismo contrato de 3 estados
    # que ya tiene WhatsApp (issue 69) -- dejar Email vacío y guardar lo
    # borra, en vez de dejarlo intacto.
    persona = _login_cliente(client)
    client.post("/mis-datos", data={"nombre": "Ana", "email": "ana@example.com"})
    client.db.expire_all()
    assert client.db.get(Persona, persona.id).email == "ana@example.com"

    client.post("/mis-datos", data={"email": ""})
    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.email is None
    assert p.nombre == "ANA"  # sigue intacto -- nombre no se pasó vacío


def test_documento_ya_no_se_acepta_en_este_formulario(client):
    persona = _login_cliente(client)
    client.post("/mis-datos", data={"documento": "123", "tipo_documento": "CC"})
    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.documento is None and p.tipo_documento is None


def test_enviar_torre_apartamento_o_conjunto_no_tiene_ningun_efecto(client):
    # Torre/Apartamento/Conjunto son de solo lectura para el residente
    # (pedido del cliente, .scratch/pendientes-cliente) -- el servidor ni
    # siquiera lee esos campos del form, aunque alguien arme el POST a mano
    # con una terna real del catálogo.
    persona = _login_cliente(client)
    r = client.post(
        "/mis-datos",
        data={"conjunto": "Cualquiera", "torre": "TORRE 1", "apartamento": "101"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.apartamento_actual_id is None  # el servidor ignoró los 3 campos


def test_con_apartamento_ya_asignado_el_cliente_no_puede_cambiarlo(client):
    apto_inicial = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto_inicial, [("3001234567", "Ana")])
    client.db.commit()

    persona = _login_cliente(client)
    r = client.post(
        "/mis-datos",
        data={"torre": "TORRE 2", "apartamento": "202"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    apto = client.db.get(Apartamento, p.apartamento_actual_id)
    # Sin cambios -- el intento del cliente se ignoró en el servidor.
    assert (apto.torre, apto.apartamento) == ("TORRE 1", "101")


def test_email_invalido_rechaza_todo_el_request_sin_persistir_nada(client):
    persona = _login_cliente(client)
    client.post("/mis-datos", data={"nombre": "Ana"})  # estado base conocido

    r = client.post(
        "/mis-datos", data={"nombre": "Otro Nombre", "email": "no-es-un-email"}
    )
    assert r.status_code == 400

    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.nombre == "ANA"  # el cambio de ESTE request no se aplicó (todo o nada)


# --------------------------------------------------------------------------- #
# Foco condicional (versión móvil, `.scratch/pendientes-cliente`): autofocus
# SOLO en una carga limpia -- con error, activarlo dispara el teclado y tapa
# el mensaje de error en mobile.
# --------------------------------------------------------------------------- #
def test_get_mis_datos_limpio_tiene_autofocus(client):
    _login_cliente(client)
    r = client.get("/mis-datos")
    assert "autofocus" in r.text


def test_post_mis_datos_con_error_no_tiene_autofocus(client):
    _login_cliente(client)
    r = client.post("/mis-datos", data={"nombre": "Ana", "email": "no-es-un-email"})
    assert r.status_code == 400
    assert "autofocus" not in r.text


def test_principal_ve_la_tarjeta_mis_ocupantes(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    r = client.get("/mis-datos")
    assert "Mis Residentes" in r.text


def test_sin_apartamento_no_ve_la_tarjeta_mis_ocupantes(client):
    _login_cliente(client)
    r = client.get("/mis-datos")
    assert "Mis Residentes" not in r.text


def test_principal_crea_ocupante_sin_telefono(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    r = client.post(
        "/mis-datos/ocupantes", data={"nombre": "Hijo"}, follow_redirects=False
    )
    assert r.status_code == 303

    client.db.expire_all()
    ocupantes = client.db.query(Ocupante).filter(Ocupante.apartamento_id == apto.id).all()
    nombres = {o.nombre for o in ocupantes}
    assert "HIJO" in nombres


def test_principal_crea_ocupante_con_telefono(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    r = client.post(
        "/mis-datos/ocupantes",
        data={"nombre": "Hija", "contacto": "3021112233"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    hija = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.nombre == "HIJA"
    ).one()
    assert hija.persona_id is not None


def test_crear_ocupante_sin_nombre_falla(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    r = client.post("/mis-datos/ocupantes", data={})
    assert r.status_code == 400


def test_crear_ocupante_respeta_limite(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    for i in range(MAX_OCUPANTES_ACTIVOS - 1):  # +1 principal ya existente = MAX_OCUPANTES_ACTIVOS
        agregar_ocupante(client.db, apto, f"Extra{i}")
    client.db.commit()

    r = client.post("/mis-datos/ocupantes", data={"nombre": "DeMas"})
    assert r.status_code == 400


def test_principal_asocia_telefono_a_ocupante_existente(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    # Ana ya es principal (arriba) -- "Hijo" es el SEGUNDO Ocupante, sin
    # teléfono, sin volverse principal.
    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.post(
        f"/mis-datos/ocupantes/{hijo.id}/telefono",
        data={"telefono": "3021112233"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Ocupante, hijo.id).persona_id is not None


def test_principal_desvincula_telefono_de_ocupante_no_principal(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    hija = agregar_ocupante(client.db, apto, "Hija", telefono="3021112233")
    client.db.commit()

    r = client.post(
        f"/mis-datos/ocupantes/{hija.id}/desvincular-telefono", follow_redirects=False
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Ocupante, hija.id).persona_id is None


def test_principal_crea_ocupante_con_whatsapp(client):
    """.scratch/ocupante-principal-escenarios, ticket 07 -- input único
    autoclasificado en "agregar Residente"."""
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    r = client.post(
        "/mis-datos/ocupantes",
        data={"nombre": "Hija", "contacto": "hija.whats"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    hija = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.nombre == "HIJA"
    ).one()
    assert hija.persona_id is not None
    assert client.db.get(Persona, hija.persona_id).whatsapp_usuario == "hija.whats"


def test_principal_asocia_whatsapp_a_ocupante_existente(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.post(
        f"/mis-datos/ocupantes/{hijo.id}/contacto",
        data={"contacto": "hijo.whats"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    ocupante = client.db.get(Ocupante, hijo.id)
    assert ocupante.persona_id is not None
    assert client.db.get(Persona, ocupante.persona_id).whatsapp_usuario == "hijo.whats"


def test_principal_edita_whatsapp_de_ocupante_existente(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    hija = agregar_ocupante(client.db, apto, "Hija", whatsapp_usuario="hija.vieja")
    client.db.commit()

    r = client.post(
        f"/mis-datos/ocupantes/{hija.id}/whatsapp",
        data={"whatsapp_usuario": "hija.nueva"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    ocupante = client.db.get(Ocupante, hija.id)
    assert client.db.get(Persona, ocupante.persona_id).whatsapp_usuario == "hija.nueva"


def test_principal_desvincula_whatsapp_de_ocupante_no_principal(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    hija = agregar_ocupante(client.db, apto, "Hija", whatsapp_usuario="hija.whats")
    client.db.commit()

    r = client.post(
        f"/mis-datos/ocupantes/{hija.id}/desvincular-whatsapp", follow_redirects=False
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Ocupante, hija.id).persona_id is None


def test_principal_da_de_baja_a_ocupante_no_principal(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.post(f"/mis-datos/ocupantes/{hijo.id}/baja", follow_redirects=False)
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Ocupante, hijo.id).desvinculado_en is not None


def test_desvincular_telefono_del_principal_por_ruta_falla(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    mi_ocupante = client.db.query(Ocupante).filter(Ocupante.apartamento_id == apto.id).one()
    r = client.post(f"/mis-datos/ocupantes/{mi_ocupante.id}/desvincular-telefono")
    assert r.status_code == 400


def test_principal_promueve_a_ocupante_con_telefono(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    hija = agregar_ocupante(client.db, apto, "Hija", telefono="3021112233")
    client.db.commit()

    r = client.post(f"/mis-datos/ocupantes/{hija.id}/promover", follow_redirects=False)
    assert r.status_code == 303

    client.db.expire_all()
    ana = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.nombre == "ANA"
    ).one()
    assert client.db.get(Ocupante, hija.id).es_principal is True
    assert ana.es_principal is False


def test_promover_a_ocupante_sin_telefono_falla(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.post(f"/mis-datos/ocupantes/{hijo.id}/promover")
    assert r.status_code == 400


def test_ocupante_no_principal_ve_roster_de_solo_lectura(client):
    from app.domain.ocupante_service import asociar_telefono_a_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)  # Ana, principal
    _confirmar_principal(client, apto)

    hija = agregar_ocupante(client.db, apto, "Hija")
    asociar_telefono_a_ocupante(client.db, hija, "3021112233")
    client.db.commit()

    r = _login_cliente(client, telefono="3021112233")  # ahora entra Hija
    assert r.telefono == "+573021112233"

    pagina = client.get("/mis-datos")
    assert "Quien más viven acá" in pagina.text
    assert "Mis Residentes" not in pagina.text  # gestión es solo del residente principal
    assert "ANA" in pagina.text
    assert "+573001234567" in pagina.text  # ve todo -- incluye teléfonos ajenos


def test_ocupante_no_principal_no_puede_cambiar_torre_apartamento(client):
    from app.domain.ocupante_service import asociar_telefono_a_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    hija = agregar_ocupante(client.db, apto, "Hija")
    asociar_telefono_a_ocupante(client.db, hija, "3021112233")
    client.db.commit()

    _login_cliente(client, telefono="3021112233")
    client.post(
        "/mis-datos", data={"nombre": "Hija Editada", "torre": "Z", "apartamento": "999"}
    )

    client.db.expire_all()
    # El apartamento del ROSTER no cambió -- el intento se ignoró en el servidor.
    assert client.db.query(Apartamento).filter(
        Apartamento.torre == "Z", Apartamento.apartamento == "999"
    ).first() is None
    hija_persona = client.db.query(Persona).filter(Persona.telefono == "+573021112233").one()
    assert hija_persona.nombre == "HIJA EDITADA"  # esto sí se guarda -- es lo suyo


def test_ocupante_no_principal_se_autodescarta(client):
    from app.domain.ocupante_service import asociar_telefono_a_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()
    # Ana confirmada como principal ANTES de que Hija reciba su paquete de
    # elegibilidad -- si no, la promoción automática (ticket 04) dejaría a
    # Hija como principal (unidad sin nadie confirmado todavía), y este test
    # dejaría de probar el caso "no principal" que le da nombre.
    _confirmar_principal(client, apto)

    hija = agregar_ocupante(client.db, apto, "Hija")
    asociar_telefono_a_ocupante(client.db, hija, "3021112233")
    client.db.commit()

    _login_cliente(client, telefono="3021112233")
    r = client.post("/mis-datos/ocupantes/salir", follow_redirects=False)
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Ocupante, hija.id).desvinculado_en is not None


def test_gestionar_ocupante_de_otro_apartamento_da_403(client):
    apto_propio = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto_propio, "Ana", "3001234567")
    apto_ajeno = resolver_apartamento(client.db, "TORRE 2", "202")
    ocupante_ajeno = agregar_ocupante(client.db, apto_ajeno, "Beto", telefono="3019999999")
    client.db.commit()

    _login_cliente(client)

    r = client.post(f"/mis-datos/ocupantes/{ocupante_ajeno.id}/baja")
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# `.scratch/pendientes-cliente/issues/35` — teléfono editable.
# --------------------------------------------------------------------------- #
def test_principal_edita_su_propio_telefono_cierra_sesion(client):
    persona = _login_cliente(client)

    r = client.post(
        "/mis-datos", data={"telefono": "3009998877"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/otp")

    client.db.expire_all()
    assert client.db.get(Persona, persona.id).telefono == "+573009998877"

    # La sesión de cliente quedó cerrada -- /mis-datos vuelve a redirigir.
    r2 = client.get("/mis-datos", follow_redirects=False)
    assert r2.status_code == 303


def test_principal_reenviar_su_mismo_telefono_no_cierra_sesion(client):
    _login_cliente(client)
    r = client.post(
        "/mis-datos", data={"telefono": "3001234567"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/mis-datos?guardado=1"


def test_principal_desvincula_su_propio_telefono_con_whatsapp_de_respaldo(client):
    """.scratch/ocupante-principal-escenarios, ticket 14 -- con WhatsApp ya
    asociado como respaldo (acá lo pone el staff directo en la Persona,
    único camino existente hoy), el principal puede quitarse su propio
    Teléfono con confirmación explícita; la sesión se cierra de inmediato."""
    persona = _login_cliente(client)
    persona.whatsapp_usuario = "ana_respaldo"
    client.db.commit()

    r = client.post(
        "/mis-datos/desvincular-telefono", data={"confirmar": "1"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/otp")

    client.db.expire_all()
    assert client.db.get(Persona, persona.id).telefono is None

    # La sesión de cliente quedó cerrada -- /mis-datos vuelve a redirigir.
    r2 = client.get("/mis-datos", follow_redirects=False)
    assert r2.status_code == 303


def test_desvincular_telefono_propio_sin_whatsapp_de_respaldo_falla(client):
    persona = _login_cliente(client)

    r = client.post("/mis-datos/desvincular-telefono", data={"confirmar": "1"})
    assert r.status_code == 400
    assert "WhatsApp" in r.text

    client.db.expire_all()
    assert client.db.get(Persona, persona.id).telefono is not None


def test_desvincular_telefono_propio_sin_confirmar_falla(client):
    persona = _login_cliente(client)
    persona.whatsapp_usuario = "ana_respaldo"
    client.db.commit()

    r = client.post("/mis-datos/desvincular-telefono", data={})
    assert r.status_code == 400
    assert "Confirma" in r.text

    client.db.expire_all()
    assert client.db.get(Persona, persona.id).telefono is not None


def test_principal_edita_telefono_a_uno_en_uso_falla(client):
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3019999999", "Otro")
    client.db.commit()

    _login_cliente(client)
    r = client.post("/mis-datos", data={"telefono": "3019999999"})
    assert r.status_code == 400
    assert "ya está en uso" in r.text


def test_principal_edita_telefono_de_un_ocupante_ya_asociado(client):
    from app.domain.ocupante_service import asociar_telefono_a_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    hija = agregar_ocupante(client.db, apto, "Hija")
    asociar_telefono_a_ocupante(client.db, hija, "3021112233")
    client.db.commit()

    r = client.post(
        f"/mis-datos/ocupantes/{hija.id}/telefono",
        data={"telefono": "3029998877"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    persona_hija = client.db.get(Persona, client.db.get(Ocupante, hija.id).persona_id)
    assert persona_hija.telefono == "+573029998877"


# --------------------------------------------------------------------------- #
# `/editar` unificado (issue 228, .scratch/pendientes-cliente) -- Nombre,
# Email, Teléfono y WhatsApp de un Ocupante en un solo submit. Sin
# cobertura directa hasta la revisión de código de issue 233 -- solo se
# había probado a mano por curl.
# --------------------------------------------------------------------------- #
def test_editar_ocupante_unificado_actualiza_todo_sin_perder_el_otro_canal(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    hijo = agregar_ocupante(
        client.db, apto, "Hijo", telefono="3021112233", whatsapp_usuario="hijo.whats"
    )
    client.db.commit()
    persona_id_antes = hijo.persona_id

    r = client.post(
        f"/mis-datos/ocupantes/{hijo.id}/editar",
        data={
            "nombre": "Hijo Editado",
            "email": "hijo@example.com",
            "telefono": "3029998877",
            "whatsapp_usuario": "hijo.nuevo",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    ocupante = client.db.get(Ocupante, hijo.id)
    assert ocupante.persona_id == persona_id_antes  # canal doble -- no se re-ligó
    assert ocupante.nombre == "HIJO EDITADO"
    persona = client.db.get(Persona, ocupante.persona_id)
    assert persona.email == "hijo@example.com"
    assert persona.telefono == "+573029998877"
    assert persona.whatsapp_usuario == "hijo.nuevo"


def test_editar_ocupante_unificado_email_vacio_lo_borra(client):
    # Issue 261 (.scratch/pendientes-cliente): mismo contrato de 3 estados
    # que ya tiene WhatsApp (issue 69) -- dejar Email vacío en este modal y
    # guardar lo borra, en vez de dejarlo intacto.
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    hijo = agregar_ocupante(client.db, apto, "Hijo", telefono="3021112233")
    client.db.commit()
    update_datos_personales(client.db, client.db.get(Persona, hijo.persona_id), email="hijo@example.com")
    client.db.commit()

    r = client.post(
        f"/mis-datos/ocupantes/{hijo.id}/editar",
        data={"email": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    ocupante = client.db.get(Ocupante, hijo.id)
    persona = client.db.get(Persona, ocupante.persona_id)
    assert persona.email is None
    assert persona.telefono == "+573021112233"  # sigue intacto


def test_editar_ocupante_sin_contacto_propio_falla(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    sin_contacto = agregar_ocupante(client.db, apto, "Sin Contacto")
    client.db.commit()

    r = client.post(
        f"/mis-datos/ocupantes/{sin_contacto.id}/editar",
        data={"nombre": "Nuevo Nombre"},
    )
    assert r.status_code == 400
    assert "todavía no tiene contacto propio" in r.text


def test_editar_ocupante_agrega_whatsapp_faltante_sin_perder_telefono(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    hijo = agregar_ocupante(client.db, apto, "Hijo", telefono="3021112233")  # solo Teléfono
    client.db.commit()

    r = client.post(
        f"/mis-datos/ocupantes/{hijo.id}/editar",
        data={"whatsapp_usuario": "hijo.nuevo"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    persona = client.db.get(Persona, client.db.get(Ocupante, hijo.id).persona_id)
    assert persona.whatsapp_usuario == "hijo.nuevo"
    assert persona.telefono == "+573021112233"  # sigue intacto


def test_editar_ocupante_choca_con_persona_huerfana_canal_doble_falla(client):
    # Issue 233 (.scratch/pendientes-cliente) -- mismo bug de la revisión de
    # código, ejercido a nivel HTTP: re-ligar a una Persona huérfana que ya
    # tiene su propio WhatsApp debe fallar, no sobreescribirlo en silencio.
    from app.domain.ocupante_service import dar_de_baja_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    viejo = agregar_ocupante(
        client.db, apto, "Viejo", telefono="3009990000", whatsapp_usuario="viejo.whats"
    )
    dar_de_baja_ocupante(client.db, viejo)
    hijo = agregar_ocupante(client.db, apto, "Hijo", telefono="3021112233")  # canal único
    client.db.commit()

    r = client.post(
        f"/mis-datos/ocupantes/{hijo.id}/editar",
        data={"telefono": "3009990000"},
    )
    assert r.status_code == 400


def test_cambiar_de_apartamento_no_reescribe_snapshot_de_paquete_ya_anunciado(client):
    # Invariante de dominio (ADR-0001): mover a alguien de unidad NUNCA
    # reescribe el snapshot de un Paquete ya anunciado. Antes se probaba vía
    # el autoservicio del cliente; ahora que mover es exclusivo del staff
    # (.scratch/pendientes-cliente), se ejerce `move_resident` directo como
    # herramienta de dominio para simular el cambio de dirección -- la ruta
    # de staff hoy pasa por `ocupante_service.reasignar_apartamento`
    # (`.scratch/announce-residente-correcto` ticket 01), pero el invariante
    # que este test cubre (el snapshot nunca se reescribe) es el mismo sin
    # importar qué mecanismo mueve a la Persona.
    from app.domain.apartamento_service import move_resident

    apto_inicial = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto_inicial, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)

    paquete = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    paquete_id = paquete.id

    apto_nuevo = resolver_apartamento(client.db, "TORRE 2", "202")
    move_resident(client.db, "3001234567", apto_nuevo)
    client.db.commit()

    client.db.expire_all()
    p = client.db.get(Paquete, paquete_id)
    assert (p.snapshot_conjunto, p.snapshot_torre, p.snapshot_apartamento) == (
        "EL CLUB",
        "TORRE 1",
        "101",
    )


# --------------------------------------------------------------------------- #
# Preferencia de notificaciones (ticket 02 de notification-preferences)
# --------------------------------------------------------------------------- #
def test_get_muestra_la_matriz_con_sms_activo_por_default(client):
    _login_cliente(client)
    r = client.get("/mis-datos")
    assert r.status_code == 200
    assert 'name="pref_SMS_ANUNCIADO"' in r.text
    # SMS por default viene marcado (checked) para los 4 eventos.
    idx = r.text.index('name="pref_SMS_ANUNCIADO"')
    assert "checked" in r.text[idx : idx + 60]


def test_marcar_un_canal_lo_activa_para_ese_evento(client):
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    persona = _login_cliente(client)
    client.post("/mis-datos", data={"pref_EMAIL_RECIBIDO": "on"})
    client.db.expire_all()

    assert preferencia_activa(
        client.db, persona.id, CanalNotificacion.EMAIL, EstadoPaquete.RECIBIDO
    ) is True


def test_llamada_no_se_puede_activar(client):
    # `.scratch/pendientes-cliente/issues/36` -- sin proveedor conectado, el
    # servidor ignora este canal aunque alguien fuerce el POST crudo.
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    persona = _login_cliente(client)
    client.post("/mis-datos", data={"pref_LLAMADA_RECIBIDO": "on"})
    client.db.expire_all()

    assert preferencia_activa(
        client.db, persona.id, CanalNotificacion.LLAMADA, EstadoPaquete.RECIBIDO
    ) is False


def test_whatsapp_si_se_puede_activar(client):
    # Issue 221 (.scratch/pendientes-cliente): columna WhatsApp activada en
    # /mis-datos -- a diferencia de Llamada (arriba), esta SÍ se guarda.
    # Default ya es `True` (issue 221bis) -- se prueba apagando primero,
    # para confirmar que el POST realmente escribe el canal.
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    persona = _login_cliente(client)
    client.post("/mis-datos", data={})  # sin marcar -- apaga el default
    client.db.expire_all()
    assert preferencia_activa(
        client.db, persona.id, CanalNotificacion.WHATSAPP, EstadoPaquete.RECIBIDO
    ) is False

    client.post("/mis-datos", data={"pref_WHATSAPP_RECIBIDO": "on"})
    client.db.expire_all()
    assert preferencia_activa(
        client.db, persona.id, CanalNotificacion.WHATSAPP, EstadoPaquete.RECIBIDO
    ) is True


def test_sms_no_se_puede_activar_fuera_de_anunciado(client):
    # 2026-08-26 (pedido del cliente): SMS solo editable en ANUNCIADO para
    # un Residente -- el servidor ignora el resto aunque alguien fuerce el
    # POST crudo (la plantilla ya los muestra deshabilitados).
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    persona = _login_cliente(client)
    client.post(
        "/mis-datos",
        data={
            "pref_SMS_RECIBIDO": "on",
            "pref_SMS_ENTREGADO": "on",
            "pref_SMS_CANCELADO": "on",
        },
    )
    client.db.expire_all()

    for evento in (EstadoPaquete.RECIBIDO, EstadoPaquete.ENTREGADO, EstadoPaquete.CANCELADO):
        assert preferencia_activa(client.db, persona.id, CanalNotificacion.SMS, evento) is False


def test_no_marcar_sms_lo_desactiva_para_ese_evento(client):
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    persona = _login_cliente(client)
    # Ningún pref_* en el POST: toda la matriz queda desmarcada (mismo
    # comportamiento que cualquier checkbox HTML ausente).
    client.post("/mis-datos", data={})
    client.db.expire_all()

    assert preferencia_activa(
        client.db, persona.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO
    ) is False


def test_reenviar_la_matriz_marcada_restaura_la_preferencia(client):
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    persona = _login_cliente(client)
    client.post("/mis-datos", data={})  # desactiva todo
    client.post("/mis-datos", data={"pref_SMS_ANUNCIADO": "on"})  # reactiva solo este

    client.db.expire_all()
    assert preferencia_activa(
        client.db, persona.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO
    ) is True


def test_residente_no_pisa_sms_que_admin_ya_activo(client):
    # Un ADMIN activó SMS×Recibido a propósito (vía /residentes/{id}); el
    # propio Residente guardando /mis-datos (sin ver ese checkbox) NO debe
    # resetearlo a `False` por simple omisión.
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import (
        guardar_preferencia,
        preferencia_activa,
    )

    persona = _login_cliente(client)
    guardar_preferencia(
        client.db, persona.id, CanalNotificacion.SMS, EstadoPaquete.RECIBIDO, True
    )
    client.db.commit()

    client.post("/mis-datos", data={"pref_EMAIL_RECIBIDO": "on"})

    client.db.expire_all()
    assert preferencia_activa(
        client.db, persona.id, CanalNotificacion.SMS, EstadoPaquete.RECIBIDO
    ) is True


def test_matriz_vacia_no_rompe_el_resto_del_guardado(client):
    persona = _login_cliente(client)
    client.post("/mis-datos", data={"nombre": "Ana Actualizada"})
    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.nombre == "ANA ACTUALIZADA"  # el resto del formulario se guardó igual


# --------------------------------------------------------------------------- #
# `/ocupantes/{id}/notificaciones` (issue 226, .scratch/pendientes-cliente)
# -- misma matriz Canal × Evento que `/mis-datos`, apuntada a otro Ocupante.
# --------------------------------------------------------------------------- #
def test_editar_notificaciones_de_ocupante_activa_un_canal(client):
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    hijo = agregar_ocupante(client.db, apto, "Hijo", telefono="3021112233")
    client.db.commit()

    client.post(
        f"/mis-datos/ocupantes/{hijo.id}/notificaciones",
        data={"pref_EMAIL_RECIBIDO": "on"},
    )
    client.db.expire_all()

    assert preferencia_activa(
        client.db, hijo.persona_id, CanalNotificacion.EMAIL, EstadoPaquete.RECIBIDO
    ) is True


def test_editar_notificaciones_de_ocupante_sin_contacto_propio_falla(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    sin_contacto = agregar_ocupante(client.db, apto, "Sin Contacto")
    client.db.commit()

    r = client.post(
        f"/mis-datos/ocupantes/{sin_contacto.id}/notificaciones",
        data={"pref_EMAIL_RECIBIDO": "on"},
    )
    assert r.status_code == 400
    assert "todavía no tiene contacto propio" in r.text


def test_editar_notificaciones_de_ocupante_no_afecta_al_principal(client):
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    persona = _login_cliente(client)
    _confirmar_principal(client, apto)

    hijo = agregar_ocupante(client.db, apto, "Hijo", telefono="3021112233")
    client.db.commit()

    client.post(
        f"/mis-datos/ocupantes/{hijo.id}/notificaciones",
        data={"pref_EMAIL_RECIBIDO": "on"},
    )
    client.db.expire_all()

    assert preferencia_activa(
        client.db, persona.id, CanalNotificacion.EMAIL, EstadoPaquete.RECIBIDO
    ) is False


def test_desactivar_detiene_una_notificacion_posterior(client):
    from app.domain.staff_service import create_initial_admin
    from app.domain.paquete_lifecycle import receive
    from app.web.notifications import get_notification_sender

    _login_cliente(client)  # Ana, +573001234567, activa por defecto

    class _SenderEspia:
        def __init__(self):
            self.enviados = []

        def enviar(self, destino, mensaje):
            self.enviados.append((destino, mensaje))

    espia = _SenderEspia()
    client.app.dependency_overrides[get_notification_sender] = lambda: espia

    # Desactivar SIN cerrar la sesión de cliente (coexiste con la de staff).
    client.post("/mis-datos", data={})

    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    admin = create_initial_admin(client.db, "admin@club.com", "Admin", "Contrasena1")
    client.db.commit()
    client.post("/ingresar", data={"email": "admin@club.com", "password": "Contrasena1"})

    client.post(f"/paquetes/{p.id}/recibir", data={})

    assert espia.enviados == []


# --------------------------------------------------------------------------- #
# Ticket 08 (.scratch/apartamento-catalogo-confirmacion) — el principal
# confirma/rechaza Ocupantes pendientes de su propia unidad.
# --------------------------------------------------------------------------- #
def test_principal_confirma_un_pending_de_su_unidad(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    hijo = agregar_ocupante(client.db, apto, "Hijo")  # pending
    client.db.commit()

    r = client.post(
        f"/mis-datos/ocupantes/{hijo.id}/confirmar", follow_redirects=False
    )
    assert r.status_code == 303

    client.db.expire_all()
    confirmado = client.db.get(Ocupante, hijo.id)
    assert confirmado.confirmado_en is not None
    assert confirmado.es_principal is False  # no le tocó el rol al principal


def test_principal_rechaza_un_pending_de_su_unidad(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto)

    hijo = agregar_ocupante(client.db, apto, "Hijo")  # pending
    client.db.commit()

    r = client.post(f"/mis-datos/ocupantes/{hijo.id}/baja", follow_redirects=False)
    assert r.status_code == 303

    client.db.expire_all()
    rechazado = client.db.get(Ocupante, hijo.id)
    assert rechazado.desvinculado_en is not None
    assert rechazado.confirmado_en is None  # nunca llegó a confirmarse


def test_principal_no_puede_confirmar_ocupante_de_otro_apartamento(client):
    apto_propio = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto_propio, "Ana", "3001234567")
    apto_ajeno = resolver_apartamento(client.db, "TORRE 2", "202")
    ocupante_ajeno = agregar_ocupante(client.db, apto_ajeno, "Beto", telefono="3019999999")
    client.db.commit()

    _login_cliente(client)
    _confirmar_principal(client, apto_propio)

    r = client.post(f"/mis-datos/ocupantes/{ocupante_ajeno.id}/confirmar")
    assert r.status_code == 403
