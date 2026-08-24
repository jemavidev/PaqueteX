# -*- coding: utf-8 -*-
"""
Capa web — `/residentes` (buscar + ver/editar cliente, ticket 02).

Comportamiento observable por HTTP: exige sesión de staff (CUALQUIER rol);
buscar por teléfono o nombre encuentra al cliente correcto; editar es parcial y
opera sobre la Persona de OTRO (no la propia sesión); email inválido rechaza
sin persistir; id inexistente -> 404.
"""

from app.domain.persona import Persona
from app.domain.persona_service import get_or_create_persona, get_or_create_persona_por_whatsapp
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario, Usuario

_PW = "Contrasena1"


def _login_operador(client, email="op@club.com"):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, email, "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def _confirmar(client, ocupante):
    """Confirma `ocupante` por staff (ticket 06) -- promueve a principal si
    su Apartamento todavía no tiene uno. Reusa el ADMIN que `_login_operador`
    ya crea internamente."""
    from app.domain.ocupante_service import confirmar_ocupante
    from app.domain.usuario import Usuario

    admin = client.db.query(Usuario).filter(Usuario.rol == RolUsuario.ADMIN).one()
    confirmar_ocupante(client.db, ocupante, admin)
    client.db.commit()


def test_sin_sesion_redirige_al_login_de_staff_no_al_de_cliente(client):
    # Confirma el gate correcto: /residentes es STAFF, no cliente, pese a
    # empezar con "/customer" como substring de "/customers".
    r = client.get("/residentes", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")
    assert "customer/login" not in r.headers["location"]


def test_buscar_por_telefono_encuentra_al_cliente(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"q": "3001234567"})
    assert r.status_code == 200
    assert "ANA" in r.text
    assert str(p.id) in r.text


def test_buscar_por_nombre_encuentra_al_cliente(client):
    get_or_create_persona(client.db, "3001234567", "Ana Gómez")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"q": "gómez"})
    assert r.status_code == 200
    assert "ANA GÓMEZ" in r.text


# --------------------------------------------------------------------------- #
# Grupo 17 (Ronda 2) — búsqueda extendida.
# --------------------------------------------------------------------------- #
def test_buscar_por_torre_encuentra_a_los_residentes_de_esa_torre(client):
    from app.domain.apartamento_service import declare_unit, resolver_apartamento

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"q": "TORRE 1"})
    assert r.status_code == 200
    assert "ANA" in r.text


def test_buscar_por_apartamento_encuentra_al_residente(client):
    from app.domain.apartamento_service import declare_unit, resolver_apartamento

    apto = resolver_apartamento(client.db, "TORRE 2", "202")
    declare_unit(client.db, apto, [("3001234567", "Ana")])
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"q": "202"})
    assert r.status_code == 200
    assert "ANA" in r.text


def test_buscar_por_nombre_de_ocupante_sin_telefono_encuentra_al_principal(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    ana = agregar_ocupante(client.db, apto, "Ana", "3001234567")
    agregar_ocupante(client.db, apto, "Hijo Menor")  # sin teléfono
    client.db.commit()
    _login_operador(client)
    _confirmar(client, ana)  # Ana confirmada como principal (ticket 06)

    r = client.get("/residentes", params={"q": "Hijo Menor"})
    assert r.status_code == 200
    assert "ANA" in r.text  # resuelve a la Persona principal de esa unidad


def test_buscar_por_telefono_de_ocupante_no_principal(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")  # principal
    agregar_ocupante(client.db, apto, "Hija", "3019999999")  # con teléfono propio

    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"q": "3019999999"})
    assert r.status_code == 200
    assert "HIJA" in r.text  # tiene su propia Persona/ficha (tiene teléfono)


def test_resultados_no_se_duplican_si_varios_criterios_coinciden(client):
    # Con catálogo cerrado (`.scratch/apartamento-catalogo-confirmacion`,
    # ticket 03) la Torre ya no puede ser texto libre ("Gómez") -- el
    # escenario de "dos criterios distintos resuelven a la misma Persona" se
    # preserva vía Persona.nombre + Ocupante.nombre (dos ramas de búsqueda
    # distintas, `_buscar_residentes`) en vez de Persona.nombre + Torre.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana Gómez", "3001234567")  # principal
    agregar_ocupante(client.db, apto, "Hijo Gómez")  # sin teléfono, mismo apellido
    client.db.commit()
    _login_operador(client)

    # "gómez" coincide con el nombre de la Persona (Ana, directo) Y con el
    # nombre del Ocupante sin teléfono (que resuelve al mismo principal) --
    # debe aparecer una sola vez, no duplicada (una sola fila). El nombre en
    # sí aparece más de una vez POR fila (columna Nombre + aria-labels de los
    # íconos de contacto, issue 67), así que se cuenta el link a la ficha
    # -- aparece 3 veces por fila (columna Nombre + 👫 de [[160]], comparte
    # unidad con "Hijo Gómez" + botón "Ver ficha"), 6 si la fila estuviera
    # duplicada.
    from app.domain.persona import Persona

    ana = client.db.query(Persona).filter(Persona.nombre == "ANA GÓMEZ").one()
    r = client.get("/residentes", params={"q": "gómez"})
    assert r.status_code == 200
    assert r.text.count(f"/residentes/{ana.id}") == 3


# --------------------------------------------------------------------------- #
# Pedido del cliente (.scratch/pendientes-cliente): sin término de búsqueda,
# la vista lista TODOS los clientes (antes no mostraba nada), con todos los
# campos de Persona en una tabla, paginado.
# --------------------------------------------------------------------------- #
def test_residentes_sin_busqueda_lista_todos_los_clientes(client):
    get_or_create_persona(client.db, "3001234567", "Ana")
    get_or_create_persona(client.db, "3007654321", "Beto")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes")
    assert r.status_code == 200
    assert "ANA" in r.text
    assert "BETO" in r.text


def test_residentes_sin_residentes_registrados_muestra_estado_vacio(client):
    _login_operador(client)

    r = client.get("/residentes")
    assert r.status_code == 200
    assert "sin residentes todavía" in r.text.lower()


def test_residentes_sin_busqueda_pagina_cuando_hay_muchos_clientes(client):
    for i in range(25):
        get_or_create_persona(client.db, f"300000{i:04d}", f"Cliente{i:02d}")
    client.db.commit()
    _login_operador(client)

    pagina_1 = client.get("/residentes")
    assert pagina_1.status_code == 200
    assert 'aria-label="Paginación"' in pagina_1.text

    pagina_2 = client.get("/residentes", params={"pagina": 2})
    assert pagina_2.status_code == 200
    # Ningún cliente debería repetirse entre las 2 páginas (ordenadas por
    # nombre, sin solape).
    assert pagina_1.text != pagina_2.text


def test_peticion_en_vivo_devuelve_solo_el_fragmento(client):
    # Issue 173 (.scratch/pendientes-cliente): la barra de búsqueda de
    # /residentes activó la misma búsqueda en vivo que ya tenía /paquetes
    # (mismo mecanismo, ver `test_peticion_en_vivo_devuelve_solo_el_fragmento`
    # de test_packages.py) -- el fetch en vivo marca su petición con el header
    # X-Requested-With: fetch, la ruta responde SOLO paginación+tabla (sin el
    # layout de la página completa), mientras que una carga normal (sin el
    # header) sigue devolviendo la página entera.
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    normal = client.get("/residentes")
    assert normal.status_code == 200
    assert "<h1" in normal.text
    assert "ANA" in normal.text

    fragmento = client.get("/residentes", headers={"X-Requested-With": "fetch"})
    assert fragmento.status_code == 200
    assert "<h1" not in fragmento.text
    assert "<html" not in fragmento.text
    assert "ANA" in fragmento.text


def test_busqueda_en_vivo_filtra_por_termino(client):
    # Mismo fetch en vivo, esta vez con `q` -- confirma que el fragmento
    # respeta el filtro igual que la carga normal (issue 173).
    get_or_create_persona(client.db, "3001234567", "Ana")
    get_or_create_persona(client.db, "3007654321", "Beto")
    client.db.commit()
    _login_operador(client)

    fragmento = client.get("/residentes", params={"q": "ana"}, headers={"X-Requested-With": "fetch"})
    assert fragmento.status_code == 200
    assert "ANA" in fragmento.text
    assert "BETO" not in fragmento.text


