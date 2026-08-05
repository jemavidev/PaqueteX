# -*- coding: utf-8 -*-
"""
Capa web — `/mis-paquetes` (historial del cliente, Grupo 10 de la Ronda 2;
rediseño en pestañas + detalle expandible, `.scratch/pendientes-cliente/
issues/42`).

Comportamiento observable: exige sesión de cliente; lista paquetes donde su
teléfono es Anunciante O Destinatario, cada uno con su `access_code` (ya no
manda a `/consultar` -- el detalle se expande en la misma vista).
"""

from app.domain.apartamento_service import resolver_apartamento
from app.domain.otp_sender import DevOtpSender
from app.domain.paquete import Paquete
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona import Persona
from app.domain.telefono import normalizar_telefono
from app.domain.usuario import RolUsuario, Usuario
from app.web.otp import get_otp_sender

_CANON = "+573001234567"


def _login_cliente(client, telefono="3001234567"):
    """Corrección en vivo 2026-08-02: pedir OTP ahora exige que el teléfono
    sea elegible (tenga un Paquete Recibido) -- se siembra uno antes de
    pedir el código. El test de "cliente sin ningún paquete" borra este
    paquete de elegibilidad DESPUÉS de loguearse (ya no es un estado
    alcanzable ANTES de loguearse, porque ahora es un prerrequisito del
    login mismo).

    Acepta cualquier teléfono (no solo el default) -- necesario para
    `.scratch/mis-paquetes-vista-apartamento`, donde varios Ocupantes de la
    misma unidad se loguean cada uno con el suyo."""
    canon = normalizar_telefono(telefono)
    staff = Usuario(nombre="ActorElegibilidad", rol=RolUsuario.OPERADOR)
    client.db.add(staff)
    client.db.flush()
    paquete_elegibilidad = announce(
        client.db,
        anunciante_telefono=telefono,
        anunciante_nombre="Cliente de prueba (elegibilidad)",
        destinatario=Destinatario.yo_mismo(),
    )
    receive(client.db, paquete_elegibilidad, staff)
    client.db.commit()

    sender = DevOtpSender()
    client.app.dependency_overrides[get_otp_sender] = lambda: sender
    client.post("/otp/solicitar", data={"telefono": telefono})
    codigo = sender.enviados[canon]
    client.post("/otp/verificar", data={"telefono": telefono, "codigo": codigo})
    return client.db.query(Persona).filter(Persona.telefono == canon).one()


def test_dos_ocupantes_del_mismo_apartamento_ven_el_conjunto_combinado(client):
    """.scratch/mis-paquetes-vista-apartamento/issues/01."""
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", telefono="3001111111")
    agregar_ocupante(client.db, apto, "Beto", telefono="3002222222")
    client.db.commit()

    p_ana = announce(client.db, "3001111111", "Ana", Destinatario.yo_mismo())
    p_beto = announce(client.db, "3002222222", "Beto", Destinatario.yo_mismo())
    client.db.commit()

    _login_cliente(client, telefono="3001111111")

    r = client.get("/mis-paquetes")
    assert p_ana.access_code in r.text
    assert p_beto.access_code in r.text


def test_conteos_por_pestana_reflejan_el_conjunto_combinado(client):
    """.scratch/mis-paquetes-vista-apartamento/issues/01 -- los conteos de
    cada pestaña deben sumar los Paquetes de TODOS los Ocupantes, no solo
    los de la sesión actual."""
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", telefono="3001111111")
    agregar_ocupante(client.db, apto, "Beto", telefono="3002222222")
    client.db.commit()

    announce(client.db, "3001111111", "Ana", Destinatario.yo_mismo())  # ANUNCIADO
    announce(client.db, "3002222222", "Beto", Destinatario.yo_mismo())  # ANUNCIADO
    client.db.commit()

    # _login_cliente sembra un 3er paquete (RECIBIDO) de elegibilidad para
    # 3001111111 -- total esperado: 2 ANUNCIADO + 1 RECIBIDO.
    _login_cliente(client, telefono="3001111111")

    r = client.get("/mis-paquetes")
    assert "Anunciados · 2" in r.text
    assert "Recibidos · 1" in r.text
    assert "Entregados · 0" in r.text
    assert "Cancelados · 0" in r.text


