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


def test_operador_ve_el_campo_unico(client):
    # Issue 324 (.scratch/pendientes-cliente): el enlace "¿Solo registrar
    # residentes?" (antes -> /residentes) se retiró del encabezado.
    _login_operador(client)
    r = client.get("/announce")
    assert r.status_code == 200
    assert 'name="q"' in r.text
    assert "¿Solo registrar residentes?" not in r.text
    # El formulario viejo de 3 bloques desapareció -- ojo, no un plain
    # 'name="torre"' not in r.text: desde el bug/mejora de "+ Nueva
    # persona" (seguimiento a issue 327) el JS de esta misma página tiene
    # un selector `[name="torre"]` en texto, que también matchea esa
    # substring aunque no exista ningún <input> real con ese name.
    assert 'name="torre" value=' not in r.text
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


def test_identificar_telefono_con_bandera_off_pide_autorizacion_por_whatsapp(client):
    # Issue 326 (.scratch/pendientes-cliente): `autoriza_recepcion_
    # automatica` en False (default) -- no aparece Recibir, aparece un link
    # de WhatsApp con el mensaje de autorización pre-cargado.
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert r.text.count('name="accion"') == 1  # solo Anunciar
    assert "wa.me/573001234567?text=" in r.text
    assert "Auto</span>" not in r.text


def test_identificar_telefono_con_bandera_on_muestra_pildora_y_recibir(client):
    from app.domain.persona_service import set_autoriza_recepcion_automatica

    ana = get_or_create_persona(client.db, "3001234567", "Ana")
    set_autoriza_recepcion_automatica(client.db, ana, True)
    client.db.commit()
    _login_operador(client)

    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert r.text.count('name="accion"') == 2  # Anunciar + Recibir
    assert 'value="recibir"' in r.text
    assert "wa.me" not in r.text
    assert "Auto</span>" in r.text


def test_identificar_telefono_de_baja_muestra_aviso_sin_notificar(client):
    # .scratch/baja-administrativa (ticket 04): puramente informativo -- no
    # bloquea Anunciar/Recibir, solo avisa que no se le va a notificar nada.
    from app.domain.persona_service import dar_de_baja_administrativa

    ana = get_or_create_persona(client.db, "3001234567", "Ana")
    dar_de_baja_administrativa(client.db, ana)
    client.db.commit()
    _login_operador(client)

    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "Sin notificar</span>" in r.text


def test_identificar_telefono_activo_no_muestra_aviso_sin_notificar(client):
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "Sin notificar</span>" not in r.text


