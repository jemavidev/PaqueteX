# -*- coding: utf-8 -*-
"""
Capa web — `/announce` (rediseño `.scratch/announce-rapido`): campo único
inteligente (Teléfono/WhatsApp -- ticket 04; Torre+Apartamento -- ticket 05)
+ Anunciar.

Comportamiento observable por HTTP: exige sesión de staff (CUALQUIER rol);
`GET /announce/identificar` clasifica el valor en el servidor (nunca confía
en el cliente) y devuelve el fragmento correcto; `POST /announce` anuncia
por tres caminos -- Teléfono/WhatsApp directo (Persona resuelta como
Anunciante Y Destinatario, `Destinatario.yo_mismo()`), un residente YA
existente elegido de una lista (`ocupante_id`, `Destinatario.ocupante()`),
o un residente NUEVO dentro de una unidad (`torre`+`apartamento`+`nombre`,
da de alta el Ocupante y anuncia en el mismo paso).
"""

from app.domain.paquete import Paquete
from app.domain.persona import Persona
from app.domain.persona_service import get_or_create_persona, get_or_create_persona_por_whatsapp
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario

_PW = "Contrasena1"


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    staff = create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})
    return staff


def test_sin_sesion_redirige_a_login(client):
    r = client.get("/announce", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_operador_ve_el_campo_unico_y_el_enlace_a_residentes(client):
    _login_operador(client)
    r = client.get("/announce")
    assert r.status_code == 200
    assert 'name="q"' in r.text
    assert 'href="/residentes"' in r.text
    # El formulario viejo de 3 bloques desapareció.
    assert 'name="torre"' not in r.text
    assert 'name="conjunto"' not in r.text


# --------------------------------------------------------------------------- #
# GET /announce/identificar -- clasificación server-side + fragmento
# --------------------------------------------------------------------------- #
def test_identificar_sin_sesion_redirige_a_login(client):
    r = client.get("/announce/identificar", params={"q": "3001234567"}, follow_redirects=False)
    assert r.status_code == 303


def test_identificar_telefono_con_match_muestra_a_la_persona(client):
    _login_operador(client)
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "ANA" in r.text
    assert 'name="telefono"' in r.text
    assert 'name="nombre"' not in r.text  # ya existe, no pide nombre


def test_identificar_telefono_sin_match_pide_nombre(client):
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert 'name="telefono"' in r.text
    assert 'name="nombre"' in r.text


def test_identificar_sin_match_recibir_esta_cableado(client):
    # Ticket 06: encontrado en code-review -- este fragmento (persona NUEVA
    # por Teléfono/WhatsApp directo, camino 1) tenía su PROPIO botón
    # Recibir, distinto del de `_persona_resuelta.html`, y se había quedado
    # sin cablear (`type="button" disabled` de siempre) mientras los otros
    # dos caminos sí quedaban listos.
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    # `boton()` (`_botones.html`) pone `name`/`value` en líneas separadas --
    # se busca cada atributo por su cuenta, no como substring adyacente.
    # El placeholder viejo (`type="button" disabled`) no tenía ninguno de
    # los dos.
    assert 'value="recibir"' in r.text
    assert 'value="anunciar"' in r.text
    assert r.text.count('name="accion"') == 2


def test_identificar_nombre_del_fragmento_no_lleva_autofocus(client):
    # Bug real encontrado en code-review antes de desplegar: el fragmento se
    # re-renderiza (innerHTML) en CADA tecleo del campo principal -- un
    # Nombre con autofocus le robaría el foco de vuelta en cada actualización
    # mientras el staff sigue escribiendo. Ver `_identificar.html`.
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "autofocus" not in r.text


def test_identificar_telefono_incompleto_no_dispara_nada(client):
    # Mismo bug: sin este umbral, el primer dígito ("3") ya clasificaba
    # como candidato completo.
    _login_operador(client)
    for prefijo in ("3", "30", "300123"):
        r = client.get("/announce/identificar", params={"q": prefijo})
        assert r.status_code == 200
        assert r.text == "", f"prefijo {prefijo!r} no debería disparar nada todavía"


def test_identificar_whatsapp_de_una_o_dos_letras_no_dispara_nada(client):
    _login_operador(client)
    for prefijo in ("a", "an"):
        r = client.get("/announce/identificar", params={"q": prefijo})
        assert r.status_code == 200
        assert r.text == ""


def test_identificar_whatsapp_con_match_muestra_a_la_persona(client):
    _login_operador(client)
    get_or_create_persona_por_whatsapp(client.db, "ana.whats", "Ana")
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "ana.whats"})
    assert r.status_code == 200
    assert "ANA" in r.text
    assert 'name="whatsapp_usuario"' in r.text
    assert 'name="nombre"' not in r.text