def test_fragmento_en_vivo_incluye_toggle_de_eliminar_para_admin(client):
    # Issue 173: el toggle de "Eliminar residente" pasó de bindeado directo
    # (querySelectorAll + addEventListener una sola vez al cargar) a delegado
    # sobre `document` -- sigue funcionando después de que el contenedor de
    # resultados se reemplace por completo (innerHTML) en cada búsqueda en
    # vivo, algo que un binding directo no sobrevive. Cubre lo que SÍ es
    # observable por HTTP: el fragmento en vivo sigue trayendo el
    # data-open/data-close y el modal de confirmación para el ADMIN.
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_admin(client)

    fragmento = client.get("/residentes", headers={"X-Requested-With": "fetch"})
    assert fragmento.status_code == 200
    assert f'data-open="modal-eliminar-{p.id}"' in fragmento.text
    assert f'id="modal-eliminar-{p.id}"' in fragmento.text


# --------------------------------------------------------------------------- #
# Issue 174 (.scratch/pendientes-cliente): botones "Listar principales" /
# "Agrupar por apartamento" / "Limpiar filtros".
# --------------------------------------------------------------------------- #
def test_botones_de_vista_siempre_visibles_sin_importar_la_vista_activa(client):
    # Pedido explícito: los 3 botones se muestran SIEMPRE, no solo el que
    # aplica -- el que no aplica queda inactivo/gris, no oculto.
    _login_operador(client)
    for params in ({}, {"vista": "principales"}, {"vista": "agrupado"}):
        r = client.get("/residentes", params=params)
        assert "Listar principales" in r.text
        assert "Agrupar por apartamento" in r.text
        assert "Limpiar filtros" in r.text


def test_boton_de_vista_activo_refleja_la_vista_actual(client):
    _login_operador(client)
    r = client.get("/residentes", params={"vista": "agrupado"})
    assert 'data-vista-boton="agrupado" aria-pressed="true"' in r.text
    assert 'data-vista-boton="principales" aria-pressed="false"' in r.text


def test_limpiar_filtros_deshabilitado_sin_filtros_activos(client):
    _login_operador(client)
    r = client.get("/residentes")
    assert "data-vista-reset disabled" in r.text


def test_limpiar_filtros_habilitado_con_busqueda_activa(client):
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)
    r = client.get("/residentes", params={"q": "ana"})
    assert "data-vista-reset disabled" not in r.text


def test_listar_principales_filtra_solo_principales(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    ana = agregar_ocupante(client.db, apto, "Ana", "3001234567")
    agregar_ocupante(client.db, apto, "Beto", "3007654321")  # secundario, con teléfono propio
    client.db.commit()
    _login_operador(client)
    _confirmar(client, ana)  # Ana confirmada como principal (ticket 06) -- `agregar_ocupante` ya no promueve solo

    todos = client.get("/residentes")
    assert "ANA" in todos.text and "BETO" in todos.text

    principales = client.get("/residentes", params={"vista": "principales"})
    assert principales.status_code == 200
    assert "ANA" in principales.text
    assert "BETO" not in principales.text


def test_listar_principales_combinado_con_busqueda(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    ana = agregar_ocupante(client.db, apto, "Ana Gómez", "3001234567")
    agregar_ocupante(client.db, apto, "Beto Gómez", "3007654321")  # secundario
    client.db.commit()
    _login_operador(client)
    _confirmar(client, ana)  # Ana confirmada como principal

    r = client.get("/residentes", params={"q": "gómez", "vista": "principales"})
    assert r.status_code == 200
    assert "ANA GÓMEZ" in r.text
    assert "BETO GÓMEZ" not in r.text


def test_agrupar_por_apartamento_trae_a_todos_aunque_la_busqueda_matcheo_a_uno(client):
    # Issue 174, pedido explícito: "incluso si ya se realizo una busqueda,
    # con el fin de saber todos los integrantes de un mismo apartamento".
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")  # principal
    agregar_ocupante(client.db, apto, "Beto", "3007654321")  # secundario
    client.db.commit()
    _login_operador(client)

    # Búsqueda normal por "Ana" no trae a Beto.
    solo_ana = client.get("/residentes", params={"q": "Ana"})
    assert "ANA" in solo_ana.text
    assert "BETO" not in solo_ana.text

    # Con vista=agrupado, el MISMO término trae la unidad completa.
    agrupado = client.get("/residentes", params={"q": "Ana", "vista": "agrupado"})
    assert agrupado.status_code == 200
    assert "ANA" in agrupado.text
    assert "BETO" in agrupado.text
    assert "T 01 - APT 101" in agrupado.text


def test_agrupar_por_apartamento_sin_busqueda_agrupa_todas_las_unidades(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto1, "Ana", "3001234567")
    apto2 = resolver_apartamento(client.db, "TORRE 2", "202")
    agregar_ocupante(client.db, apto2, "Carlos", "3009999999")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"vista": "agrupado"})
    assert r.status_code == 200
    assert "ANA" in r.text
    assert "CARLOS" in r.text
    assert "T 01 - APT 101" in r.text
    assert "T 02 - APT 202" in r.text


def test_agrupar_por_apartamento_incluye_sin_apartamento_asignado(client):
    # Personas sin apartamento no arman grupo, pero tampoco desaparecen --
    # se listan en su propia sección.
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"vista": "agrupado"})
    assert r.status_code == 200
    assert "Sin apartamento asignado" in r.text
    assert "ANA" in r.text


