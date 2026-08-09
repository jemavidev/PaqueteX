# -*- coding: utf-8 -*-
"""
Capa web — `/residentes` (buscar + ver/editar cliente, ticket 02).

Comportamiento observable por HTTP: exige sesión de staff (CUALQUIER rol);
buscar por teléfono o nombre encuentra al cliente correcto; editar es parcial y
opera sobre la Persona de OTRO (no la propia sesión); email inválido rechaza
sin persistir; id inexistente -> 404.
"""

from app.domain.persona import Persona
from app.domain.persona_service import get_or_create_persona
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


def test_buscar_por_nombre_de_segundo_contacto(client):
    from app.domain.persona_service import update_datos_personales

    p = get_or_create_persona(client.db, "3001234567", "Ana")
    update_datos_personales(client.db, p, segundo_contacto="Carlos Gómez")
    client.db.commit()
    _login_operador(client)

    r = client.get("/residentes", params={"q": "Carlos Gómez"})
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
    # -- aparece 2 veces por fila (columna Nombre + botón "Ver ficha"), 4 si
    # la fila estuviera duplicada.
    from app.domain.persona import Persona

    ana = client.db.query(Persona).filter(Persona.nombre == "ANA GÓMEZ").one()
    r = client.get("/residentes", params={"q": "gómez"})
    assert r.status_code == 200
    assert r.text.count(f"/residentes/{ana.id}") == 2


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


def test_residentes_sin_clientes_registrados_muestra_estado_vacio(client):
    _login_operador(client)

    r = client.get("/residentes")
    assert r.status_code == 200
    assert "sin clientes todavía" in r.text.lower()


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


def test_lista_muestra_badge_principal_no_badge_secundario_pero_si_acento(client):
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
    assert "border-l-4 border-l-red-400" in r.text


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


def test_tab_direccion_marca_apartamentos_con_principal_para_el_picker(client):
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
    assert '"TORRE 1|101"' in r.text


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