def test_sesion_sin_apartamento_sigue_viendo_solo_lo_propio(client):
    """Regresión: el alcance ampliado no debe afectar a quien no tiene
    Apartamento asignado."""
    p_propio = announce(client.db, "3001234567", "Ana", Destinatario.yo_mismo())
    p_ajeno = announce(client.db, "3009999999", "Otro", Destinatario.yo_mismo())
    client.db.commit()
    _login_cliente(client)

    r = client.get("/mis-paquetes")
    assert p_propio.access_code in r.text
    assert p_ajeno.access_code not in r.text


def test_ocupante_dado_de_baja_no_contamina_la_vista_de_los_demas(client):
    from app.domain.ocupante_service import agregar_ocupante, dar_de_baja_ocupante

    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Ana", telefono="3001111111")
    secundario = agregar_ocupante(client.db, apto, "Beto", telefono="3002222222")
    client.db.commit()

    p_beto = announce(client.db, "3002222222", "Beto", Destinatario.yo_mismo())
    client.db.commit()

    dar_de_baja_ocupante(client.db, secundario)
    client.db.commit()

    _login_cliente(client, telefono="3001111111")

    r = client.get("/mis-paquetes")
    assert p_beto.access_code not in r.text


def test_sin_sesion_redirige_a_login_de_cliente(client):
    r = client.get("/mis-paquetes", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/otp")


def test_lista_paquetes_anunciados_por_el_cliente(client):
    # Anuncia ANTES de loguearse -- así la Persona nace con nombre "Ana"
    # (get_or_create_persona no sobreescribe el nombre de una ya existente).
    p = announce(
        client.db,
        anunciante_telefono="3001234567",
        anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    _login_cliente(client)

    r = client.get("/mis-paquetes")
    assert r.status_code == 200
    assert "ANA" in r.text
    assert p.access_code in r.text


def test_lista_paquetes_donde_es_destinatario_aunque_no_haya_anunciado(client):
    from app.domain.persona_service import update_datos_personales

    persona = _login_cliente(client)
    update_datos_personales(client.db, persona, nombre="Ana")
    client.db.commit()

    p = announce(
        client.db,
        anunciante_telefono="3019999999",
        anunciante_nombre="Portero",
        destinatario=Destinatario.persona_registrada("3001234567"),
    )
    client.db.commit()

    r = client.get("/mis-paquetes")
    assert r.status_code == 200
    assert "ANA" in r.text  # el nombre mostrado es el del destinatario, no el anunciante
    assert p.access_code in r.text


def test_no_muestra_paquetes_de_otro_telefono(client):
    _login_cliente(client)
    announce(
        client.db,
        anunciante_telefono="3019999999",
        anunciante_nombre="Otro",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()

    r = client.get("/mis-paquetes")
    assert r.status_code == 200
    assert "Otro" not in r.text


# --------------------------------------------------------------------------- #
# `.scratch/pendientes-cliente/issues/42` — pestañas por estado + detalle.
# `.scratch/pendientes-cliente/issues/43` — se quitó el tab "Todos" y cada
# tab restante se colorea con el color real de su estado.
# --------------------------------------------------------------------------- #
def test_pestanas_muestran_el_conteo_por_estado(client):
    _login_cliente(client)  # ya siembra un Paquete RECIBIDO como elegibilidad
    announce(client.db, "3001234567", "Ana", Destinatario.yo_mismo())  # 2do, queda ANUNCIADO
    client.db.commit()

    r = client.get("/mis-paquetes")
    assert r.status_code == 200
    assert "Todos ·" not in r.text
    assert "Recibidos · 1" in r.text
    assert "Anunciados · 1" in r.text
    assert "Entregados · 0" in r.text
    assert "Cancelados · 0" in r.text


def test_tabs_de_estado_tienen_su_color_correspondiente(client):
    _login_cliente(client)
    client.db.commit()

    r = client.get("/mis-paquetes")
    assert r.status_code == 200
    assert 'data-tab="ANUNCIADO" data-color="amber"' in r.text
    assert 'data-tab="RECIBIDO" data-color="blue"' in r.text
    assert 'data-tab="ENTREGADO" data-color="emerald"' in r.text
    assert 'data-tab="CANCELADO" data-color="red"' in r.text


def test_muestra_el_codigo_de_acceso_de_cada_paquete(client):
    p = announce(client.db, "3001234567", "Ana", Destinatario.yo_mismo())
    client.db.commit()
    _login_cliente(client)

    r = client.get("/mis-paquetes")
    assert p.access_code in r.text


def test_ubicacion_con_apartamento_muestra_conjunto_torre_apto(client):
    """.scratch/pendientes-cliente/issues/47 (Alternativa A) -- Conjunto en
    Título Case, Torre/Apto resaltados, sin las MAYÚSCULAS crudas del
    snapshot.

    Usa agregar_ocupante (no set_apartamento_actual suelto) a propósito:
    telefonos_activos_del_apartamento_de (issue 01 de
    mis-paquetes-vista-apartamento) resuelve por el Ocupante real, no por
    apartamento_actual_id crudo -- ese estado sin Ocupante nunca ocurre en
    producción (todo caller real pasa por agregar_ocupante primero)."""
    from app.domain.ocupante_service import agregar_ocupante

    apto = resolver_apartamento(client.db, "TORRE 2", "301")
    agregar_ocupante(client.db, apto, "Ana", telefono="3001234567")
    p = announce(client.db, "3001234567", "Ana", Destinatario.yo_mismo())
    client.db.commit()
    _login_cliente(client)

    r = client.get("/mis-paquetes")
    assert "El Club" in r.text
    assert "EL CLUB" not in r.text
    assert "Torre <strong" in r.text and ">TORRE 2</strong>" in r.text
    assert "Apto <strong" in r.text and ">301</strong>" in r.text


def test_ubicacion_sin_apartamento_muestra_texto_generico(client):
    p = announce(client.db, "3001234567", "Ana", Destinatario.yo_mismo())
    client.db.commit()
    _login_cliente(client)

    r = client.get("/mis-paquetes")
    assert "Sin apartamento" in r.text


def test_codigo_de_acceso_enlaza_a_consultar(client):
    """.scratch/pendientes-cliente/issues/46 -- el código ya no copia al
    portapapeles, redirige a /consultar?q=<codigo> para ver el detalle."""
    p = announce(client.db, "3001234567", "Ana", Destinatario.yo_mismo())
    client.db.commit()
    _login_cliente(client)

    r = client.get("/mis-paquetes")
    assert f'href="/consultar?q={p.access_code}"' in r.text
    assert "data-copiar" not in r.text


def test_detalle_incluye_timeline_y_no_solo_el_estado(client):
    persona = _login_cliente(client)
    p = client.db.query(Paquete).filter(Paquete.announced_by_phone == persona.telefono).first()

    r = client.get("/mis-paquetes")
    assert r.status_code == 200
    # El timeline (mismo componente que /consultar) trae al menos el hito
    # "Recibido" con su actor -- no solo el badge de estado en la tarjeta.
    assert "ActorElegibilidad" in r.text or "(staff)" in r.text
    assert 'id="detalle-mp' in r.text  # el panel expandible existe en el HTML


def test_sin_paquetes_muestra_mensaje_vacio(client):
    # "Cliente logueado con cero paquetes" ya no es alcanzable ANTES de
    # loguearse (corrección en vivo 2026-08-02: la elegibilidad de OTP
    # exige un Paquete Recibido) -- se borra el paquete de elegibilidad
    # DESPUÉS de loguearse, puramente para seguir probando esta rama
    # defensiva de la plantilla.
    _login_cliente(client)
    client.db.query(Paquete).filter(
        Paquete.announced_by_phone == _CANON
    ).delete()
    client.db.commit()

    r = client.get("/mis-paquetes")
    assert r.status_code == 200
    assert "no tienes ningún paquete" in r.text.lower()