def test_agrupar_por_apartamento_pagina_por_unidad(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    # Catálogo cerrado (`.scratch/apartamento-catalogo-confirmacion`) -- no
    # cualquier número sirve, hay que usar unidades reales. TORRE 1 tiene 6
    # unidades por piso en los pisos 1-6 (`_TORRE_CHICA`, migración
    # 0021_seed_catalogo_apartamentos), de sobra para 25.
    ternas = [(piso, i) for piso in range(1, 7) for i in range(1, 7)]
    for idx, (piso, i) in enumerate(ternas[:25]):
        apto = resolver_apartamento(client.db, "TORRE 1", str(piso * 100 + i))
        agregar_ocupante(client.db, apto, f"Residente{idx:02d}", f"300000{idx:04d}")
    client.db.commit()
    _login_operador(client)

    pagina_1 = client.get("/residentes", params={"vista": "agrupado"})
    assert pagina_1.status_code == 200
    assert 'aria-label="Paginación"' in pagina_1.text

    pagina_2 = client.get("/residentes", params={"vista": "agrupado", "pagina": 2})
    assert pagina_2.status_code == 200
    assert pagina_1.text != pagina_2.text


def test_fragmento_en_vivo_respeta_vista_agrupado(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", "3001234567")
    agregar_ocupante(client.db, apto, "Beto", "3007654321")
    client.db.commit()
    _login_operador(client)

    fragmento = client.get("/residentes", params={"vista": "agrupado"}, headers={"X-Requested-With": "fetch"})
    assert fragmento.status_code == 200
    assert "<h1" not in fragmento.text
    assert "ANA" in fragmento.text
    assert "BETO" in fragmento.text


# --------------------------------------------------------------------------- #
# Issue 67 (.scratch/pendientes-cliente): tabla simplificada con íconos de
# contacto (WhatsApp/llamada) en vez de las 12 columnas del issue 66.
# --------------------------------------------------------------------------- #
def test_tabla_de_residentes_incluye_link_de_whatsapp_por_usuario(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    p.whatsapp_usuario = "ana.whats"
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes")
    # Prioriza el username (issue 67) -- NO arma el link con el teléfono
    # cuando hay username.
    assert "https://wa.me/ana.whats" in r.text
    assert "https://wa.me/573001234567" not in r.text


def test_tabla_de_residentes_incluye_link_de_whatsapp_por_telefono_sin_usuario(client):
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes")
    assert "https://wa.me/573001234567" in r.text


def test_tabla_de_residentes_incluye_link_de_llamada(client):
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes")
    assert "tel:+573001234567" in r.text


def test_tabla_de_residentes_sin_telefono_no_filtra_none(client):
    # Persona solo-WhatsApp (ADR-0007): sin Teléfono, la columna debe mostrar
    # "N/D" en vez del literal "None", y el ícono de Llamar debe quedar
    # inactivo (sin armar un link roto "tel:None").
    get_or_create_persona_por_whatsapp(client.db, "ana.whats", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes")
    assert "tel:None" not in r.text
    assert ">None<" not in r.text
    assert "N/D" in r.text
    assert "Sin teléfono registrado" in r.text


def test_tabla_de_residentes_ya_no_lista_los_campos_que_pasaron_a_la_ficha(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    p.email = "ana@x.com"
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes")
    assert "ana@x.com" not in r.text


def test_tabla_de_residentes_excluye_clientes_eliminados(client):
    from app.domain.persona_service import anonimizar_persona

    activo = get_or_create_persona(client.db, "3001234567", "Ana")
    eliminado = get_or_create_persona(client.db, "3007654321", "Beto")
    anonimizar_persona(client.db, eliminado)
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes")
    assert "ANA" in r.text
    assert "Cliente eliminado" not in r.text


def test_operador_ve_y_edita_la_ficha_de_otra_persona(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{p.id}")
    assert r.status_code == 200
    assert "ANA" in r.text


def test_editar_guarda_parcialmente(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    client.post(f"/residentes/{p.id}", data={"email": "ana@x.com"})
    client.db.expire_all()
    p2 = client.db.get(Persona, p.id)
    assert p2.nombre == "ANA"  # no enviado, sigue igual
    assert p2.email == "ana@x.com"


def test_staff_edita_el_usuario_de_whatsapp(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    client.post(f"/residentes/{p.id}", data={"whatsapp_usuario": "ana.whats"})
    client.db.expire_all()
    assert client.db.get(Persona, p.id).whatsapp_usuario == "ana.whats"


def test_staff_activa_recepcion_automatica(client):
    # Issue 169 (.scratch/pendientes-cliente): antes exclusivo de
    # /mis-datos -- staff solo veía el badge de solo lectura, sin control
    # para tocarlo.
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)
    assert client.db.get(Persona, p.id).autoriza_recepcion_automatica is False

    client.post(f"/residentes/{p.id}", data={"autoriza_recepcion_automatica": "on"})
    client.db.expire_all()
    assert client.db.get(Persona, p.id).autoriza_recepcion_automatica is True


def test_staff_desactiva_recepcion_automatica_al_omitir_el_checkbox(client):
    # Un checkbox HTML desmarcado no manda su `name` -- "ausente" en el
    # form ES "no autoriza", no "no tocar" (mismo contrato que /mis-datos).
    from app.domain.persona_service import set_autoriza_recepcion_automatica

    p = get_or_create_persona(client.db, "3001234567", "Ana")
    set_autoriza_recepcion_automatica(client.db, p, True)
    client.db.commit()
    _login_operador(client)

    client.post(f"/residentes/{p.id}", data={})
    client.db.expire_all()
    assert client.db.get(Persona, p.id).autoriza_recepcion_automatica is False


def test_ficha_muestra_el_checkbox_de_recepcion_automatica_marcado(client):
    from app.domain.persona_service import set_autoriza_recepcion_automatica

    p = get_or_create_persona(client.db, "3001234567", "Ana")
    set_autoriza_recepcion_automatica(client.db, p, True)
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{p.id}")
    assert r.status_code == 200
    assert 'name="autoriza_recepcion_automatica" checked' in r.text


def test_staff_borra_el_usuario_de_whatsapp_ya_seteado(client):
    # Issue 69: bug real -- una vez seteado, el campo no se podía vaciar
    # (el form manda "" y antes se trataba como "no tocar").
    from app.domain.persona_service import update_datos_personales

    p = get_or_create_persona(client.db, "3001234567", "Ana")
    update_datos_personales(client.db, p, whatsapp_usuario="ana.whats")
    client.db.commit()
    _login_operador(client)

    client.post(f"/residentes/{p.id}", data={"whatsapp_usuario": ""})
    client.db.expire_all()
    assert client.db.get(Persona, p.id).whatsapp_usuario is None


def test_usuario_de_whatsapp_invalido_rechaza_sin_persistir(client):
    # Issue 67: ya no es texto libre -- arma un link real, así que se valida
    # contra las reglas de username de WhatsApp (letras/números/puntos/_).
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.post(f"/residentes/{p.id}", data={"whatsapp_usuario": "ana con espacios"})
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Persona, p.id).whatsapp_usuario is None


def test_staff_edita_el_telefono_del_cliente(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    client.post(f"/residentes/{p.id}", data={"telefono": "3009998877"})
    client.db.expire_all()
    assert client.db.get(Persona, p.id).telefono == "+573009998877"


def test_staff_edita_telefono_repetido_rechaza_sin_persistir(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    get_or_create_persona(client.db, "3009998877", "Beto")
    client.db.commit()
    _login_operador(client)

    r = client.post(f"/residentes/{p.id}", data={"telefono": "3009998877"})
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Persona, p.id).telefono == "+573001234567"  # sin cambios


def test_email_invalido_rechaza_sin_persistir(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.post(f"/residentes/{p.id}", data={"email": "no-es-email"})
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Persona, p.id).email is None


def test_persona_inexistente_da_404(client):
    _login_operador(client)
    import uuid

    r = client.get(f"/residentes/{uuid.uuid4()}")
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Eliminar cliente (ticket 03) — solo ADMIN.
# --------------------------------------------------------------------------- #
def _login_admin(client, email="admin@club.com"):
    create_initial_admin(client.db, email, "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": email, "password": _PW})


def test_admin_elimina_anonimiza_al_cliente(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_admin(client)

    r = client.post(f"/residentes/{p.id}/eliminar", follow_redirects=False)
    assert r.status_code == 303

    client.db.expire_all()
    p2 = client.db.get(Persona, p.id)
    assert p2.nombre == "Cliente eliminado"
    assert p2.telefono.startswith("DEL-")
    assert p2.eliminado_en is not None


def test_operador_no_puede_eliminar(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.post(f"/residentes/{p.id}/eliminar")
    assert r.status_code == 403

    client.db.expire_all()
    p2 = client.db.get(Persona, p.id)
    assert p2.nombre == "ANA"  # sin cambios


def test_eliminar_sin_sesion_redirige_a_login_de_staff(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    r = client.post(f"/residentes/{p.id}/eliminar", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_eliminar_id_inexistente_da_404(client):
    _login_admin(client)
    import uuid

    r = client.post(f"/residentes/{uuid.uuid4()}/eliminar")
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Notificaciones desde la ficha de staff (issue 67: matriz completa Canal ×
# Evento, reemplaza el toggle simplificado de SMS que tenía antes esta ficha
# -- ver ticket 02 de notification-preferences para el estado anterior).
# --------------------------------------------------------------------------- #
def test_staff_desactiva_un_canal_del_cliente_via_la_matriz(client):
    from app.domain.paquete import EstadoPaquete
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    client.post(f"/residentes/{p.id}/notificaciones", data={})  # todo ausente = todo apagado

    client.db.expire_all()
    assert preferencia_activa(
        client.db, p.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO
    ) is False


def test_staff_activa_canales_puntuales_via_la_matriz(client):
    from app.domain.paquete import EstadoPaquete
    from app.domain.preferencia_notificacion import CanalNotificacion
    from app.domain.preferencia_notificacion_service import preferencia_activa

    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    client.post(
        f"/residentes/{p.id}/notificaciones",
        data={"pref_SMS_ANUNCIADO": "on", "pref_EMAIL_RECIBIDO": "on"},
    )

    client.db.expire_all()
    assert preferencia_activa(
        client.db, p.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO
    ) is True
    assert preferencia_activa(
        client.db, p.id, CanalNotificacion.EMAIL, EstadoPaquete.RECIBIDO
    ) is True
    # No marcado -- queda apagado (mismo contrato que un checkbox HTML).
    assert preferencia_activa(
        client.db, p.id, CanalNotificacion.SMS, EstadoPaquete.ENTREGADO
    ) is False


def test_ficha_muestra_las_4_tabs(client):
    # Issue 68: Dirección se separó de Datos (picker de Torre/Piso/Apto), y
    # la tab de Ocupantes se renombró de "Apartamento y Residentes" a
    # "Residentes" (data-tab sigue siendo el mismo id interno).
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{p.id}")
    assert r.status_code == 200
    assert 'data-tab="datos"' in r.text
    assert 'data-tab="direccion"' in r.text
    assert 'data-tab="notif"' in r.text
    assert 'data-tab="residentes"' in r.text


def test_ficha_query_param_tab_abre_directo_en_esa_tab(client):
    # Conversación 2026-08-17 (pedido explícito): un link externo puede
    # entrar directo a la tab "Residentes" en vez de "Datos".
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{p.id}", params={"tab": "residentes"})
    assert r.status_code == 200
    assert "activar('residentes')" in r.text


def test_ficha_tab_desconocida_cae_al_default(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{p.id}", params={"tab": "no-existe"})
    assert r.status_code == 200
    assert "activar('datos')" in r.text


# --------------------------------------------------------------------------- #
# Issue 68 (.scratch/pendientes-cliente): batch de correcciones sobre [[67]].
# --------------------------------------------------------------------------- #
def test_ficha_no_muestra_texto_de_ayuda_del_whatsapp_usuario(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{p.id}")
    assert "se usa para el ícono de chat" not in r.text


def test_ficha_muestra_el_whatsapp_usuario_con_arroba(client):
    from app.domain.persona_service import update_datos_personales

    p = get_or_create_persona(client.db, "3001234567", "Ana")
    update_datos_personales(client.db, p, whatsapp_usuario="ana.whats")
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{p.id}")
    assert 'value="@ana.whats"' in r.text


def test_ficha_muestra_badge_de_recepcion_automatica(client):
    from app.domain.persona_service import set_autoriza_recepcion_automatica

    p = get_or_create_persona(client.db, "3001234567", "Ana")
    set_autoriza_recepcion_automatica(client.db, p, True)
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{p.id}")
    assert "Recepción automática" in r.text


def test_ficha_no_muestra_badge_cuando_recepcion_es_manual(client):
    # Issue 69: el estado "default" (Manual) ya no lleva badge -- solo Auto.
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{p.id}")
    assert "Recepción manual" not in r.text


def test_ficha_muestra_badge_de_residente_principal(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    client.db.commit()
    _login_operador(client)
    _confirmar(client, papa)

    persona = client.db.get(Persona, papa.persona_id)
    r = client.get(f"/residentes/{persona.id}")
    assert "Residente principal</span>" in r.text


def test_ficha_de_residente_secundario_no_muestra_badge_pero_si_fondo_rojizo(client):
    # Issue 69: Secundario ya no lleva badge -- el fondo rojizo de cada
    # tab-panel es la señal (ver `bg-red-50` condicional en detail.html).
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    client.db.commit()
    _login_operador(client)
    _confirmar(client, papa)
    hijo = agregar_ocupante(client.db, apto, "Hijo", telefono="3007654321")
    client.db.commit()
    _confirmar(client, hijo)

    persona = client.db.get(Persona, hijo.persona_id)
    r = client.get(f"/residentes/{persona.id}")
    assert "Residente secundario" not in r.text
    assert "border-l-4 border-red-400" in r.text


def test_ficha_sin_ocupante_no_muestra_badge_principal_ni_acento(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{p.id}")
    assert "Residente principal</span>" not in r.text
    assert "border-l-4 border-red-400" not in r.text


def test_lista_muestra_badge_principal_no_badge_secundario_ni_acento(client):
    # Issue 168 (.scratch/pendientes-cliente, revierte issue 71): el acento
    # rojo a la izquierda para "Secundario" se retiró -- pedido explícito
    # ("remueve esa marca"), confundía sin explicación visible de qué
    # significaba.
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    client.db.commit()
    _login_operador(client)
    _confirmar(client, papa)
    hijo = agregar_ocupante(client.db, apto, "Hijo", telefono="3007654321")
    client.db.commit()
    _confirmar(client, hijo)

    r = client.get("/residentes")
    assert ">Principal<" in r.text
    assert ">Secundario<" not in r.text
    assert "border-l-4 border-l-red-400" not in r.text


# --------------------------------------------------------------------------- #
# Issue 156 (.scratch/pendientes-cliente): 👫 en Acciones -- marca si el
# Residente comparte su unidad con al menos otro Ocupante ACTIVO.
# --------------------------------------------------------------------------- #
def test_lista_muestra_icono_comparte_apartamento_con_dos_o_mas_ocupantes(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    agregar_ocupante(client.db, apto, "Hijo")  # sin contacto -- igual cuenta como Ocupante activo
    client.db.commit()
    persona = client.db.get(Persona, papa.persona_id)
    persona.apartamento_actual_id = apto.id
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes")
    assert "👫" in r.text
    # Issue 160 (.scratch/pendientes-cliente): enlaza a la tab Residentes de
    # esta misma ficha.
    assert f'href="/residentes/{papa.persona_id}?tab=residentes"' in r.text


def test_lista_no_muestra_icono_comparte_apartamento_con_un_solo_ocupante(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    client.db.commit()
    persona = client.db.get(Persona, papa.persona_id)
    persona.apartamento_actual_id = apto.id
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes")
    assert "👫" not in r.text


def test_lista_no_muestra_icono_comparte_apartamento_sin_apartamento_asignado(client):
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes")
    assert "👫" not in r.text


def test_lista_muestra_boton_eliminar_solo_para_admin(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_admin(client)

    r = client.get("/residentes")
    assert f"modal-eliminar-{p.id}" in r.text


def test_lista_no_muestra_boton_eliminar_para_operador(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes")
    assert f"modal-eliminar-{p.id}" not in r.text


def test_lista_ver_ficha_es_icono_no_texto(client):
    # Issue 69: "Ver ficha" pasa de texto a ícono -- Acciones queda solo con
    # íconos (WhatsApp, llamada, ver, eliminar).
    from app.web.icons import ICONOS_NAV

    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes")
    # "Ver ficha" sigue en aria-label/title (accesibilidad) -- lo que ya no
    # debe estar es como TEXTO VISIBLE del link.
    assert ">Ver ficha<" not in r.text


# --------------------------------------------------------------------------- #
# Issue 70 (.scratch/pendientes-cliente): columna "Torre y Apartamento" de la
# lista usa el mismo formato compacto que la tab "Residentes" de la ficha.
# --------------------------------------------------------------------------- #
def test_lista_columna_torre_apartamento_formato_compacto(client):
    from app.domain.apartamento_service import resolver_apartamento

    apto = resolver_apartamento(client.db, "TORRE 5", "105")
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    p.apartamento_actual_id = apto.id
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes")
    assert "T 05 - APT 105" in r.text


def test_lista_columna_torre_apartamento_no_asignado(client):
    get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes")
    assert "No Asignado" in r.text


# --------------------------------------------------------------------------- #
# Issue 69: tab "Residentes" muestra la referencia del apartamento cuando
# aplica ("T 05 - APT 102"), y "Residentes" a secas si no tiene unidad.
# --------------------------------------------------------------------------- #
def test_tab_residentes_muestra_referencia_del_apartamento(client):
    from app.domain.apartamento_service import resolver_apartamento

    apto = resolver_apartamento(client.db, "TORRE 5", "102")
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    p.apartamento_actual_id = apto.id
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{p.id}")
    assert "T 05 - APT 102" in r.text


def test_tab_residentes_dice_solo_residentes_sin_apartamento(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{p.id}")
    assert 'data-tab="residentes">Residentes<' in r.text


# --------------------------------------------------------------------------- #
# Issue 69: aviso de reasignación bloqueada, visible ANTES de intentar
# guardar (antes el staff solo se enteraba con el error tras el submit).
# --------------------------------------------------------------------------- #
def test_tab_direccion_avisa_si_es_ocupante_activo(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    client.db.commit()
    _login_operador(client)
    _confirmar(client, papa)

    persona = client.db.get(Persona, papa.persona_id)
    r = client.get(f"/residentes/{persona.id}")
    assert "primero dalo de baja como Residente" in r.text


def test_tab_direccion_sin_aviso_si_no_es_ocupante(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{p.id}")
    assert "primero dalo de baja como Residente" not in r.text
    assert "convierte a otro en principal" not in r.text


def test_tab_direccion_picker_expone_residentes_por_unidad(client):
    """Issue 147 (.scratch/pendientes-cliente): tab Dirección usa el mismo
    componente/dato (`residentes_por_torre_apartamento`) que "Asignar
    apartamento"/Recibir en /paquetes -- informativo, mismo criterio que el
    resto de la app (ver `test_modal_asignar_apartamento_expone_
    residentes_por_unidad` en test_packages.py)."""
    import json
    import re

    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    client.db.commit()
    _login_operador(client)
    _confirmar(client, papa)

    otro = get_or_create_persona(client.db, "3009998877", "Beto")
    client.db.commit()

    r = client.get(f"/residentes/{otro.id}")
    match = re.search(r'id="residentes-unidad-direccion">(.*?)</script>', r.text, re.S)
    assert match, "no se encontró el script de residentes por unidad"
    residentes = json.loads(match.group(1))
    assert residentes["TORRE 1"]["101"] == ["PAPÁ"]


# --------------------------------------------------------------------------- #
# Issue 70 (.scratch/pendientes-cliente): "Zona de peligro" se elimina de la
# ficha por completo -- quedaba redundante con el botón "Eliminar" que ya
# existe en la columna Acciones de la lista (issue 68).
# --------------------------------------------------------------------------- #
def test_ficha_ya_no_tiene_zona_de_peligro(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_admin(client)

    r = client.get(f"/residentes/{p.id}")
    assert "Zona de peligro" not in r.text
    assert f"modal-eliminar-{p.id}" not in r.text


# --------------------------------------------------------------------------- #
# Ocupantes de la unidad (Grupo 7) — de solo lectura en esta ficha.
# --------------------------------------------------------------------------- #
def test_ficha_muestra_los_ocupantes_del_apartamento(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    agregar_ocupante(client.db, apto, "Mamá")
    client.db.commit()

    persona = client.db.get(Persona, papa.persona_id)
    persona.apartamento_actual_id = apto.id
    client.db.commit()

    _login_operador(client)
    _confirmar(client, papa)  # papá confirmado como principal (ticket 06)
    r = client.get(f"/residentes/{persona.id}")
    assert r.status_code == 200
    assert "PAPÁ" in r.text and "MAMÁ" in r.text
    assert "Residente principal" in r.text
    assert "+573001234567" in r.text


def test_ficha_sin_apartamento_no_muestra_ocupantes(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()

    _login_operador(client)
    r = client.get(f"/residentes/{p.id}")
    assert r.status_code == 200
    assert "Ocupantes de la unidad" not in r.text


# --------------------------------------------------------------------------- #
# Asignación de Torre/Apartamento (.scratch/pendientes-cliente): única vía
# para tocar `apartamento_actual_id` -- el residente la ve de solo lectura en
# /mis-datos (ver test_customer_verify.py), acá el personal de Papyrus la
# asigna, cambia o desvincula.
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Ticket 01 (.scratch/announce-residente-correcto) — asignar por "Dirección"
# pasa a crear/ligar un Ocupante confirmado, no solo escribir
# apartamento_actual_id.
# --------------------------------------------------------------------------- #
def test_direccion_asigna_crea_ocupante_confirmado_y_principal(client):
    from app.domain.ocupante import Ocupante

    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    client.post(
        f"/residentes/{p.id}/apartamento",
        data={"torre": "TORRE 1", "apartamento": "101"},
    )

    client.db.expire_all()
    ocupante = client.db.query(Ocupante).filter(Ocupante.persona_id == p.id).one()
    assert ocupante.confirmado_en is not None
    assert ocupante.es_principal is True


def test_direccion_permite_agregar_a_unidad_con_principal_ya_confirmado(client):
    """Issue 158 (.scratch/pendientes-cliente) -- revierte el ticket 13 de
    .scratch/ocupante-principal-escenarios: staff con control total, tab
    Dirección ya no exige unidad vacía. Papá se queda de principal -- Hija
    se suma PENDING, no principal (issue 161: staff puede asignar la unidad,
    pero no salta el paso de confirmación -- el Principal, o cualquier
    staff, la confirma después, mismo criterio que agregarla vía tab
    Residentes, ver `test_staff_confirma_un_segundo_ocupante_sin_tocar_
    quien_es_principal`)."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    client.db.commit()
    _login_operador(client)
    _confirmar(client, papa)

    hija = get_or_create_persona(client.db, "3021112233", "Hija")
    client.db.commit()

    r = client.post(
        f"/residentes/{hija.id}/apartamento",
        data={"torre": "TORRE 1", "apartamento": "101"},
    )

    assert r.status_code == 200
    client.db.expire_all()
    assert client.db.get(Persona, hija.id).apartamento_actual_id == apto.id
    ocupante_hija = client.db.query(Ocupante).filter(Ocupante.persona_id == hija.id).one()
    assert ocupante_hija.confirmado_en is None
    assert ocupante_hija.es_principal is False
    papa_actualizado = client.db.get(Ocupante, papa.id)
    assert papa_actualizado.es_principal is True  # Papá no se ve afectado


def test_direccion_desvincula_da_de_baja_al_ocupante(client):
    from app.domain.ocupante import Ocupante

    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)
    client.post(
        f"/residentes/{p.id}/apartamento", data={"torre": "TORRE 1", "apartamento": "101"}
    )

    client.post(f"/residentes/{p.id}/apartamento", data={"torre": "", "apartamento": ""})

    client.db.expire_all()
    ocupante = client.db.query(Ocupante).filter(Ocupante.persona_id == p.id).one()
    assert ocupante.desvinculado_en is not None


def test_direccion_bloquea_reasignar_a_quien_ya_es_ocupante_de_otra_unidad(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    papa = agregar_ocupante(client.db, apto1, "Papá", telefono="3001234567")
    client.db.commit()
    _login_operador(client)
    _confirmar(client, papa)
    persona = client.db.get(Persona, papa.persona_id)

    r = client.post(
        f"/residentes/{persona.id}/apartamento",
        data={"torre": "TORRE 2", "apartamento": "202"},
    )

    assert r.status_code == 400
    # .scratch/ocupante-principal-escenarios, ticket 12: Papá es PRINCIPAL de
    # su unidad actual -- nunca se mueve directo, el mensaje lo explica.
    assert "Ocupante PRINCIPAL" in r.text
    assert "TORRE 1" in r.text
    client.db.expire_all()
    assert client.db.get(Persona, persona.id).apartamento_actual_id == apto1.id


def test_direccion_no_principal_ofrece_mover_sin_marcar_la_casilla(client):
    """.scratch/ocupante-principal-escenarios, ticket 12 -- un no-principal
    de otra unidad queda bloqueado (con el mensaje que ofrece mover) si no
    se marca la casilla, sin efecto."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto1, "Papá", telefono="3001234567")
    hija = agregar_ocupante(client.db, apto1, "Hija", telefono="3021112233")
    client.db.commit()
    _login_operador(client)
    persona = client.db.get(Persona, hija.persona_id)

    r = client.post(
        f"/residentes/{persona.id}/apartamento",
        data={"torre": "TORRE 2", "apartamento": "202"},
    )

    assert r.status_code == 400
    assert "Mover acá" in r.text
    client.db.expire_all()
    assert client.db.get(Persona, persona.id).apartamento_actual_id == apto1.id


def test_direccion_mueve_a_un_no_principal_marcando_la_casilla(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    apto2 = resolver_apartamento(client.db, "TORRE 2", "202")
    agregar_ocupante(client.db, apto1, "Papá", telefono="3001234567")
    hija = agregar_ocupante(client.db, apto1, "Hija", telefono="3021112233")
    client.db.commit()
    _login_operador(client)
    persona = client.db.get(Persona, hija.persona_id)

    r = client.post(
        f"/residentes/{persona.id}/apartamento",
        data={"torre": "TORRE 2", "apartamento": "202", "mover_de_otra_unidad": "1"},
        follow_redirects=False,
    )

    assert r.status_code == 200  # re-renderiza la ficha (no un 303)
    client.db.expire_all()
    assert client.db.get(Persona, persona.id).apartamento_actual_id == apto2.id
    hija_original = client.db.get(Ocupante, hija.id)
    assert hija_original.desvinculado_en is not None


def test_direccion_picker_expone_unidad_pending_sin_principal(client):
    """Issue 147 -- el picker informa cualquier unidad con al menos un
    Ocupante activo (con o sin principal confirmado); ver
    `test_direccion_permite_agregar_a_unidad_con_solo_pendientes_y_promueve`
    para lo que pasa si igual se elige esa unidad."""
    import json
    import re

    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto1, "Papá", telefono="3001234567")  # pending, sin principal
    client.db.commit()
    _login_operador(client)

    p = get_or_create_persona(client.db, "3021112233", "Hija")
    client.db.commit()

    r = client.get(f"/residentes/{p.id}")
    match = re.search(r'id="residentes-unidad-direccion">(.*?)</script>', r.text, re.S)
    assert match, "no se encontró el script de residentes por unidad"
    residentes = json.loads(match.group(1))
    assert residentes["TORRE 1"]["101"] == ["PAPÁ"]


def test_direccion_permite_agregar_a_unidad_con_solo_pendientes_queda_pending(client):
    """Issue 158 (.scratch/pendientes-cliente, revierte el ticket 13 de
    .scratch/ocupante-principal-escenarios) -- una unidad con Ocupante(s)
    pending (sin principal confirmado) también deja de bloquear la
    asignación. Issue 161: pero ya NO se auto-confirma -- Hija llega
    PENDING igual que Papá, ninguno queda principal todavía (eso lo
    resuelve después el Principal confirmando a mano, o el primero de los
    dos en recibir un paquete, `promover_al_recibir`)."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto1, "Papá", telefono="3001234567")
    client.db.commit()
    _login_operador(client)

    p = get_or_create_persona(client.db, "3021112233", "Hija")
    client.db.commit()

    r = client.post(
        f"/residentes/{p.id}/apartamento",
        data={"torre": "TORRE 1", "apartamento": "101"},
    )

    assert r.status_code == 200
    client.db.expire_all()
    assert client.db.get(Persona, p.id).apartamento_actual_id == apto1.id
    ocupante_hija = client.db.query(Ocupante).filter(Ocupante.persona_id == p.id).one()
    assert ocupante_hija.confirmado_en is None
    assert ocupante_hija.es_principal is False
    papa = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto1.id, Ocupante.nombre == "PAPÁ"
    ).one()
    assert papa.confirmado_en is None  # sigue pending, sin tocar
    assert papa.es_principal is False


def test_direccion_mueve_a_un_no_principal_a_unidad_ya_ocupada(client):
    """Issue 158 (.scratch/pendientes-cliente) -- revierte el ticket 13 de
    .scratch/ocupante-principal-escenarios: "mover" (ticket 12) también
    puede aterrizar en una unidad que ya tiene gente. `mover_ocupante` no
    auto-confirma -- Hija llega pending a la unidad nueva, igual que
    cualquier alta nueva sin promover."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    apto2 = resolver_apartamento(client.db, "TORRE 2", "202")
    agregar_ocupante(client.db, apto1, "Papá", telefono="3001234567")
    hija = agregar_ocupante(client.db, apto1, "Hija", telefono="3021112233")
    agregar_ocupante(client.db, apto2, "Vecino", telefono="3031112233")
    client.db.commit()
    _login_operador(client)
    persona = client.db.get(Persona, hija.persona_id)

    r = client.post(
        f"/residentes/{persona.id}/apartamento",
        data={"torre": "TORRE 2", "apartamento": "202", "mover_de_otra_unidad": "1"},
    )

    assert r.status_code == 200
    client.db.expire_all()
    assert client.db.get(Persona, persona.id).apartamento_actual_id == apto2.id
    hija_original = client.db.get(Ocupante, hija.id)
    assert hija_original.desvinculado_en is not None
    nueva_hija = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto2.id, Ocupante.persona_id == persona.id
    ).one()
    assert nueva_hija.confirmado_en is None  # mover_ocupante no auto-confirma
    assert nueva_hija.es_principal is False


def test_direccion_asigna_visible_de_inmediato_en_la_tab_residentes(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    client.post(
        f"/residentes/{p.id}/apartamento",
        data={"torre": "TORRE 1", "apartamento": "101"},
    )

    r = client.get(f"/residentes/{p.id}")
    assert "ANA" in r.text
    assert "Residente principal" in r.text  # tab "Residentes", mismo patrón que test_ficha_muestra_los_ocupantes_del_apartamento


def test_residente_agregado_por_direccion_visible_en_announce_torre_apto(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    client.post(
        f"/residentes/{p.id}/apartamento", data={"torre": "TORRE 1", "apartamento": "101"}
    )

    r = client.get("/announce/identificar", params={"q": "01101"})
    assert r.status_code == 200
    assert "ANA" in r.text  # visible para /announce, no un residente "fantasma"


def test_staff_asigna_torre_y_apartamento_a_cliente_sin_unidad(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.post(
        f"/residentes/{p.id}/apartamento",
        data={"torre": "TORRE 1", "apartamento": "101"},
        follow_redirects=False,
    )
    assert r.status_code == 200  # re-render in-place, mismo patrón que /residentes/{id}

    client.db.expire_all()
    from app.domain.apartamento import Apartamento

    p2 = client.db.get(Persona, p.id)
    apto = client.db.get(Apartamento, p2.apartamento_actual_id)
    assert (apto.torre, apto.apartamento) == ("TORRE 1", "101")


def test_staff_cambia_torre_y_apartamento_de_cliente_existente(client):
    from app.domain.apartamento import Apartamento
    from app.domain.apartamento_service import resolver_apartamento

    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    p.apartamento_actual_id = apto1.id
    client.db.commit()
    _login_operador(client)

    r = client.post(
        f"/residentes/{p.id}/apartamento",
        data={"torre": "TORRE 2", "apartamento": "202"},
        follow_redirects=False,
    )
    assert r.status_code == 200

    client.db.expire_all()
    p2 = client.db.get(Persona, p.id)
    apto = client.db.get(Apartamento, p2.apartamento_actual_id)
    assert (apto.torre, apto.apartamento) == ("TORRE 2", "202")


def test_staff_desvincula_apartamento_dejando_ambos_campos_vacios(client):
    from app.domain.apartamento_service import resolver_apartamento

    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    p.apartamento_actual_id = apto1.id
    client.db.commit()
    _login_operador(client)

    r = client.post(
        f"/residentes/{p.id}/apartamento", data={"torre": "", "apartamento": ""},
        follow_redirects=False,
    )
    assert r.status_code == 200

    client.db.expire_all()
    assert client.db.get(Persona, p.id).apartamento_actual_id is None
    # .scratch/ocupante-principal-escenarios, ticket 13: `p.apartamento_actual_id`
    # no tenía ningún Ocupante real detrás -- se avisa que se limpió el dato.
    assert "dato inconsistente" in r.text


def test_staff_asignar_terna_fuera_del_catalogo_falla(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.post(
        f"/residentes/{p.id}/apartamento",
        data={"torre": "TORRE 99", "apartamento": "101"},
    )
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Persona, p.id).apartamento_actual_id is None


def test_staff_torre_o_apartamento_incompleto_rechaza_todo(client):
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    client.db.commit()
    _login_operador(client)

    r = client.post(f"/residentes/{p.id}/apartamento", data={"torre": "TORRE 1"})
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Persona, p.id).apartamento_actual_id is None


def test_staff_no_puede_reasignar_mientras_hay_otros_ocupantes_activos(client):
    from app.domain.ocupante import Ocupante

    persona, apto1 = _persona_con_apartamento(client)  # "Papá", pending
    _login_operador(client)
    papa = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto1.id, Ocupante.nombre == "PAPÁ"
    ).one()
    _confirmar(client, papa)  # papá confirmado como principal

    from app.domain.ocupante_service import agregar_ocupante

    agregar_ocupante(client.db, apto1, "Hijo")  # sin teléfono, sigue activo
    client.db.commit()

    r = client.post(
        f"/residentes/{persona.id}/apartamento",
        data={"torre": "TORRE 2", "apartamento": "202"},
    )
    assert r.status_code == 400

    client.db.expire_all()
    assert client.db.get(Persona, persona.id).apartamento_actual_id == apto1.id


def test_staff_reasignar_al_mismo_apartamento_actual_no_falla(client):
    persona, apto = _persona_con_apartamento(client)  # "Papá", pending, principal aún no
    _login_operador(client)

    r = client.post(
        f"/residentes/{persona.id}/apartamento",
        data={"torre": apto.torre, "apartamento": apto.apartamento},
        follow_redirects=False,
    )
    assert r.status_code == 200

    client.db.expire_all()
    assert client.db.get(Persona, persona.id).apartamento_actual_id == apto.id


def test_cambiar_de_apartamento_por_staff_no_reescribe_snapshot_de_paquete(client):
    from app.domain.apartamento import Apartamento
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.paquete import Paquete
    from app.domain.paquete_service import Destinatario, announce

    apto1 = resolver_apartamento(client.db, "TORRE 1", "101")
    p = get_or_create_persona(client.db, "3001234567", "Ana")
    p.apartamento_actual_id = apto1.id
    client.db.commit()

    paquete = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    paquete_id = paquete.id

    _login_operador(client)
    client.post(
        f"/residentes/{p.id}/apartamento",
        data={"torre": "TORRE 2", "apartamento": "202"},
    )

    client.db.expire_all()
    pq = client.db.get(Paquete, paquete_id)
    assert (pq.snapshot_torre, pq.snapshot_apartamento) == ("TORRE 1", "101")


# --------------------------------------------------------------------------- #
# Ticket 10 (.scratch/mis-datos) — staff gestiona Ocupantes sin restricción.
# --------------------------------------------------------------------------- #
def _persona_con_apartamento(client, torre="TORRE 1", apartamento_num="101"):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, torre, apartamento_num)
    papa = agregar_ocupante(client.db, apto, "Papá", telefono="3001234567")
    client.db.commit()
    persona = client.db.get(Persona, papa.persona_id)
    persona.apartamento_actual_id = apto.id
    client.db.commit()
    return persona, apto


def test_ficha_form_agregar_residente_explica_que_contacto_suma_a_alguien_existente(client):
    # Issue 157: sin este texto no era obvio que el campo Teléfono/WhatsApp
    # también sirve para SUMAR a alguien con ficha propia, no solo para dar
    # de alta gente nueva.
    persona, _apto = _persona_con_apartamento(client)
    _login_operador(client)

    r = client.get(f"/residentes/{persona.id}")
    assert r.status_code == 200
    assert "escribí su teléfono o WhatsApp para sumarla acá" in r.text


def test_identificar_ocupante_encuentra_persona_por_telefono(client):
    # Issue 154 -- mismo endpoint/mecanismo que "+ Nuevo residente" en
    # /paquetes (`nuevo_residente_identificar`), acá escopado a la unidad
    # ACTUAL de la Persona de la ficha en vez del snapshot de un Paquete.
    from app.domain.persona_service import get_or_create_persona

    persona, _apto = _persona_con_apartamento(client)
    get_or_create_persona(client.db, "3005558888", "Persona Ya Registrada")
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{persona.id}/ocupantes/identificar", params={"contacto": "3005558888"})
    assert r.status_code == 200
    assert r.json() == {"encontrado": True, "nombre": "PERSONA YA REGISTRADA", "conflicto": None}


def test_identificar_ocupante_sin_match_devuelve_encontrado_false(client):
    persona, _apto = _persona_con_apartamento(client)
    _login_operador(client)

    r = client.get(f"/residentes/{persona.id}/ocupantes/identificar", params={"contacto": "3009998888"})
    assert r.status_code == 200
    assert r.json() == {"encontrado": False}


def test_identificar_ocupante_conflicto_no_principal(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    persona, _apto = _persona_con_apartamento(client)
    apto_conflicto = resolver_apartamento(client.db, "TORRE 2", "202")
    agregar_ocupante(client.db, apto_conflicto, "Hija", telefono="3005557777")
    client.db.commit()
    _login_operador(client)

    r = client.get(f"/residentes/{persona.id}/ocupantes/identificar", params={"contacto": "3005557777"})
    assert r.status_code == 200
    data = r.json()
    assert data["encontrado"] is True
    assert data["conflicto"]["es_principal"] is False
    assert data["conflicto"]["torre"] == "TORRE 2"
    assert data["conflicto"]["apartamento"] == "202"


def test_identificar_ocupante_conflicto_principal(client):
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    persona, _apto = _persona_con_apartamento(client)
    _login_operador(client)
    apto_conflicto = resolver_apartamento(client.db, "TORRE 2", "202")
    principal = agregar_ocupante(client.db, apto_conflicto, "Principal", telefono="3005556666")
    _confirmar(client, principal)

    r = client.get(f"/residentes/{persona.id}/ocupantes/identificar", params={"contacto": "3005556666"})
    assert r.status_code == 200
    data = r.json()
    assert data["conflicto"]["es_principal"] is True
    assert data["conflicto"]["persona_id"] == str(principal.persona_id)


def test_identificar_ocupante_sin_conflicto_si_ya_es_de_esta_misma_unidad(client):
    # El contacto ya es Ocupante de la MISMA unidad de esta ficha -- no hay
    # nada que avisar, `conflicto` queda None.
    persona, apto = _persona_con_apartamento(client)
    _login_operador(client)

    r = client.get(f"/residentes/{persona.id}/ocupantes/identificar", params={"contacto": "3001234567"})
    assert r.status_code == 200
    assert r.json() == {"encontrado": True, "nombre": "PAPÁ", "conflicto": None}


def test_identificar_ocupante_requiere_sesion_de_staff(client):
    persona, _apto = _persona_con_apartamento(client)
    r = client.get(
        f"/residentes/{persona.id}/ocupantes/identificar",
        params={"contacto": "3005558888"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("/ingresar")


def test_agregar_ocupante_bloquea_contacto_ya_ocupante_de_otra_unidad(client):
    """.scratch/ocupante-principal-escenarios, ticket 12 -- sin marcar la
    casilla, queda bloqueado con el mensaje que ofrece mover."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    persona, apto = _persona_con_apartamento(client)
    apto2 = resolver_apartamento(client.db, "TORRE 2", "202")
    agregar_ocupante(client.db, apto2, "Hija", telefono="3021112233")
    client.db.commit()

    _login_operador(client)
    r = client.post(
        f"/residentes/{persona.id}/ocupantes",
        data={"nombre": "Cualquiera", "contacto": "3021112233"},
    )
    assert r.status_code == 400
    assert "Mover acá" in r.text


def test_agregar_ocupante_mueve_marcando_la_casilla(client):
    """El nombre tecleado se ignora -- se mueve la identidad REAL (Hija),
    no se crea un residente nuevo llamado "Cualquiera"."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    persona, apto = _persona_con_apartamento(client)
    apto2 = resolver_apartamento(client.db, "TORRE 2", "202")
    hija = agregar_ocupante(client.db, apto2, "Hija", telefono="3021112233")
    client.db.commit()

    _login_operador(client)
    r = client.post(
        f"/residentes/{persona.id}/ocupantes",
        data={
            "nombre": "Cualquiera", "contacto": "3021112233",
            "mover_de_otra_unidad": "1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    movida = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.persona_id == hija.persona_id
    ).one()
    assert movida.nombre == "HIJA"  # no "CUALQUIERA"
    assert client.db.get(Ocupante, hija.id).desvinculado_en is not None
    assert not client.db.query(Ocupante).filter(Ocupante.nombre == "CUALQUIERA").first()


def test_staff_crea_ocupante_sin_telefono(client):
    from app.domain.ocupante import Ocupante

    persona, apto = _persona_con_apartamento(client)
    _login_operador(client)

    r = client.post(
        f"/residentes/{persona.id}/ocupantes", data={"nombre": "Hijo"}, follow_redirects=False
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.nombre == "HIJO"
    ).one() is not None


def test_staff_asocia_telefono_a_ocupante(client):
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    persona, apto = _persona_con_apartamento(client)
    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    _login_operador(client)
    r = client.post(
        f"/residentes/{persona.id}/ocupantes/{hijo.id}/telefono",
        data={"telefono": "3021112233"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Ocupante, hijo.id).persona_id is not None


def test_staff_edita_telefono_ya_asociado(client):
    # `.scratch/pendientes-cliente/issues/35` -- mismo endpoint que asociar,
    # rama distinta cuando el Ocupante ya tenía un teléfono.
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    persona, apto = _persona_con_apartamento(client)
    hija = agregar_ocupante(client.db, apto, "Hija", telefono="3021112233")
    client.db.commit()

    _login_operador(client)
    r = client.post(
        f"/residentes/{persona.id}/ocupantes/{hija.id}/telefono",
        data={"telefono": "3029998877"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    ocupante = client.db.get(Ocupante, hija.id)
    nueva_persona = client.db.get(Persona, ocupante.persona_id)
    assert nueva_persona.telefono == "+573029998877"


def test_staff_desvincula_telefono_de_ocupante(client):
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    persona, apto = _persona_con_apartamento(client)
    hija = agregar_ocupante(client.db, apto, "Hija", telefono="3021112233")
    client.db.commit()

    _login_operador(client)
    r = client.post(
        f"/residentes/{persona.id}/ocupantes/{hija.id}/desvincular-telefono",
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Ocupante, hija.id).persona_id is None


def test_staff_agrega_ocupante_con_whatsapp_desde_agregar_residente(client):
    """.scratch/ocupante-principal-escenarios, ticket 06 -- input único
    autoclasificado en "agregar Residente"."""
    from app.domain.ocupante import Ocupante

    persona, apto = _persona_con_apartamento(client)
    _login_operador(client)

    r = client.post(
        f"/residentes/{persona.id}/ocupantes",
        data={"nombre": "Hija", "contacto": "hija.whats"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    ocupante = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.nombre == "HIJA"
    ).one()
    nueva_persona = client.db.get(Persona, ocupante.persona_id)
    assert nueva_persona.whatsapp_usuario == "hija.whats"


def test_staff_asocia_whatsapp_a_ocupante_sin_contacto(client):
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    persona, apto = _persona_con_apartamento(client)
    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    _login_operador(client)
    r = client.post(
        f"/residentes/{persona.id}/ocupantes/{hijo.id}/contacto",
        data={"contacto": "hijo.whats"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    ocupante = client.db.get(Ocupante, hijo.id)
    assert ocupante.persona_id is not None
    assert client.db.get(Persona, ocupante.persona_id).whatsapp_usuario == "hijo.whats"


def test_staff_edita_whatsapp_ya_asociado(client):
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    persona, apto = _persona_con_apartamento(client)
    hija = agregar_ocupante(client.db, apto, "Hija", whatsapp_usuario="hija.vieja")
    client.db.commit()

    _login_operador(client)
    r = client.post(
        f"/residentes/{persona.id}/ocupantes/{hija.id}/whatsapp",
        data={"whatsapp_usuario": "hija.nueva"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    ocupante = client.db.get(Ocupante, hija.id)
    assert client.db.get(Persona, ocupante.persona_id).whatsapp_usuario == "hija.nueva"


def test_staff_desvincula_whatsapp_de_ocupante(client):
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    persona, apto = _persona_con_apartamento(client)
    hija = agregar_ocupante(client.db, apto, "Hija", whatsapp_usuario="hija.whats")
    client.db.commit()

    _login_operador(client)
    r = client.post(
        f"/residentes/{persona.id}/ocupantes/{hija.id}/desvincular-whatsapp",
        follow_redirects=False,
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Ocupante, hija.id).persona_id is None


def test_staff_da_de_baja_ocupante(client):
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    persona, apto = _persona_con_apartamento(client)
    hijo = agregar_ocupante(client.db, apto, "Hijo")
    client.db.commit()

    _login_operador(client)
    r = client.post(f"/residentes/{persona.id}/ocupantes/{hijo.id}/baja", follow_redirects=False)
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Ocupante, hijo.id).desvinculado_en is not None


def test_staff_promueve_ocupante_con_telefono(client):
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante

    persona, apto = _persona_con_apartamento(client)
    hija = agregar_ocupante(client.db, apto, "Hija", telefono="3021112233")
    client.db.commit()
    papa = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.nombre == "PAPÁ"
    ).one()

    _login_operador(client)
    r = client.post(
        f"/residentes/{persona.id}/ocupantes/{hija.id}/promover", follow_redirects=False
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Ocupante, hija.id).es_principal is True
    assert client.db.get(Ocupante, papa.id).es_principal is False


# --------------------------------------------------------------------------- #
# Ticket 07 (.scratch/apartamento-catalogo-confirmacion) — staff confirma
# Ocupantes pendientes.
# --------------------------------------------------------------------------- #
def test_staff_confirma_al_primer_ocupante_y_lo_promueve_a_principal(client):
    from app.domain.ocupante import Ocupante

    persona, apto = _persona_con_apartamento(client)  # "Papá" pending, sin confirmar
    papa = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.nombre == "PAPÁ"
    ).one()
    assert papa.confirmado_en is None and papa.es_principal is False

    _login_operador(client)
    r = client.post(
        f"/residentes/{persona.id}/ocupantes/{papa.id}/confirmar", follow_redirects=False
    )
    assert r.status_code == 303

    client.db.expire_all()
    confirmado = client.db.get(Ocupante, papa.id)
    assert confirmado.confirmado_en is not None
    assert confirmado.es_principal is True


def test_staff_confirma_un_segundo_ocupante_sin_tocar_quien_es_principal(client):
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import agregar_ocupante, confirmar_ocupante

    persona, apto = _persona_con_apartamento(client)
    papa = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.nombre == "PAPÁ"
    ).one()
    _login_operador(client)  # crea el ADMIN internamente
    admin = client.db.query(Usuario).filter(Usuario.rol == RolUsuario.ADMIN).one()
    confirmar_ocupante(client.db, papa, admin)  # papá ya confirmado como principal
    hijo = agregar_ocupante(client.db, apto, "Hijo")  # segundo, pending
    client.db.commit()

    r = client.post(
        f"/residentes/{persona.id}/ocupantes/{hijo.id}/confirmar", follow_redirects=False
    )
    assert r.status_code == 303

    client.db.expire_all()
    assert client.db.get(Ocupante, hijo.id).confirmado_en is not None
    assert client.db.get(Ocupante, hijo.id).es_principal is False
    assert client.db.get(Ocupante, papa.id).es_principal is True  # no lo tocó


def test_staff_rechaza_un_ocupante_pending_via_la_misma_ruta_de_baja(client):
    from app.domain.ocupante import Ocupante

    persona, apto = _persona_con_apartamento(client)
    papa = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.nombre == "PAPÁ"
    ).one()

    _login_operador(client)
    r = client.post(f"/residentes/{persona.id}/ocupantes/{papa.id}/baja", follow_redirects=False)
    assert r.status_code == 303

    client.db.expire_all()
    rechazado = client.db.get(Ocupante, papa.id)
    assert rechazado.desvinculado_en is not None
    assert rechazado.confirmado_en is None  # nunca llegó a confirmarse


def test_ficha_muestra_badge_pendiente_y_confirmado(client):
    from app.domain.ocupante import Ocupante
    from app.domain.ocupante_service import confirmar_ocupante

    persona, apto = _persona_con_apartamento(client)
    papa = client.db.query(Ocupante).filter(
        Ocupante.apartamento_id == apto.id, Ocupante.nombre == "PAPÁ"
    ).one()
    _login_operador(client)  # crea el ADMIN internamente
    admin = client.db.query(Usuario).filter(Usuario.rol == RolUsuario.ADMIN).one()
    confirmar_ocupante(client.db, papa, admin)
    from app.domain.ocupante_service import agregar_ocupante
    agregar_ocupante(client.db, apto, "Hijo")  # segundo, pending
    client.db.commit()

    r = client.get(f"/residentes/{persona.id}")
    assert r.status_code == 200
    assert "Pendiente de confirmar" in r.text
    assert "Residente principal" in r.text  # papá, ya confirmado y promovido


# --------------------------------------------------------------------------- #
# Ticket 12 (.scratch/mis-datos) — staff ve (solo lectura) la autorización
# automática de recepción. Issue 68: pasó de un párrafo de texto a un badge
# siempre visible en el header -- ver `test_ficha_muestra_badge_de_recepcion_*`.
# --------------------------------------------------------------------------- #