def test_identificar_telefono_con_anunciado_muestra_link_recibir(client):
    # Issue 164 (.scratch/pendientes-cliente): al identificar a un residente
    # con un paquete ANUNCIADO a su nombre, aparece listado con su código de
    # acceso y un link para "Recibir" directo (abre /paquetes?recibir=<id>).
    from app.domain.paquete_service import Destinatario, announce

    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)
    paquete = announce(
        client.db, anunciante_telefono="3001234567", anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert paquete.access_code in r.text
    assert f'href="/paquetes?recibir={paquete.id}"' in r.text


def test_identificar_telefono_con_recibido_no_lo_lista(client):
    # Issue 325 (.scratch/pendientes-cliente): un paquete RECIBIDO ya no
    # aparece en "Ya tiene paquetes en curso" -- /announce es para
    # anunciar/recibir, no para entregar/cancelar (eso vive en /paquetes).
    from app.domain.paquete_lifecycle import receive
    from app.domain.paquete_service import Destinatario, announce
    from app.domain.usuario import RolUsuario, Usuario

    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)
    admin = client.db.query(Usuario).filter(Usuario.rol == RolUsuario.ADMIN).one()
    paquete = announce(
        client.db, anunciante_telefono="3001234567", anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    receive(client.db, paquete, admin)
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "Ya tiene paquetes en curso" not in r.text
    assert paquete.access_code not in r.text


def test_identificar_telefono_sin_paquetes_no_muestra_la_seccion(client):
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "Ya tiene paquetes en curso" not in r.text


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


def test_toast_de_confirmacion_incluye_codigo_clickeable_a_consultar(client):
    # Issue 131, ampliación C (pedido explícito): el código de acceso del
    # toast de éxito es un link a /consultar, mismo patrón que
    # /mis-paquetes y la columna Cliente de /paquetes.
    staff = _login_operador(client)
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    r = client.post("/announce", data={"telefono": "3001234567"})
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert f'href="/consultar?q={p.access_code}"' in r.text
    assert f'>{p.access_code}</a>' in r.text


def test_toast_de_confirmacion_escapa_el_nombre_del_destinatario(client):
    # `Markup(...).format(...)` escapa automáticamente cada valor
    # sustituido (issue 131, ampliación C) -- un nombre con caracteres
    # especiales no debe colarse como HTML real en el toast, aunque en la
    # práctica `oninput` ya lo pasa a mayúsculas del lado del cliente (el
    # servidor no debe confiar en eso).
    _login_operador(client)
    r = client.post(
        "/announce",
        data={"telefono": "3009999999", "nombre": "<b>Ana</b> & Cía"},
    )
    assert r.status_code == 200
    # `nombre` se guarda en mayúsculas (mismo criterio server-side de
    # siempre) -- las etiquetas escapadas también suben de caso, es el
    # mismo texto tal cual quedó persistido, no una transformación aparte.
    assert "<b>Ana</b>" not in r.text
    assert "&lt;B&gt;ANA&lt;/B&gt; &amp; CÍA" in r.text


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
    # Sin autofocus (issue 284, .scratch/pendientes-cliente, pedido
    # explícito del cliente): vista exclusiva de staff, se retiró TODO
    # autofocus de acá -- ya no depende de si el modal de Recibir está
    # abierto o no (antes SÍ tenía autofocus en este caso puntual).
    assert 'name="q"' in r.text
    assert "autofocus" not in r.text


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
    assert "Nueva persona" in r.text
    # Principal primero (listar_ocupantes ya lo ordena así) -- sin badge
    # visible (issue 131, mismo criterio que Recibir en issue 125).
    assert r.text.index("PAPÁ") < r.text.index("HIJO")


def test_identificar_torre_apto_lista_muestra_badge_de_anunciados(client):
    # Bug/mejora reportada en vivo (.scratch/pendientes-cliente): la lista
    # de residentes de una unidad muestra cuántos paquetes ANUNCIADO tiene
    # cada uno, en una píldora fucsia (mismo color que el pill de total de
    # paquetes de /residentes, issue 321).
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante
    from app.domain.paquete_service import Destinatario, announce

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    confirmar_ocupante(client.db, papa, staff)
    agregar_ocupante(client.db, apto, "Hijo")  # sin contacto, sin paquetes
    announce(
        client.db, anunciante_telefono="3001234567", anunciante_nombre="Papá",
        destinatario=Destinatario.ocupante(papa.id), staff_actor=staff,
    )
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "01106"})
    assert r.status_code == 200
    assert r.text.count("bg-fuchsia-100") == 1  # solo Papá, Hijo no tiene paquetes
    assert "1 paquete anunciado" in r.text


def test_identificar_torre_apto_lista_badge_pluraliza_con_2_paquetes(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante
    from app.domain.paquete_service import Destinatario, announce

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    confirmar_ocupante(client.db, papa, staff)
    for _ in range(2):
        announce(
            client.db, anunciante_telefono="3001234567", anunciante_nombre="Papá",
            destinatario=Destinatario.ocupante(papa.id), staff_actor=staff,
        )
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "01106"})
    assert r.status_code == 200
    assert ">2</span>" in r.text
    assert "2 paquetes anunciados" in r.text


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


def test_identificar_ocupante_con_bandera_off_pide_autorizacion_por_whatsapp(client):
    # Issue 326 (.scratch/pendientes-cliente): mismo criterio que el camino
    # Teléfono/WhatsApp directo, para un Ocupante con Persona propia.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    hija = agregar_ocupante(client.db, apto, "Hija", telefono="3021112233")
    client.db.commit()

    r = client.get("/announce/identificar-ocupante", params={"ocupante_id": str(hija.id)})
    assert r.status_code == 200
    assert r.text.count('name="accion"') == 1
    assert "wa.me/573021112233?text=" in r.text
    assert "Auto</span>" not in r.text


