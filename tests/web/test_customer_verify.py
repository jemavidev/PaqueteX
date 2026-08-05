# -*- coding: utf-8 -*-
"""
Capa web — `/mis-datos` (autoedición del cliente, ticket único).

Comportamiento observable por HTTP: exige sesión de cliente; guarda datos
personales de forma PARCIAL; email inválido o Torre/Apartamento incompleto
rechazan TODO el request (rollback); el snapshot de paquetes ya anunciados no
cambia.

Grupo 12 (Ronda 2, `ajustes-post-referencia-funcional/REQUERIMIENTOS.md`): el
cliente ya NO puede fijar ni cambiar el Conjunto — solo el staff lo asigna
(vía `/announce`). Mientras no tenga Conjunto asignado, el cliente no puede
declarar Torre/Apartamento tampoco (no tiene sentido sin saber en cuál
Conjunto). Una vez el staff le asigna uno, el cliente puede actualizar Torre y
Apartamento libremente dentro de ESE Conjunto. `documento`/`tipo_documento`
tampoco se aceptan ya en este formulario (mismo grupo).
"""

from app.domain.apartamento import Apartamento
from app.domain.apartamento_service import declare_unit, resolver_apartamento
from app.domain.ocupante import Ocupante
from app.domain.otp_sender import DevOtpSender
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.domain.telefono import normalizar_telefono
from app.domain.usuario import RolUsuario, Usuario
from app.web.otp import get_otp_sender

_CANON = "+573001234567"


def _confirmar_principal(client, apto):
    """Confirma (por staff) al único Ocupante activo de `apto` -- lo
    promueve a principal en el mismo acto (ticket 06). Fixture de
    conveniencia para tests que no son SOBRE el flujo de confirmación en sí,
    pero necesitan un principal ya establecido (p.ej. para gestionar otros
    Ocupantes, que exige `es_principal`)."""
    from app.domain.ocupante_service import confirmar_ocupante
    from app.domain.staff_service import create_initial_admin

    admin = client.db.query(Usuario).filter(Usuario.rol == RolUsuario.ADMIN).first()
    if admin is None:
        admin = create_initial_admin(client.db, "admin@club.com", "Admin", "Contrasena1")
    ocupante = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.desvinculado_en.is_(None)
    ).one()
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


def test_con_sesion_sin_apartamento_muestra_el_picker_listo_para_declarar(client):
    # Catálogo cerrado (.scratch/apartamento-catalogo-confirmacion, ticket
    # 04): sin Conjunto que "asignar" primero, cualquier residente ve el
    # picker de Torre/Apartamento listo, incluso sin unidad todavía.
    _login_cliente(client)
    r = client.get("/mis-datos")
    assert r.status_code == 200
    assert 'name="nombre"' in r.text
    assert 'name="torre"' in r.text and 'name="apartamento"' in r.text
    assert 'id="conjunto"' not in r.text  # sin campo de Conjunto


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