def test_identificar_whatsapp_sin_match_pide_nombre(client):
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": "ana.whats"})
    assert r.status_code == 200
    assert 'name="whatsapp_usuario"' in r.text
    assert 'name="nombre"' in r.text


def test_identificar_torre_apto_incompleto_no_dispara_nada(client):
    _login_operador(client)
    for prefijo in ("0", "01", "011"):
        r = client.get("/announce/identificar", params={"q": prefijo})
        assert r.status_code == 200
        assert r.text == "", f"prefijo {prefijo!r} no debería resolver todavía"


def test_identificar_torre_apto_invalido_no_dispara_nada(client):
    # "99" no es una torre real (1-10).
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": "99106"})
    assert r.status_code == 200
    assert r.text == ""


def test_identificar_torre_apto_con_pocos_digitos_de_apto_no_dispara_nada(client):
    """.scratch/ocupante-principal-escenarios, ticket 15 -- con menos de 3
    dígitos de apartamento (el mínimo real, "101") sigue siendo "a medio
    teclear", igual que antes de este ticket: mostrar el aviso ahí
    interrumpiría al staff a mitad de un código real (ej. camino a "01106")."""
    _login_operador(client)
    for q in ("01", "011", "0110"):
        r = client.get("/announce/identificar", params={"q": q})
        assert r.status_code == 200
        assert r.text == "", f"{q!r} no debería disparar el aviso todavía"


def test_identificar_torre_apto_completo_sin_match_muestra_aviso(client):
    """.scratch/ocupante-principal-escenarios, ticket 15 -- torre válida +
    apto de 3+ dígitos que no calza con ninguna unidad real: antes no
    mostraba nada, ahora avisa explícito."""
    _login_operador(client)
    # TORRE 1 (chica) solo tiene 101-106, 201-206, ..., 701-702 -- "199" no
    # es ninguna unidad real.
    r = client.get("/announce/identificar", params={"q": "01199"})
    assert r.status_code == 200
    assert "No encontramos esa Torre/Apartamento" in r.text


def test_identificar_valor_sin_candidato_no_devuelve_nada(client):
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": "500 no es nada"})
    assert r.status_code == 200
    assert r.text == ""


def test_identificar_vacio_no_devuelve_nada(client):
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": ""})
    assert r.status_code == 200
    assert r.text == ""


def test_identificar_reclasifica_en_servidor_sin_confiar_en_el_cliente(client):
    # El "cliente" (este test) manda un valor con forma de Torre+Apto que no
    # calza con ninguna unidad real -- el servidor no lo reclasifica como
    # Teléfono ni WhatsApp solo porque alguien lo pida distinto (ni lo trata
    # como un número de teléfono, aunque sea igual de largo).
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": "0110699999999999"})
    assert r.status_code == 200
    assert "Ya registrado" not in r.text
    # .scratch/ocupante-principal-escenarios, ticket 15: torre válida + apto
    # de sobra dígitos ya es "completo" -- sin match, avisa en vez de callar.
    assert "No encontramos esa Torre/Apartamento" in r.text


# --------------------------------------------------------------------------- #
# POST /announce -- Anunciar
# --------------------------------------------------------------------------- #
def test_anunciar_por_telefono_de_persona_existente(client):
    staff = _login_operador(client)
    ana = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    r = client.post("/announce", data={"telefono": "3001234567"})
    assert r.status_code == 200
    assert "ANA" in r.text  # toast de confirmación

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "ANA"
    assert p.announced_by_persona_id == ana.id
    assert p.announced_by_phone == "+573001234567"
    assert p.announced_by_usuario_id == staff.id