def test_identificar_ocupante_con_bandera_on_muestra_recibir(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante
    from app.domain.persona_service import buscar_persona_por_telefono, set_autoriza_recepcion_automatica

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    hija = agregar_ocupante(client.db, apto, "Hija", telefono="3021112233")
    persona_hija = buscar_persona_por_telefono(client.db, "3021112233")
    set_autoriza_recepcion_automatica(client.db, persona_hija, True)
    client.db.commit()

    r = client.get("/announce/identificar-ocupante", params={"ocupante_id": str(hija.id)})
    assert r.status_code == 200
    assert r.text.count('name="accion"') == 2
    assert 'value="recibir"' in r.text
    assert "wa.me" not in r.text
    assert "Auto</span>" in r.text


def test_identificar_ocupante_sin_contacto_propio_usa_bandera_del_principal(client):
    # Issue 326: sin Persona propia, se resuelve igual que el Anunciante
    # (`anunciante_para_ocupante`) -- la bandera/contacto del Principal.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante
    from app.domain.persona_service import buscar_persona_por_telefono, set_autoriza_recepcion_automatica

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    confirmar_ocupante(client.db, papa, staff)
    hijo = agregar_ocupante(client.db, apto, "Hijo")  # sin contacto propio
    persona_papa = buscar_persona_por_telefono(client.db, "3001234567")
    set_autoriza_recepcion_automatica(client.db, persona_papa, True)
    client.db.commit()

    r = client.get("/announce/identificar-ocupante", params={"ocupante_id": str(hijo.id)})
    assert r.status_code == 200
    assert r.text.count('name="accion"') == 2
    assert 'value="recibir"' in r.text
    assert "Auto</span>" in r.text


def test_identificar_ocupante_de_baja_muestra_aviso_sin_notificar(client):
    # .scratch/baja-administrativa (ticket 04): mismo criterio que el
    # camino Teléfono/WhatsApp directo, para un Ocupante con Persona propia.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante
    from app.domain.persona_service import buscar_persona_por_telefono, dar_de_baja_administrativa

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    hija = agregar_ocupante(client.db, apto, "Hija", telefono="3021112233")
    persona_hija = buscar_persona_por_telefono(client.db, "3021112233")
    dar_de_baja_administrativa(client.db, persona_hija)
    client.db.commit()

    r = client.get("/announce/identificar-ocupante", params={"ocupante_id": str(hija.id)})
    assert r.status_code == 200
    assert "Sin notificar</span>" in r.text


def test_identificar_ocupante_sin_contacto_propio_ignora_baja_del_principal(client):
    # El aviso "de baja" es del propio Ocupante identificado, NO de
    # `persona_contacto` (que acá es el Principal actuando como proxy de
    # autorización) -- son preguntas distintas, a diferencia de `autoriza_
    # auto` que sí se resuelve por el proxy cuando no hay contacto propio.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante
    from app.domain.persona_service import buscar_persona_por_telefono, dar_de_baja_administrativa

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    confirmar_ocupante(client.db, papa, staff)
    hijo = agregar_ocupante(client.db, apto, "Hijo")  # sin contacto propio
    persona_papa = buscar_persona_por_telefono(client.db, "3001234567")
    dar_de_baja_administrativa(client.db, persona_papa)
    client.db.commit()

    r = client.get("/announce/identificar-ocupante", params={"ocupante_id": str(hijo.id)})
    assert r.status_code == 200
    assert "Sin notificar</span>" not in r.text


def test_identificar_ocupante_sin_contacto_ni_principal_no_ofrece_pildora_ni_whatsapp(client):
    # Issue 326: sin ninguna identidad resoluble (ver `anunciante_para_
    # ocupante` -> None), ni píldora ni WhatsApp -- solo Anunciar, que
    # `announce()` rechazará igual que hoy si se intenta.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")  # pending, no confirmado
    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    r = client.get("/announce/identificar-ocupante", params={"ocupante_id": str(hijo.id)})
    assert r.status_code == 200
    assert r.text.count('name="accion"') == 1
    assert "wa.me" not in r.text
    assert "Auto</span>" not in r.text


def test_identificar_ocupante_con_paquete_anunciado_lo_lista(client):
    # Issue 164 -- mismo listado que el camino de Teléfono/WhatsApp directo,
    # pero para un Ocupante elegido de la lista de residentes de una unidad.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante
    from app.domain.paquete_service import Destinatario, announce

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    hija = agregar_ocupante(client.db, apto, "Hija", telefono="3021112233")
    client.db.commit()
    paquete = announce(
        client.db, anunciante_telefono="3021112233", anunciante_nombre="Hija",
        destinatario=Destinatario.ocupante(hija.id),
    )
    client.db.commit()

    r = client.get("/announce/identificar-ocupante", params={"ocupante_id": str(hija.id)})
    assert r.status_code == 200
    assert paquete.access_code in r.text
    assert f'href="/paquetes?recibir={paquete.id}"' in r.text


def test_identificar_ocupante_sin_contacto_propio_no_lista_nada(client):
    # Un Ocupante sin Persona propia (pending, sin contacto) no tiene
    # identidad con la cual buscar sus paquetes -- lista vacía, sin error.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    agregar_ocupante(client.db, apto, "Mamá", telefono="3001234567")
    hijo = agregar_ocupante(client.db, apto, "Hijo")  # sin contacto propio
    client.db.commit()

    r = client.get("/announce/identificar-ocupante", params={"ocupante_id": str(hijo.id)})
    assert r.status_code == 200
    assert "Ya tiene paquetes en curso" not in r.text


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


# --------------------------------------------------------------------------- #
# GET /announce/identificar-contacto -- resolución en vivo del campo
# "Teléfono o WhatsApp" de "+ Nueva persona" (bug/mejora reportada en vivo,
# .scratch/pendientes-cliente, seguimiento a issue 327).
# --------------------------------------------------------------------------- #
def test_identificar_contacto_sin_sesion_redirige_a_login(client):
    r = client.get("/announce/identificar-contacto", params={"q": "3009998888"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_identificar_contacto_sin_match_pide_nombre(client):
    _login_operador(client)
    r = client.get(
        "/announce/identificar-contacto",
        params={"q": "3009998888", "torre": "TORRE 1", "apartamento": "106"},
    )
    assert r.status_code == 200
    assert 'placeholder="Nombre"' in r.text


def test_identificar_contacto_conocido_sin_unidad_no_pide_nombre(client):
    # Q3 del mini-diseño (.scratch/pendientes-cliente): una Persona conocida
    # SIN Ocupante activo en ningún lado también cuenta como "encontrada".
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3009998888", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get(
        "/announce/identificar-contacto",
        params={"q": "3009998888", "torre": "TORRE 1", "apartamento": "106"},
    )
    assert r.status_code == 200
    assert 'placeholder="Nombre"' not in r.text
    assert "Ya registrado como" in r.text
    assert 'type="hidden" name="nombre" value="ANA"' in r.text
    # Bug corregido en code-review (seguimiento a issue 330): la bandera
    # Auto por defecto es False -- Recibir NO debe aparecer, solo el link
    # de WhatsApp pidiendo autorización (mismo gate que ya tenía la
    # tarjeta de residente existente, issue 326).
    assert 'value="recibir"' not in r.text
    assert "wa.me/573009998888?text=" in r.text
    assert "Auto</span>" not in r.text


def test_identificar_contacto_conocido_sin_unidad_de_baja_muestra_aviso(client):
    # .scratch/baja-administrativa (ticket 04): `persona` ya está resuelta
    # en esta plantilla (`_nueva_persona_datos.html`), sin parámetro nuevo.
    from app.domain.persona_service import dar_de_baja_administrativa, get_or_create_persona

    ana = get_or_create_persona(client.db, "3009998888", "Ana")
    dar_de_baja_administrativa(client.db, ana)
    client.db.commit()
    _login_operador(client)

    r = client.get(
        "/announce/identificar-contacto",
        params={"q": "3009998888", "torre": "TORRE 1", "apartamento": "106"},
    )
    assert r.status_code == 200
    assert "Sin notificar</span>" in r.text


def test_identificar_contacto_conocido_sin_unidad_con_bandera_auto_muestra_recibir(client):
    from app.domain.persona_service import get_or_create_persona, set_autoriza_recepcion_automatica

    ana = get_or_create_persona(client.db, "3009998888", "Ana")
    set_autoriza_recepcion_automatica(client.db, ana, True)
    client.db.commit()
    _login_operador(client)

    r = client.get(
        "/announce/identificar-contacto",
        params={"q": "3009998888", "torre": "TORRE 1", "apartamento": "106"},
    )
    assert r.status_code == 200
    assert 'value="recibir"' in r.text
    assert "wa.me" not in r.text
    assert "Auto</span>" in r.text


def test_identificar_contacto_misma_unidad_bloquea(client):
    # Q2 del mini-diseño: ya es residente de ESTA unidad -- aviso, SIN
    # ningún botón de envío (bug corregido en code-review: antes se
    # deshabilitaban por JS vía `[data-bloqueo-envio]`; ahora el fragmento
    # simplemente no trae ningún `name="accion"`).
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    agregar_ocupante(client.db, apto, "Ana", telefono="3009998888")
    client.db.commit()

    r = client.get(
        "/announce/identificar-contacto",
        params={"q": "3009998888", "torre": "TORRE 1", "apartamento": "106"},
    )
    assert r.status_code == 200
    assert "ya es residente de esta unidad" in r.text
    assert 'placeholder="Nombre"' not in r.text
    assert 'name="accion"' not in r.text


def test_identificar_contacto_otra_unidad_premarca_mudanza(client):
    # Q1 del mini-diseño: ya es residente de OTRA unidad -- checkbox de
    # mudanza pre-marcado, con la unidad DESTINO (la que se está
    # identificando ahora), no la de origen. Bandera Auto en False por
    # defecto -- mismo bug corregido que en "conocido_sin_unidad": Recibir
    # no debe aparecer, solo el link de WhatsApp.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto_otra = resolver_apartamento(client.db, "TORRE 2", "202")
    agregar_ocupante(client.db, apto_otra, "Ana", telefono="3009998888")
    client.db.commit()

    r = client.get(
        "/announce/identificar-contacto",
        params={"q": "3009998888", "torre": "TORRE 1", "apartamento": "106"},
    )
    assert r.status_code == 200
    assert "TORRE 2 · Apto 202" in r.text  # unidad de origen, en el aviso
    assert 'name="mover_de_otra_unidad" value="1" checked' in r.text
    assert "Mudar este residente a TORRE 1 · Apto 106" in r.text  # unidad destino
    assert 'value="recibir"' not in r.text
    assert "wa.me/573009998888?text=" in r.text


def test_identificar_contacto_otra_unidad_con_bandera_auto_muestra_recibir(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante
    from app.domain.persona_service import buscar_persona_por_telefono, set_autoriza_recepcion_automatica

    _login_operador(client)
    apto_otra = resolver_apartamento(client.db, "TORRE 2", "202")
    agregar_ocupante(client.db, apto_otra, "Ana", telefono="3009998888")
    persona_ana = buscar_persona_por_telefono(client.db, "3009998888")
    set_autoriza_recepcion_automatica(client.db, persona_ana, True)
    client.db.commit()

    r = client.get(
        "/announce/identificar-contacto",
        params={"q": "3009998888", "torre": "TORRE 1", "apartamento": "106"},
    )
    assert r.status_code == 200
    assert 'value="recibir"' in r.text
    assert "wa.me" not in r.text
    assert "Auto</span>" in r.text


def test_identificar_contacto_valor_vacio_pide_nombre(client):
    _login_operador(client)
    r = client.get(
        "/announce/identificar-contacto",
        params={"q": "", "torre": "TORRE 1", "apartamento": "106"},
    )
    assert r.status_code == 200
    assert 'placeholder="Nombre"' in r.text


def test_identificar_contacto_sin_match_nunca_muestra_recibir(client):
    # Bug corregido en code-review (seguimiento a issue 330): una persona
    # genuinamente NUEVA nunca pudo autorizar nada de antemano -- Recibir
    # no debe aparecer nunca en el estado "nuevo", ni el link de WhatsApp
    # (no hay a quién pedirle: nadie resolvió todavía).
    _login_operador(client)
    r = client.get(
        "/announce/identificar-contacto",
        params={"q": "3009998888", "torre": "TORRE 1", "apartamento": "106"},
    )
    assert r.status_code == 200
    assert 'value="anunciar"' in r.text
    assert 'value="recibir"' not in r.text
    assert "wa.me" not in r.text


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
    assert "activa la opción de mudarlo" in r.text
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


def test_nueva_persona_sin_nombre_pero_contacto_ya_conocido_usa_su_nombre_real(client):
    # Bug/mejora reportada en vivo (.scratch/pendientes-cliente, seguimiento
    # a issue 327): antes esto fallaba con 400 "Escribe el nombre..." aunque
    # el contacto YA fuera una Persona conocida (sin Ocupante activo en
    # ningún lado) -- `agregar_ocupante` ya iba a ignorar cualquier nombre
    # tecleado a favor del registrado, así que exigirlo era un obstáculo
    # falso. Ahora coincide con lo que muestra en vivo `_identificar_
    # unidad.html` (campo Nombre oculto cuando el contacto ya resuelve).
    from app.domain.persona_service import get_or_create_persona

    get_or_create_persona(client.db, "3009998888", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.post(
        "/announce",
        data={"torre": "TORRE 1", "apartamento": "106", "contacto": "3009998888"},
    )
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "ANA"


def test_nueva_persona_mueve_sin_nombre_no_falla(client):
    # Mismo bug que el anterior, para el camino de "mudanza" (contacto ya
    # es Ocupante activo de OTRA unidad).
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
            "contacto": "3021112233", "mover_de_otra_unidad": "1",
        },
    )
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()
    assert p.recipient_name == "HIJA"


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


def test_recibir_telefono_directo_picker_expone_residentes_por_unidad(client):
    # Issue 127: el picker de Recibir en /announce también necesita
    # `residentes_por_unidad` (antes solo lo pasaba packages.py) -- mismo
    # mecanismo que /paquetes.
    import json
    import re

    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Jesus Villalobos", telefono="3033333333")
    client.db.commit()

    r = client.post(
        "/announce",
        data={"telefono": "3001234567", "nombre": "Ana", "accion": "recibir"},
    )
    assert r.status_code == 200

    client.db.expire_all()
    p = client.db.query(Paquete).one()

    match = re.search(
        rf'id="residentes-unidad-recibir-{p.id}">(.*?)</script>', r.text, re.S
    )
    assert match is not None
    data = json.loads(match.group(1))
    assert data["TORRE 1"]["101"] == ["JESUS VILLALOBOS"]


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
    # `test_identificar_nombre_del_fragmento_no_lleva_autofocus`). Desde el
    # issue 284 esto ya no depende del modal -- este campo nunca lleva
    # autofocus (vista exclusiva de staff), pero el test se mantiene: sigue
    # siendo cierto y sigue siendo la razón original por la que este caso
    # puntual importa.
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
    assert "Nueva persona" in r.text
    assert "data-ocupante-id" in r.text  # lista de residentes, no la tarjeta directa
    assert "Ya registrado" not in r.text  # esa etiqueta es de la tarjeta directa de _identificar.html


def test_identificar_telefono_con_coresidentes_no_muestra_badges_en_la_lista(client):
    # Issue 131, retroalimentación en vivo 2026-08-18: mismo criterio que
    # Recibir en /paquetes (issue 125) -- la lista de residentes de una
    # unidad muestra SOLO el nombre, sin badge "Principal" ni "Anunciante".
    # Quién es Principal/quién llamó sigue siendo información REAL que la
    # app usa (preselección de la tarjeta, ver
    # test_identificar_telefono_con_coresidentes_preselecciona_a_quien_llama)
    # -- solo deja de mostrarse como badge en esta lista.
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
    assert "MAMÁ" in r.text
    assert "HIJO" in r.text
    # "Anunciante" ya no aparece en absoluto (era solo el badge, sin otro
    # uso legítimo en este fragmento). "Principal" SÍ sigue apareciendo
    # -- pero como subtítulo de la tarjeta preseleccionada de Mamá
    # (`<p>...Principal</p>`, `_persona_resuelta.html`), NUNCA como el
    # badge de la lista (`<span>...Principal</span>`, ya retirado).
    assert "Anunciante" not in r.text
    assert ">Principal</span>" not in r.text
    assert ">Principal</p>" in r.text


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
    # Mamá no autoriza recepción automática (default False) -- Recibir se
    # reemplaza por el link de WhatsApp (issue 326, corregido en 330 para
    # que "+ Nueva persona" respete el mismo gate y no aporte un
    # ">Recibir<" incondicional que enmascarara esto).
    assert "wa.me/573001234567?text=" in r.text


def test_identificar_telefono_con_coresidentes_preseleccionado_bandera_off(client):
    # Issue 326 (.scratch/pendientes-cliente): la tarjeta preseleccionada
    # (issue anterior, ticket 10) también respeta la bandera -- no es un
    # camino aparte, comparte el mismo macro `tarjeta_persona_resuelta`.
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
    # 1 del form colapsado "+ Nueva persona" (solo Anunciar -- sin contacto
    # tecleado todavía, estado "nuevo", corregido en code-review junto con
    # el gap de consistencia 326/330) + 1 de la tarjeta preseleccionada
    # (solo Anunciar -- bandera OFF por default).
    assert r.text.count('name="accion"') == 2
    assert "wa.me/573001234567?text=" in r.text


def test_identificar_telefono_con_coresidentes_preseleccionado_bandera_on(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante
    from app.domain.persona_service import buscar_persona_por_telefono, set_autoriza_recepcion_automatica

    staff = _login_operador(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "106")
    mama = agregar_ocupante(client.db, apto, "Mamá", telefono="3001234567")
    confirmar_ocupante(client.db, mama, staff)
    agregar_ocupante(client.db, apto, "Hijo")
    persona_mama = buscar_persona_por_telefono(client.db, "3001234567")
    set_autoriza_recepcion_automatica(client.db, persona_mama, True)
    client.db.commit()

    r = client.get("/announce/identificar", params={"q": "3001234567"})
    assert r.status_code == 200
    # 3 = 1 del form "+ Nueva persona" (solo Anunciar, estado "nuevo" -- su
    # propia resolución en vivo es independiente de que Mamá esté
    # preseleccionada en la OTRA tarjeta) + 2 de la tarjeta preseleccionada
    # (Anunciar + Recibir -- bandera ON).
    assert r.text.count('name="accion"') == 3
    assert "Auto</span>" in r.text


def test_identificar_whatsapp_con_coresidentes_muestra_la_lista(client):
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
    # Sin badge "Anunciante" (issue 131) -- ver test dedicado del camino
    # Teléfono para la cobertura completa de la ausencia del badge.


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


def test_coresidentes_notificacion_cae_al_principal_no_a_quien_llamo(client):
    """Issue 163 (.scratch/pendientes-cliente, revierte el ticket 10 de
    .scratch/ocupante-principal-escenarios): "siempre debe haber un
    número... responsable, [el] del principal del apartamento" -- cuando el
    destinatario elegido no tiene contacto propio, la notificación
    (recipient_phone) cae al Principal CONFIRMADO de la unidad, no a quien
    llamó (a menos que sean la misma persona)."""
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
    assert p.recipient_phone == "+573001234567"  # el de Mamá (principal), no el de Papá (quien llamó)


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