def test_con_apartamento_declarado_el_picker_muestra_la_seleccion_actual(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    r = client.get("/mis-datos")
    assert r.status_code == 200
    # Sin campo de Conjunto (único y global -- .scratch/apartamento-
    # catalogo-confirmacion, ticket 04); Torre/Apartamento como selects con
    # la unidad actual ya elegida.
    assert 'id="conjunto"' not in r.text
    assert 'name="torre"' in r.text and 'name="apartamento"' in r.text
    assert '<option value="TORRE 1" selected>' in r.text
    assert '<option value="101" selected>' in r.text


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


def test_campo_segundo_contacto_viejo_ya_no_aparece_en_el_formulario(client):
    # Retirado (.scratch/mis-datos, ticket 07) -- reemplazado por el sistema
    # de Ocupantes. La columna sigue en la base (dato histórico neutral),
    # pero el formulario ya no la muestra ni la escribe.
    _login_cliente(client)
    r = client.get("/mis-datos")
    assert 'name="segundo_contacto"' not in r.text


def test_documento_ya_no_se_acepta_en_este_formulario(client):
    persona = _login_cliente(client)
    client.post("/mis-datos", data={"documento": "123", "tipo_documento": "CC"})
    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.documento is None and p.tipo_documento is None


def test_torre_apartamento_fuera_del_catalogo_se_rechaza(client):
    # Catálogo cerrado (`.scratch/apartamento-catalogo-confirmacion`, ticket
    # 03): reemplaza el viejo candado de "conjunto sin asignar" -- ahora lo
    # que rechaza una declaración es que la terna no exista en el catálogo.
    persona = _login_cliente(client)
    r = client.post(
        "/mis-datos",
        data={"torre": "TORRE 99", "apartamento": "101"},
    )
    assert r.status_code == 400

    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.apartamento_actual_id is None


def test_enviar_un_conjunto_en_el_formulario_no_tiene_ningun_efecto(client):
    # Aunque alguien arme el POST a mano con "conjunto", el servidor lo ignora
    # por completo -- nunca lee ese campo del cliente (es único y global,
    # `configuracion_conjunto_service`). La declaración de Torre 1/101 (real,
    # del catálogo) igual funciona.
    persona = _login_cliente(client)
    r = client.post(
        "/mis-datos",
        data={"conjunto": "Cualquiera", "torre": "TORRE 1", "apartamento": "101"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    p = client.db.get(Persona, persona.id)
    assert p.apartamento_actual_id == apto.id
    assert apto.conjunto == "EL CLUB"  # nunca "Cualquiera"


def test_con_conjunto_ya_asignado_el_cliente_actualiza_torre_y_apartamento(client):
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
    # El Conjunto se mantuvo (nunca vino del cliente); Torre/Apto cambiaron.
    assert (apto.conjunto, apto.torre, apto.apartamento) == ("EL CLUB", "TORRE 2", "202")


# --------------------------------------------------------------------------- #
# Ticket 01 (.scratch/mis-datos) — declarar apartamento crea el Ocupante
# principal.
# --------------------------------------------------------------------------- #
def test_declarar_apartamento_por_primera_vez_crea_ocupante_pending(client):
    # El Conjunto ya está asignado (por staff, vía declare_unit directo --
    # mismo bootstrap que el resto de los tests de esta sección), pero esta
    # Persona nunca pasó por el padrón de Ocupantes todavía.
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    persona = _login_cliente(client)
    r = client.post(
        "/mis-datos",
        data={"torre": "TORRE 1", "apartamento": "101"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    ocupantes = client.db.query(Ocupante).filter(Ocupante.apartamento_id == apto.id).all()
    assert len(ocupantes) == 1
    assert ocupantes[0].persona_id == persona.id
    # Confirmación (ticket 06): nace pending, ya no principal automático.
    assert ocupantes[0].confirmado_en is None
    assert ocupantes[0].es_principal is False


def test_reenviar_el_mismo_apartamento_no_duplica_el_ocupante(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})

    client.db.expire_all()
    ocupantes = client.db.query(Ocupante).filter(Ocupante.apartamento_id == apto.id).all()
    assert len(ocupantes) == 1


def test_cambiar_de_apartamento_da_de_baja_el_ocupante_anterior(client):
    # Ya es Ocupante (principal, solo) de A/101 -- vía la ruta, no bootstrap.
    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto1, [("3001234567", "Ana")])
    client.db.commit()

    persona = _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    _confirmar_principal(client, apto1)  # confirmada como principal (ticket 06)

    r = client.post(
        "/mis-datos",
        data={"torre": "TORRE 2", "apartamento": "202"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    ocupantes_1 = client.db.query(Ocupante).filter(Ocupante.apartamento_id == apto1.id).all()
    assert len(ocupantes_1) == 1
    assert ocupantes_1[0].desvinculado_en is not None  # el viejo queda de baja

    apto2 = client.db.query(Apartamento).filter(
        Apartamento.torre == "TORRE 2", Apartamento.apartamento == "202"
    ).one()
    ocupantes_2 = client.db.query(Ocupante).filter(Ocupante.apartamento_id == apto2.id).all()
    assert len(ocupantes_2) == 1
    assert ocupantes_2[0].persona_id == persona.id
    # Unidad nueva para ella -- nace pending igual que cualquier Ocupante
    # nuevo (ticket 06), sin importar que ya fue principal en la anterior.
    assert ocupantes_2[0].confirmado_en is None
    assert ocupantes_2[0].es_principal is False


def test_cambiar_de_apartamento_se_rechaza_si_quedan_otros_ocupantes_activos(client):
    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto1, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    _confirmar_principal(client, apto1)  # confirmada como principal (ticket 06)

    from app.domain.ocupante_service import agregar_ocupante

    apto1_obj = client.db.query(Apartamento).filter(
        Apartamento.torre == "TORRE 1", Apartamento.apartamento == "101"
    ).one()
    agregar_ocupante(client.db, apto1_obj, "Hijo")  # sin teléfono, sigue activo
    client.db.commit()

    r = client.post("/mis-datos", data={"torre": "TORRE 2", "apartamento": "202"})
    assert r.status_code == 400

    client.db.expire_all()
    ocupantes_1 = client.db.query(Ocupante).filter(Ocupante.apartamento_id == apto1_obj.id).all()
    assert all(o.desvinculado_en is None for o in ocupantes_1)  # nada cambió (todo o nada)


def test_actualizar_torre_reutiliza_apartamento_existente_sin_mutar_a_otros(client):
    # Beto ya está en A/101 del mismo conjunto (declarado por staff).
    apto_destino = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto_destino, [("3019999999", "Beto")])
    # Ana está en el mismo Conjunto pero en otra Torre/Apto.
    apto_ana = resolver_apartamento(client.db, "TORRE 2", "202")
    declare_unit(client.db, apto_ana, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    r = client.post(
        "/mis-datos",
        data={"torre": "TORRE 1", "apartamento": "101"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    # Catálogo cerrado (ticket 03): no hay conteo total significativo que
    # afirmar (las 804 unidades ya existían) -- "reutilizado, no duplicado"
    # se prueba por identidad de fila abajo (mismo `apto_destino.id`).
    ana = client.db.query(Persona).filter(Persona.telefono == "+573001234567").one()
    beto = client.db.query(Persona).filter(Persona.telefono == "+573019999999").one()
    assert ana.apartamento_actual_id == apto_destino.id
    assert beto.apartamento_actual_id == apto_destino.id  # sigue ahí, sin ser tocado


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


def test_principal_ve_la_tarjeta_mis_ocupantes(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    _confirmar_principal(client, apto)

    r = client.get("/mis-datos")
    assert "Mis Ocupantes" in r.text


def test_sin_apartamento_no_ve_la_tarjeta_mis_ocupantes(client):
    _login_cliente(client)
    r = client.get("/mis-datos")
    assert "Mis Ocupantes" not in r.text


def test_principal_crea_ocupante_sin_telefono(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
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
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    _confirmar_principal(client, apto)

    r = client.post(
        "/mis-datos/ocupantes",
        data={"nombre": "Hija", "telefono": "3021112233"},
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
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    _confirmar_principal(client, apto)

    r = client.post("/mis-datos/ocupantes", data={})
    assert r.status_code == 400


def test_crear_ocupante_respeta_limite_de_5(client):
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    _confirmar_principal(client, apto)

    for i in range(4):  # +1 principal ya existente = 5
        agregar_ocupante(client.db, apto, f"Extra{i}")
    client.db.commit()

    r = client.post("/mis-datos/ocupantes", data={"nombre": "Seis"})
    assert r.status_code == 400


def test_principal_asocia_telefono_a_ocupante_existente(client):
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
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
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    _confirmar_principal(client, apto)

    hija = agregar_ocupante(client.db, apto, "Hija", telefono="3021112233")
    client.db.commit()

    r = client.post(
        f"/mis-datos/ocupantes/{hija.id}/desvincular-telefono", follow_redirects=False
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Ocupante, hija.id).persona_id is None


def test_principal_da_de_baja_a_ocupante_no_principal(client):
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    _confirmar_principal(client, apto)

    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.post(f"/mis-datos/ocupantes/{hijo.id}/baja", follow_redirects=False)
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Ocupante, hijo.id).desvinculado_en is not None


def test_desvincular_telefono_del_principal_por_ruta_falla(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    _confirmar_principal(client, apto)

    mi_ocupante = client.db.query(Ocupante).filter(Ocupante.apartamento_id == apto.id).one()
    r = client.post(f"/mis-datos/ocupantes/{mi_ocupante.id}/desvincular-telefono")
    assert r.status_code == 400


def test_principal_promueve_a_ocupante_con_telefono(client):
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
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
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    _confirmar_principal(client, apto)

    from app.domain.ocupante_service import agregar_ocupante

    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.post(f"/mis-datos/ocupantes/{hijo.id}/promover")
    assert r.status_code == 400


def test_ocupante_no_principal_ve_roster_de_solo_lectura(client):
    from app.domain.ocupante_service import agregar_ocupante, asociar_telefono_a_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)  # Ana, principal
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    _confirmar_principal(client, apto)

    hija = agregar_ocupante(client.db, apto, "Hija")
    asociar_telefono_a_ocupante(client.db, hija, "3021112233")
    client.db.commit()

    r = _login_cliente(client, telefono="3021112233")  # ahora entra Hija
    assert r.telefono == "+573021112233"

    pagina = client.get("/mis-datos")
    assert "Quien más viven acá" in pagina.text
    assert "Mis Ocupantes" not in pagina.text  # gestión es solo del principal
    assert "ANA" in pagina.text
    assert "+573001234567" in pagina.text  # ve todo -- incluye teléfonos ajenos


def test_ocupante_no_principal_no_puede_cambiar_torre_apartamento(client):
    from app.domain.ocupante_service import agregar_ocupante, asociar_telefono_a_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})

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
    from app.domain.ocupante_service import agregar_ocupante, asociar_telefono_a_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})

    hija = agregar_ocupante(client.db, apto, "Hija")
    asociar_telefono_a_ocupante(client.db, hija, "3021112233")
    client.db.commit()

    _login_cliente(client, telefono="3021112233")
    r = client.post("/mis-datos/ocupantes/salir", follow_redirects=False)
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Ocupante, hija.id).desvinculado_en is not None


def test_gestionar_ocupante_de_otro_apartamento_da_403(client):
    from app.domain.ocupante_service import agregar_ocupante

    apto_propio = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto_propio, [("3001234567", "Ana")])
    apto_ajeno = resolver_apartamento(client.db, "TORRE 2", "202")
    ocupante_ajeno = agregar_ocupante(client.db, apto_ajeno, "Beto", telefono="3019999999")
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})

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


def test_principal_edita_telefono_a_uno_en_uso_falla(client):
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3019999999", "Otro")
    client.db.commit()

    _login_cliente(client)
    r = client.post("/mis-datos", data={"telefono": "3019999999"})
    assert r.status_code == 400
    assert "ya está en uso" in r.text


def test_principal_edita_telefono_de_un_ocupante_ya_asociado(client):
    from app.domain.ocupante_service import agregar_ocupante, asociar_telefono_a_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
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


def test_cambiar_apartamento_con_dependientes_da_mensaje_claro(client):
    # `.scratch/pendientes-cliente/issues/38` -- el mensaje ya no habla de
    # "darte de baja" (confuso, no era la intención), sino de que el cambio
    # de Torre/Apartamento está bloqueado por tener otros Ocupantes activos.
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    _confirmar_principal(client, apto)
    agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "102"})
    assert r.status_code == 400
    assert "cambiar de Torre/Apartamento" in r.text
    assert "darte de baja" not in r.text