def test_anunciar_por_telefono_nuevo_crea_persona(client):
    _login_operador(client)
    r = client.post("/announce", data={"telefono": "3001234567", "nombre": "Ana"})
    assert r.status_code == 200

    client.db.expire_all()
    persona = client.db.query(Persona).one()
    assert persona.telefono == "+573001234567"
    assert persona.nombre == "ANA"
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "ANA"


def test_anunciar_por_telefono_nuevo_sin_nombre_falla(client):
    _login_operador(client)
    r = client.post("/announce", data={"telefono": "3001234567"})
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Persona).count() == 0
    assert client.db.query(Paquete).count() == 0


def test_anunciar_por_whatsapp_de_persona_existente(client):
    _login_operador(client)
    ana = get_or_create_persona_por_whatsapp(client.db, "ana.whats", "Ana")
    client.db.commit()

    r = client.post("/announce", data={"whatsapp_usuario": "ana.whats"})
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.announced_by_persona_id == ana.id
    assert p.announced_by_phone is None
    assert p.recipient_phone is None


def test_anunciar_por_whatsapp_nuevo_crea_persona_solo_whatsapp(client):
    _login_operador(client)
    r = client.post("/announce", data={"whatsapp_usuario": "ana.whats", "nombre": "Ana"})
    assert r.status_code == 200

    client.db.expire_all()
    persona = client.db.query(Persona).one()
    assert persona.telefono is None
    assert persona.whatsapp_usuario == "ana.whats"


def test_anunciar_sin_telefono_ni_whatsapp_falla(client):
    _login_operador(client)
    r = client.post("/announce", data={"nombre": "Ana"})
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Paquete).count() == 0


def test_anunciar_con_telefono_y_whatsapp_juntos_falla(client):
    _login_operador(client)
    r = client.post(
        "/announce",
        data={"telefono": "3001234567", "whatsapp_usuario": "ana.whats", "nombre": "Ana"},
    )
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Paquete).count() == 0


def test_anunciar_deja_el_formulario_listo_para_el_siguiente(client):
    _login_operador(client)
    r = client.post("/announce", data={"telefono": "3001234567", "nombre": "Ana"})
    assert r.status_code == 200
    # El campo único vuelve a estar presente y vacío, listo para el próximo.
    assert 'name="q"' in r.text
    assert "autofocus" in r.text


# --------------------------------------------------------------------------- #
# Ticket 05 -- Torre+Apto: resolución en vivo + lista de residentes +
# nueva persona + Anunciar.
# --------------------------------------------------------------------------- #
def test_identificar_torre_apto_con_residentes_muestra_la_lista(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    confirmar_ocupante(client.db, papa, staff)  # Principal confirmado
    agregar_ocupante(client.db, apto, "Hijo")  # sin contacto
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "01106"})
    assert r.status_code == 200
    assert "PAPÁ" in r.text
    assert "HIJO" in r.text
    assert "Principal" in r.text
    assert "Nueva persona" in r.text
    # Principal primero (listar_ocupantes ya lo ordena así).
    assert r.text.index("PAPÁ") < r.text.index("HIJO")


def test_identificar_torre_apto_unidad_vacia_solo_nueva_persona(client):
    _login_operador(client)
    r = client.get("/announce/identificar", params={"q": "01106"})
    assert r.status_code == 200
    assert 'data-ocupante-id' not in r.text
    assert "Nueva persona" in r.text
    assert 'name="torre"' in r.text
    assert 'name="apartamento"' in r.text


