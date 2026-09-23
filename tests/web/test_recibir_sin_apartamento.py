# -*- coding: utf-8 -*-
"""
Capa web — Recibir un paquete sin apartamento nunca se bloquea (issue 377, `.scratch/pendientes-cliente`).

Antes, marcar "Nuevo residente" en un paquete sin unidad (y sin elegir Torre/Apartamento en el selector del mismo
modal) cancelaba TODA la recepción con "Este paquete no tiene apartamento resuelto en su snapshot.", descartando
guía, tipo, condición y fotos. Ahora el paquete se recibe igual: solo se omite el registro como Ocupante (que sí
necesita una unidad), lo tecleado queda como destinatario del paquete y se avisa.
"""

from app.domain.ocupante import Ocupante
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin

_PW = "Contrasena1"


def _login_staff(client, email="staff@club.com"):
    create_initial_admin(client.db, email, "Operador", _PW)
    client.db.commit()
    r = client.post("/ingresar", data={"email": email, "password": _PW})
    assert r.status_code == 200


def _anunciar_sin_apartamento(client, tel="3001234567", nombre="Ana"):
    p = announce(
        client.db,
        anunciante_telefono=tel,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    assert p.snapshot_apartamento is None
    return p


def _recibir(client, p, **campos):
    return client.post(f"/paquetes/{p.id}/recibir", data=campos, follow_redirects=False)


def _recargar(client, p):
    client.db.expire_all()
    return client.db.get(Paquete, p.id)


def test_nuevo_residente_sin_apartamento_recibe_igual_y_guarda_lo_tecleado(client):
    _login_staff(client)
    p = _anunciar_sin_apartamento(client)

    r = _recibir(
        client, p,
        guide_number="GUIA-377",
        candidato_idx="nuevo",
        nuevo_ocupante_nombre="Maria Lopez",
        nuevo_ocupante_contacto="3105559876",
    )

    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes?aviso=residente_sin_apartamento"
    p2 = _recargar(client, p)
    assert p2.estado == EstadoPaquete.RECIBIDO
    assert p2.guide_number == "GUIA-377"  # nada de lo capturado se descarta
    assert p2.recipient_name == "MARIA LOPEZ"
    assert p2.recipient_phone.endswith("3105559876")
    assert client.db.query(Ocupante).count() == 0  # sin unidad no hay Ocupante que registrar


def test_el_aviso_explica_que_no_se_registro_el_residente(client):
    _login_staff(client)
    p = _anunciar_sin_apartamento(client)
    r = _recibir(client, p, candidato_idx="nuevo", nuevo_ocupante_nombre="Maria Lopez")

    pagina = client.get(r.headers["location"])

    assert pagina.status_code == 200
    assert "no tiene apartamento" in pagina.text
    assert "snapshot" not in pagina.text


def test_nuevo_residente_sin_apartamento_y_sin_nombre_recibe_sin_tocar_el_destinatario(client):
    _login_staff(client)
    p = _anunciar_sin_apartamento(client, nombre="Ana")

    r = _recibir(client, p, candidato_idx="nuevo", nuevo_ocupante_nombre="", nuevo_ocupante_contacto="")

    assert r.status_code == 303
    p2 = _recargar(client, p)
    assert p2.estado == EstadoPaquete.RECIBIDO
    assert p2.recipient_name == p.recipient_name


def test_contacto_whatsapp_o_invalido_sin_apartamento_no_bloquea_y_guarda_solo_el_nombre(client):
    _login_staff(client)
    p = _anunciar_sin_apartamento(client, tel="3001234567")
    telefono_original = p.recipient_phone

    r = _recibir(
        client, p, candidato_idx="nuevo", nuevo_ocupante_nombre="Maria Lopez", nuevo_ocupante_contacto="???",
    )

    assert r.status_code == 303
    p2 = _recargar(client, p)
    assert p2.estado == EstadoPaquete.RECIBIDO
    assert p2.recipient_name == "MARIA LOPEZ"
    assert p2.recipient_phone == telefono_original


def test_nombre_tecleado_sin_elegir_tarjeta_y_sin_apartamento_tampoco_bloquea(client):
    _login_staff(client)
    p = _anunciar_sin_apartamento(client)

    r = _recibir(client, p, nuevo_ocupante_nombre="Maria Lopez")

    assert r.status_code == 303
    assert _recargar(client, p).estado == EstadoPaquete.RECIBIDO


def test_sin_apartamento_y_sin_tocar_residente_recibe_como_siempre(client):
    _login_staff(client)
    p = _anunciar_sin_apartamento(client)

    r = _recibir(client, p, guide_number="G-1")

    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes"
    assert _recargar(client, p).estado == EstadoPaquete.RECIBIDO


def test_desde_consultar_sin_apartamento_tambien_recibe(client):
    _login_staff(client)
    p = _anunciar_sin_apartamento(client)

    r = _recibir(
        client, p, origen="consultar", q=p.access_code,
        candidato_idx="nuevo", nuevo_ocupante_nombre="Maria Lopez",
    )

    assert r.status_code == 303
    assert r.headers["location"].startswith("/consultar")
    assert _recargar(client, p).estado == EstadoPaquete.RECIBIDO


def test_el_modal_deshabilita_nuevo_residente_hasta_elegir_apartamento(client):
    from app.domain.apartamento_service import resolver_apartamento

    _login_staff(client)
    resolver_apartamento(client.db, "TORRE 1", "101")  # catálogo con al menos una unidad: se muestra el selector
    client.db.commit()
    p = _anunciar_sin_apartamento(client)

    html = client.get("/paquetes").text

    tarjeta = html.split(f'id="recibir-candidato-nuevo-{p.id}"', 1)[1].split(">", 1)[0]
    assert "disabled" in tarjeta
    assert "Elige primero el apartamento" in html


def test_snapshot_con_una_unidad_que_el_catalogo_no_encuentra_tampoco_bloquea(client):
    """Issue 378: el caso real de 7JY7 -- snapshot completo (conjunto/torre/apartamento) pero con un Conjunto que el
    catálogo ya no tiene (renombrado). Cuenta como "sin apartamento": se recibe igual, nunca con el mensaje técnico."""
    from app.domain.apartamento_service import resolver_apartamento

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 4", "806")
    p = announce(
        client.db, anunciante_telefono="3001234567", anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(), apartamento=apto,
    )
    p.snapshot_conjunto = "NOMBRE VIEJO DEL CONJUNTO"
    client.db.commit()

    r = _recibir(client, p, candidato_idx="nuevo", nuevo_ocupante_nombre="Catalina Parra")

    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes?aviso=residente_sin_apartamento"
    assert _recargar(client, p).estado == EstadoPaquete.RECIBIDO


def test_tras_renombrar_el_conjunto_nuevo_residente_registra_en_la_unidad_del_paquete(client):
    """Issue 378, de punta a punta: anunciado con apartamento, el Admin renombra el Conjunto, y Recibir con "Nuevo
    residente" registra al Ocupante en esa misma unidad (antes: "no tiene apartamento resuelto")."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.configuracion_conjunto_service import renombrar_conjunto
    from app.domain.usuario import Usuario

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 4", "806")
    p = announce(
        client.db, anunciante_telefono="3001234567", anunciante_nombre="Ana",
        destinatario=Destinatario.yo_mismo(), apartamento=apto,
    )
    client.db.commit()
    admin = client.db.query(Usuario).filter(Usuario.email == "staff@club.com").one()
    renombrar_conjunto(client.db, "El Club Apartamentos", admin)
    client.db.commit()

    r = _recibir(
        client, p, candidato_idx="nuevo", nuevo_ocupante_nombre="Catalina Parra",
        nuevo_ocupante_contacto="3105559876",
    )

    assert r.status_code == 303
    assert r.headers["location"] == "/paquetes"
    assert _recargar(client, p).estado == EstadoPaquete.RECIBIDO
    ocupante = client.db.query(Ocupante).one()
    assert ocupante.apartamento_id == apto.id