def test_torre_o_apartamento_incompleto_rechaza_todo_el_request(client):
    # Torre sin apartamento debe rechazar TODO el request, no solo el
    # apartamento.
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    persona = _login_cliente(client)
    client.post("/mis-datos", data={"nombre": "Ana"})

    r = client.post(
        "/mis-datos", data={"nombre": "Cambiado", "torre": "B"}
    )
    assert r.status_code == 400

    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.nombre == "ANA"  # tampoco se guardó el nombre de este request
    apto_sin_cambios = client.db.get(Apartamento, p.apartamento_actual_id)
    assert apto_sin_cambios.torre == "TORRE 1"  # el cambio parcial no se aplicó


def test_cambiar_de_apartamento_no_reescribe_snapshot_de_paquete_ya_anunciado(client):
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

    client.post("/mis-datos", data={"torre": "TORRE 2", "apartamento": "202"})

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


def test_llamada_y_whatsapp_no_se_pueden_activar(client):
    # `.scratch/pendientes-cliente/issues/36` -- sin proveedor conectado, el
    # servidor ignora esos 2 canales aunque alguien fuerce el POST crudo.
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    persona = _login_cliente(client)
    client.post(
        "/mis-datos",
        data={"pref_WHATSAPP_RECIBIDO": "on", "pref_LLAMADA_RECIBIDO": "on"},
    )
    client.db.expire_all()

    assert preferencia_activa(
        client.db, persona.id, CanalNotificacion.WHATSAPP, EstadoPaquete.RECIBIDO
    ) is False
    assert preferencia_activa(
        client.db, persona.id, CanalNotificacion.LLAMADA, EstadoPaquete.RECIBIDO
    ) is False


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