def test_identificar_ocupante_existente_muestra_tarjeta_anunciar(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    hija = agregar_ocupante(client.db, apto, "Hija", telefono="3021112233")
    client.db.commit()

    r = client.get("/announce/identificar-ocupante", params={"ocupante_id": str(hija.id)})
    assert r.status_code == 200
    assert "HIJA" in r.text
    assert f'name="ocupante_id" value="{hija.id}"' in r.text


def test_identificar_ocupante_inexistente_no_dispara_nada(client):
    import uuid

    _login_operador(client)
    r = client.get("/announce/identificar-ocupante", params={"ocupante_id": str(uuid.uuid4())})
    assert r.status_code == 200
    assert r.text == ""


def test_identificar_ocupante_id_invalido_no_dispara_nada(client):
    _login_operador(client)
    r = client.get("/announce/identificar-ocupante", params={"ocupante_id": "no-es-un-uuid"})
    assert r.status_code == 200
    assert r.text == ""


def test_anunciar_residente_existente_con_telefono_propio(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    hija = agregar_ocupante(client.db, apto, "Hija", telefono="3021112233")
    client.db.commit()

    r = client.post("/announce", data={"ocupante_id": str(hija.id)})
    assert r.status_code == 200
    assert "HIJA" in r.text

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "HIJA"
    assert p.announced_by_phone == "+573021112233"
    assert p.snapshot_torre == "TORRE 1"
    assert p.snapshot_apartamento == "106"
    assert p.announced_by_usuario_id == staff.id


def test_anunciar_residente_sin_contacto_cae_al_principal(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    confirmar_ocupante(client.db, papa, staff)
    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.post("/announce", data={"ocupante_id": str(hijo.id)})
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "HIJO"
    # Hijo no tiene teléfono propio: tanto el contacto de notificación
    # (telefono_notificacion_ocupante) como el Anunciante (anunciante_para_
    # ocupante) caen al mismo Principal (Papá) -- mismo mecanismo, distintas
    # columnas.
    assert p.recipient_phone == "+573001234567"
    assert p.announced_by_phone == "+573001234567"


def test_anunciar_residente_sin_contacto_ni_principal_confirmado_falla(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")  # pending, no confirmado
    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.post("/announce", data={"ocupante_id": str(hijo.id)})
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Paquete).count() == 0


def test_anunciar_ocupante_id_inexistente_falla(client):
    import uuid

    _login_operador(client)
    r = client.post("/announce", data={"ocupante_id": str(uuid.uuid4())})
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Paquete).count() == 0


def test_nueva_persona_en_unidad_con_telefono_crea_ocupante_pending_y_anuncia(client):
    _login_operador(client)

    r = client.post(
        "/announce",
        data={"torre": "TORRE 1", "apartamento": "106", "nombre": "Ana", "contacto": "3001234567"},
    )
    assert r.status_code == 200

    client.db.expire_all()
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import listar_ocupantes

    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    ocupantes = listar_ocupantes(client.db, apto)
    assert len(ocupantes) == 1
    assert ocupantes[0].nombre == "ANA"
    assert ocupantes[0].confirmado_en is None  # pending
    assert ocupantes[0].es_principal is False  # nunca automático al crear

    p = client.db.query(Paquete).one()
    assert p.recipient_name == "ANA"
    assert p.announced_by_phone == "+573001234567"
    assert p.snapshot_apartamento == "106"


def test_nueva_persona_en_unidad_con_whatsapp_crea_persona_solo_whatsapp(client):
    _login_operador(client)

    r = client.post(
        "/announce",
        data={"torre": "TORRE 1", "apartamento": "106", "nombre": "Ana", "contacto": "ana.whats"},
    )
    assert r.status_code == 200

    client.db.expire_all()
    persona = client.db.query(Persona).one()
    assert persona.telefono is None
    assert persona.whatsapp_usuario == "ana.whats"

    p = client.db.query(Paquete).one()
    assert p.announced_by_phone is None


def test_nueva_persona_en_unidad_sin_contacto_no_siendo_el_primero(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    confirmar_ocupante(client.db, papa, staff)
    client.db.commit()

    r = client.post(
        "/announce",
        data={"torre": "TORRE 1", "apartamento": "106", "nombre": "Hijo"},
    )
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "HIJO"
    assert p.announced_by_phone == "+573001234567"  # cae al Principal (Papá)


def test_nueva_persona_sin_anunciante_resolvible_no_deja_ocupante_huerfano(client):
    """.scratch/ocupante-principal-escenarios, ticket 09 -- unidad con un
    primer Ocupante SIN confirmar (sin principal todavía): agregar_ocupante
    para el nuevo residente (sin contacto) tiene éxito, pero _anunciar_para
    falla después (no hay Anunciante resolvible) -- el Ocupante recién
    creado no debe quedar persistido."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    agregar_ocupante(client.db, apto, "Ana", telefono="3001234567")  # sin confirmar
    client.db.commit()

    r = client.post(
        "/announce",
        data={"torre": "TORRE 1", "apartamento": "106", "nombre": "Hijo"},
    )
    assert r.status_code == 400

    client.db.expire_all()
    existe = (
        client.db.query(Ocupante)
        .filter(Ocupante.apartamento_id == apto.id, Ocupante.nombre == "HIJO")
        .first()
    )
    assert existe is None


def test_nueva_persona_contacto_ya_ocupante_de_otra_unidad_bloquea_sin_mover(client):
    """.scratch/ocupante-principal-escenarios, ticket 12 -- sin marcar la
    casilla, queda bloqueado con el mensaje que ofrece mover."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto_otra = resolver_apartamento(client.db, "TORRE 2", "202")
    agregar_ocupante(client.db, apto_otra, "Hija", telefono="3021112233")
    client.db.commit()

    r = client.post(
        "/announce",
        data={
            "torre": "TORRE 1", "apartamento": "106",
            "nombre": "Cualquiera", "contacto": "3021112233",
        },
    )
    assert r.status_code == 400
    assert "Mover acá" in r.text
    assert client.db.query(Paquete).count() == 0


def test_nueva_persona_mueve_marcando_la_casilla(client):
    """El nombre tecleado se ignora -- se mueve/anuncia con la identidad
    REAL (Hija), no se crea un residente nuevo llamado "Cualquiera"."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto_otra = resolver_apartamento(client.db, "TORRE 2", "202")
    hija = agregar_ocupante(client.db, apto_otra, "Hija", telefono="3021112233")
    client.db.commit()

    r = client.post(
        "/announce",
        data={
            "torre": "TORRE 1", "apartamento": "106",
            "nombre": "Cualquiera", "contacto": "3021112233",
            "mover_de_otra_unidad": "1",
        },
    )
    assert r.status_code == 200

    client.db.expire_all()
    apto_nueva = resolver_apartamento(client.db, "TORRE 1", "106")
    movida = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto_nueva.id, Ocupante.persona_id == hija.persona_id
    ).one()
    assert movida.nombre == "HIJA"
    assert client.db.get(Ocupante, hija.id).desvinculado_en is not None

    p = client.db.query(Paquete).one()
    assert p.recipient_name == "HIJA"
    assert p.snapshot_apartamento == "106"


def test_nueva_persona_primer_residente_de_unidad_vacia_sin_contacto_falla(client):
    _login_operador(client)

    r = client.post(
        "/announce",
        data={"torre": "TORRE 1", "apartamento": "106", "nombre": "Ana"},
    )
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Paquete).count() == 0
    assert client.db.query(Persona).count() == 0


def test_nueva_persona_sin_nombre_falla(client):
    _login_operador(client)
    r = client.post(
        "/announce",
        data={"torre": "TORRE 1", "apartamento": "106", "contacto": "3001234567"},
    )
    assert r.status_code == 400
    client.db.expire_all()
    assert client.db.query(Paquete).count() == 0


def test_nueva_persona_torre_apartamento_invalido_falla(client):
    _login_operador(client)
    r = client.post(
        "/announce",
        data={"torre": "TORRE 99", "apartamento": "106", "nombre": "Ana"},
    )
    assert r.status_code == 400


def test_nueva_persona_contacto_invalido_rechaza_sin_crear_sin_contacto(client):
    # Bug real encontrado en code-review: un contacto que no clasifica ni
    # como Teléfono ni como WhatsApp NO debe descartarse en silencio -- debe
    # rechazarse explícitamente, para que el Ocupante nunca quede creado sin
    # el contacto que el staff sí quiso darle.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    confirmar_ocupante(client.db, papa, staff)
    client.db.commit()

    r = client.post(
        "/announce",
        data={"torre": "TORRE 1", "apartamento": "106", "nombre": "Hijo", "contacto": "30012345"},
    )
    assert r.status_code == 400

    client.db.expire_all()
    from app.domain.ocupante_service import listar_ocupantes

    ocupantes = listar_ocupantes(client.db, apto)
    assert len(ocupantes) == 1  # "Hijo" NUNCA se creó sin el contacto
    assert client.db.query(Paquete).count() == 0


def test_anunciar_residente_existente_con_whatsapp_propio(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    hija = agregar_ocupante(client.db, apto, "Hija", whatsapp_usuario="hija.whats")
    client.db.commit()

    r = client.post("/announce", data={"ocupante_id": str(hija.id)})
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "HIJA"
    assert p.announced_by_phone is None  # Anunciante solo-WhatsApp


# --------------------------------------------------------------------------- #
# Ticket 06 -- Recibir: anunciar y abrir de inmediato el formulario de
# recepción (mismo componente/JS que /paquetes, `receive()` sin cambios).
# --------------------------------------------------------------------------- #
def _modal_receive_abierto(texto, paquete_id):
    """True si el HTML trae `#modal-receive-<id>` SIN el atributo `hidden`
    (ver `components/_modales.html`: el toggle usa `hidden`, no una clase)."""
    marcador = f'id="modal-receive-{paquete_id}"'
    if marcador not in texto:
        return False
    inicio = texto.index(marcador)
    fin_etiqueta = texto.index(">", inicio)
    return "hidden" not in texto[inicio:fin_etiqueta]


def test_recibir_telefono_directo_anuncia_y_muestra_modal_abierto(client):
    _login_operador(client)
    r = client.post(
        "/announce",
        data={"telefono": "3001234567", "nombre": "Ana", "accion": "recibir"},
    )
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "ANA"  # announce() corrió igual que con "anunciar"

    assert _modal_receive_abierto(r.text, p.id)
    assert f'action="/paquetes/{p.id}/recibir"' in r.text
    assert ">Recibir</button>" in r.text
    # Paso nuevo (.scratch/ocupante-principal-escenarios, ticket 05): Ana
    # quedó sin apartamento (Destinatario.yo_mismo() por Teléfono directo,
    # sin unidad) -- el modal de Recibir ofrece declararlo ahí mismo (picker
    # número->Torre, conversación 2026-08-17, sin párrafo explicativo --
    # el placeholder del input ya dice "Apartamento").
    assert 'placeholder="Apartamento (ej. 302)"' in r.text


def test_recibir_residente_existente_anuncia_y_muestra_modal_abierto(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    hija = agregar_ocupante(client.db, apto, "Hija", telefono="3021112233")
    client.db.commit()

    r = client.post("/announce", data={"ocupante_id": str(hija.id), "accion": "recibir"})
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "HIJA"
    assert _modal_receive_abierto(r.text, p.id)


def test_recibir_nueva_persona_en_unidad_anuncia_y_muestra_modal_abierto(client):
    _login_operador(client)
    r = client.post(
        "/announce",
        data={
            "torre": "TORRE 1", "apartamento": "106", "nombre": "Ana",
            "contacto": "3001234567", "accion": "recibir",
        },
    )
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "ANA"
    assert _modal_receive_abierto(r.text, p.id)


def test_anunciar_sin_accion_no_muestra_modal_de_recibir(client):
    # `accion` por defecto es "anunciar" -- comportamiento de siempre, sin
    # el modal de recepción.
    _login_operador(client)
    r = client.post("/announce", data={"telefono": "3001234567", "nombre": "Ana"})
    assert r.status_code == 200
    assert "modal-receive-" not in r.text


def test_recibir_sin_autofocus_en_el_campo_principal(client):
    # El modal ya está abierto encima -- autofocus en el campo de atrás le
    # robaría el foco al modal (misma clase de bug de ticket 04, ver
    # `test_identificar_nombre_del_fragmento_no_lleva_autofocus`).
    _login_operador(client)
    r = client.post(
        "/announce",
        data={"telefono": "3001234567", "nombre": "Ana", "accion": "recibir"},
    )
    assert r.status_code == 200
    assert "autofocus" not in r.text


def test_recibir_error_de_validacion_no_muestra_modal(client):
    # `accion=recibir` sin nombre para una persona nueva sigue fallando
    # igual que "anunciar" -- nunca se llega a crear el Paquete ni a
    # mostrar el modal.
    _login_operador(client)
    r = client.post("/announce", data={"telefono": "3001234567", "accion": "recibir"})
    assert r.status_code == 400
    assert "modal-receive-" not in r.text
    client.db.expire_all()
    assert client.db.query(Paquete).count() == 0


def test_recibir_reusa_la_ruta_existente_de_recepcion(client):
    # Requisito duro del ticket: completar el formulario transiciona a
    # RECIBIDO reusando `POST /paquetes/{id}/recibir` TAL CUAL -- sin ruta
    # nueva, sin reimplementar `receive()`.
    from app.domain.paquete import EstadoPaquete

    _login_operador(client)
    client.post(
        "/announce",
        data={"telefono": "3001234567", "nombre": "Ana", "accion": "recibir"},
    )
    client.db.expire_all()
    p = client.db.query(Paquete).one()

    r2 = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"package_type": "NORMAL", "package_condition": "BUENO"},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/paquetes"

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.estado == EstadoPaquete.RECIBIDO


# --------------------------------------------------------------------------- #
# Ticket 02 (.scratch/announce-residente-correcto) -- Teléfono/WhatsApp con
# co-residentes: elegir el destinatario correcto, Anunciante = quien llamó.
# --------------------------------------------------------------------------- #
def test_identificar_telefono_con_coresidentes_muestra_la_lista_de_la_unidad(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    mama = agregar_ocupante(client.db, apto, "Mamá", telefono="3001234567")
    confirmar_ocupante(client.db, mama, staff)
    agregar_ocupante(client.db, apto, "Hijo")  # sin contacto propio
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "MAMÁ" in r.text
    assert "HIJO" in r.text
    assert "Principal" in r.text
    assert "Nueva persona" in r.text
    assert "data-ocupante-id" in r.text  # lista de residentes, no la tarjeta directa
    assert "Ya registrado" not in r.text  # esa etiqueta es de la tarjeta directa de _identificar.html


def test_identificar_telefono_con_coresidentes_marca_a_quien_llama_como_anunciante(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    mama = agregar_ocupante(client.db, apto, "Mamá", telefono="3001234567")
    confirmar_ocupante(client.db, mama, staff)
    agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "Anunciante" in r.text
    # El badge está asociado a Mamá, no a Hijo -- confirmado por posición
    # dentro del HTML (cada residente es un <button> propio).
    idx_mama = r.text.index("MAMÁ")
    idx_hijo = r.text.index("HIJO")
    idx_badge = r.text.index("Anunciante")
    assert idx_mama < idx_badge < idx_hijo


def test_identificar_telefono_con_coresidentes_preselecciona_a_quien_llama(client):
    """.scratch/ocupante-principal-escenarios, ticket 10 -- la tarjeta
    Anunciar/Recibir de quien llamó ya viene lista en la respuesta inicial,
    sin necesitar un clic extra sobre su fila."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    mama = agregar_ocupante(client.db, apto, "Mamá", telefono="3001234567")
    confirmar_ocupante(client.db, mama, staff)
    agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert f'name="ocupante_id" value="{mama.id}"' in r.text
    assert 'name="telefono" value="+573001234567"' in r.text
    assert "Confirmar recibo" not in r.text  # es la tarjeta, no el modal de Recibir
    assert ">Anunciar<" in r.text
    assert ">Recibir<" in r.text


def test_identificar_whatsapp_con_coresidentes_muestra_la_lista_y_el_badge(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    mama = agregar_ocupante(client.db, apto, "Mamá", whatsapp_usuario="mama.whats")
    confirmar_ocupante(client.db, mama, staff)
    agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "mama.whats"})
    assert r.status_code == 200
    assert "MAMÁ" in r.text
    assert "HIJO" in r.text
    assert "Anunciante" in r.text


def test_identificar_telefono_sin_coresidentes_mantiene_el_atajo_directo(client):
    # Regresión: Ocupante de una unidad donde vive SOLA -- sigue siendo la
    # tarjeta directa de siempre, sin pantalla intermedia.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    agregar_ocupante(client.db, apto, "Ana", telefono="3001234567")
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "Anunciante" not in r.text
    assert "data-ocupante-id" not in r.text
    assert "Ya registrado" in r.text
    assert 'name="telefono"' in r.text


def test_coresidentes_notificacion_cae_a_quien_llamo_no_al_principal(client):
    """.scratch/ocupante-principal-escenarios, ticket 10 -- cuando el
    destinatario elegido no tiene contacto propio, la notificación
    (recipient_phone) cae a quien identificó por Teléfono/WhatsApp, NO al
    principal de la unidad (a menos que sean la misma persona)."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    mama = agregar_ocupante(client.db, apto, "Mamá", telefono="3001234567")
    confirmar_ocupante(client.db, mama, staff)  # Mamá es la principal
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3007654321")
    hijo = agregar_ocupante(client.db, apto, "Hijo")  # sin contacto propio
    client.db.commit()

    # Papá (NO es el principal) llama y anuncia a nombre de Hijo.
    r = client.post(
        "/announce", data={"ocupante_id": str(hijo.id), "telefono": "3007654321"}
    )
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "HIJO"
    assert p.announced_by_persona_id == papa.persona_id
    assert p.recipient_phone == "+573007654321"  # el de Papá (quien llamó), no el de Mamá


def test_elegir_residente_distinto_anuncia_con_anunciante_quien_llamo(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    mama = agregar_ocupante(client.db, apto, "Mamá", telefono="3001234567")
    confirmar_ocupante(client.db, mama, staff)
    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.post(
        "/announce", data={"ocupante_id": str(hijo.id), "telefono": "3001234567"}
    )
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "HIJO"
    assert p.announced_by_persona_id == mama.persona_id
    assert p.announced_by_phone == "+573001234567"


def test_elegir_a_quien_llama_funciona_como_yo_mismo(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    mama = agregar_ocupante(client.db, apto, "Mamá", telefono="3001234567")
    confirmar_ocupante(client.db, mama, staff)
    agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.post(
        "/announce", data={"ocupante_id": str(mama.id), "telefono": "3001234567"}
    )
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "MAMÁ"
    assert p.announced_by_persona_id == mama.persona_id
    assert p.announced_by_phone == "+573001234567"
    assert p.recipient_phone == "+573001234567"


def test_nueva_persona_desde_coresidentes_anunciante_es_quien_llamo(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante, listar_ocupantes

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    mama = agregar_ocupante(client.db, apto, "Mamá", telefono="3001234567")
    confirmar_ocupante(client.db, mama, staff)
    agregar_ocupante(client.db, apto, "Hijo")  # co-residente, sin él este camino no se activa
    client.db.commit()

    r = client.post(
        "/announce",
        data={
            "torre": "TORRE 1", "apartamento": "106", "nombre": "Visita",
            "telefono": "3001234567",
        },
    )
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "VISITA"
    assert p.announced_by_persona_id == mama.persona_id
    assert p.announced_by_phone == "+573001234567"
    assert len(listar_ocupantes(client.db, apto)) == 3  # Mamá, Hijo, Visita


def test_recibir_desde_camino_con_coresidentes(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    mama = agregar_ocupante(client.db, apto, "Mamá", telefono="3001234567")
    confirmar_ocupante(client.db, mama, staff)
    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.post(
        "/announce",
        data={"ocupante_id": str(hijo.id), "telefono": "3001234567", "accion": "recibir"},
    )
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert "modal-receive-" in r.text
    assert f'action="/paquetes/{p.id}/recibir"' in r.text


def test_identificar_ocupante_propaga_anunciante_telefono_a_la_tarjeta(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    mama = agregar_ocupante(client.db, apto, "Mamá", telefono="3001234567")
    confirmar_ocupante(client.db, mama, staff)
    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.get(
        "/announce/identificar-ocupante",
        params={"ocupante_id": str(hijo.id), "anunciante_telefono": "3001234567"},
    )
    assert r.status_code == 200
    assert 'name="telefono" value="3001234567"' in r.text
    assert f'name="ocupante_id" value="{hijo.id}"' in r.text


def test_identificar_ocupante_sin_anunciante_conocido_no_agrega_telefono(client):
    # Camino Torre+Apto directo (sin llamante conocido) -- regresión.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    hija = agregar_ocupante(client.db, apto, "Hija", telefono="3021112233")
    client.db.commit()

    r = client.get("/announce/identificar-ocupante", params={"ocupante_id": str(hija.id)})
    assert r.status_code == 200
    assert 'name="telefono"' not in r.text