def test_matriz_vacia_no_rompe_el_resto_del_guardado(client):
    persona = _login_cliente(client)
    client.post("/mis-datos", data={"nombre": "Ana Actualizada"})
    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.nombre == "ANA ACTUALIZADA"  # el resto del formulario se guardó igual


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
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
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
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
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
    from app.domain.ocupante_service import agregar_ocupante

    apto_propio = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto_propio, [("3001234567", "Ana")])
    apto_ajeno = resolver_apartamento(client.db, "TORRE 2", "202")
    ocupante_ajeno = agregar_ocupante(client.db, apto_ajeno, "Beto", telefono="3019999999")
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    _confirmar_principal(client, apto_propio)

    r = client.post(f"/mis-datos/ocupantes/{ocupante_ajeno.id}/confirmar")
    assert r.status_code == 403


def test_veo_mi_propio_reclamo_pending_sin_que_me_bloquee_nada(client):
    # Ticket 08: declarar por primera vez deja el reclamo pending -- se debe
    # ver reflejado, pero sin bloquear ninguna otra función de la pantalla
    # (ticket 06: pending no pierde funcionalidad).
    persona = _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})

    r = client.get("/mis-datos")
    assert r.status_code == 200
    assert "pendiente de confirmación" in r.text

    # El resto de la pantalla sigue funcionando con normalidad (guardar el nombre).
    r2 = client.post("/mis-datos", data={"nombre": "Nombre Nuevo"})
    assert r2.status_code in (200, 303)
    client.db.expire_all()
    p = client.db.get(Persona, persona.id)
    assert p.nombre == "NOMBRE NUEVO"


def test_una_vez_confirmado_ya_no_se_ve_el_aviso_de_pending(client):
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()

    _login_cliente(client)
    client.post("/mis-datos", data={"torre": "TORRE 1", "apartamento": "101"})
    _confirmar_principal(client, apto)

    r = client.get("/mis-datos")
    assert "pendiente de confirmación" not in r.text
