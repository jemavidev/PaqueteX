# -*- coding: utf-8 -*-
"""
Capa web — "Primera entrega" para un cliente identificado solo por usuario de WhatsApp (issue 379,
`.scratch/pendientes-cliente`).

La regla se decidía solo por `Paquete.recipient_phone`: un cliente solo-WhatsApp (ADR-0007) no tenía teléfono, así
que nunca veía la bandera "Primera entrega a este cliente" y se le cobraba la tarifa base desde el primer paquete.
Ahora el Paquete guarda también el WhatsApp propio del destinatario (`recipient_whatsapp`): con teléfono se decide
por teléfono (sin cambios); sin teléfono, por WhatsApp; sin ninguno, se cobra y el modal Entregar lo avisa.
"""

from app.domain.cobro import Cobro
from app.domain.paquete import Paquete
from app.domain.paquete_lifecycle import deliver as dom_deliver
from app.domain.paquete_lifecycle import receive as dom_receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin
from app.domain.usuario import Usuario

_PW = "Contrasena1"
_BANDERA = "Primera entrega a este cliente"
_NO_VERIFICABLE = "No se puede verificar si es la primera entrega"


def _login_staff(client, email="staff@club.com"):
    create_initial_admin(client.db, email, "Operador", _PW)
    client.db.commit()
    r = client.post("/ingresar", data={"email": email, "password": _PW})
    assert r.status_code == 200
    return client.db.query(Usuario).filter(Usuario.email == email).one()


def _anunciar_por_whatsapp(client, whatsapp="juan.test", nombre="Juan Test"):
    p = announce(
        client.db,
        anunciante_whatsapp=whatsapp,
        anunciante_nombre=nombre,
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    return p


def _recibido(client, staff, p):
    dom_receive(client.db, p, staff)
    client.db.commit()
    return p


def _entregado(client, staff, p):
    _recibido(client, staff, p)
    dom_deliver(client.db, p, staff)
    client.db.commit()
    return p


def _cobro(client, p):
    return client.db.query(Cobro).filter(Cobro.paquete_id == p.id).one()


def test_anunciar_por_whatsapp_guarda_el_whatsapp_del_destinatario(client):
    _login_staff(client)
    p = _anunciar_por_whatsapp(client)

    assert p.recipient_phone is None
    assert p.recipient_whatsapp == "juan.test"


def test_primera_entrega_por_whatsapp_muestra_la_bandera_y_no_cobra_la_base(client):
    staff = _login_staff(client)
    p = _recibido(client, staff, _anunciar_por_whatsapp(client))

    r = client.get("/paquetes", params={"q": p.access_code})
    assert _BANDERA in r.text
    assert _NO_VERIFICABLE not in r.text

    client.post(f"/paquetes/{p.id}/entregar", follow_redirects=False)
    assert _cobro(client, p).monto_base == 0


def test_segunda_entrega_al_mismo_whatsapp_cobra_y_no_muestra_la_bandera(client):
    staff = _login_staff(client)
    _entregado(client, staff, _anunciar_por_whatsapp(client))
    p = _recibido(client, staff, _anunciar_por_whatsapp(client))

    r = client.get("/paquetes", params={"q": p.access_code})
    assert _BANDERA not in r.text

    client.post(f"/paquetes/{p.id}/entregar", follow_redirects=False)
    assert _cobro(client, p).monto_base == 1500


def test_otro_whatsapp_no_cuenta_como_entrega_previa(client):
    staff = _login_staff(client)
    _entregado(client, staff, _anunciar_por_whatsapp(client, whatsapp="otra.persona", nombre="Otra Persona"))
    p = _recibido(client, staff, _anunciar_por_whatsapp(client))

    client.post(f"/paquetes/{p.id}/entregar", follow_redirects=False)
    assert _cobro(client, p).monto_base == 0


def test_consultar_tambien_muestra_la_bandera_por_whatsapp(client):
    staff = _login_staff(client)
    p = _recibido(client, staff, _anunciar_por_whatsapp(client))

    r = client.get("/consultar", params={"q": p.access_code})
    assert _BANDERA in r.text


def test_sin_telefono_ni_whatsapp_cobra_y_avisa_que_no_se_puede_verificar(client):
    staff = _login_staff(client)
    p = announce(
        client.db, anunciante_whatsapp="anunciante.x", anunciante_nombre="Anunciante X",
        destinatario=Destinatario.solo_nombre("Solo Un Nombre"),
    )
    client.db.commit()
    assert p.recipient_phone is None and p.recipient_whatsapp is None
    _recibido(client, staff, p)

    r = client.get("/paquetes", params={"q": p.access_code})
    assert _BANDERA not in r.text
    assert _NO_VERIFICABLE in r.text

    client.post(f"/paquetes/{p.id}/entregar", follow_redirects=False)
    assert _cobro(client, p).monto_base == 1500


def test_con_telefono_no_aparece_el_aviso_de_no_verificable(client):
    staff = _login_staff(client)
    p = announce(
        client.db, anunciante_telefono="3005556666", anunciante_nombre="Con Telefono",
        destinatario=Destinatario.yo_mismo(),
    )
    client.db.commit()
    _recibido(client, staff, p)

    r = client.get("/paquetes", params={"q": p.access_code})
    assert _BANDERA in r.text
    assert _NO_VERIFICABLE not in r.text


def test_corregir_destinatario_a_un_residente_solo_whatsapp_copia_su_whatsapp(client):
    """El WhatsApp sigue al destinatario corregido: si no, el paquete quedaría juzgado con el WhatsApp de otro."""
    from app.domain.apartamento_service import resolver_apartamento
    from app.domain.ocupante_service import agregar_ocupante

    _login_staff(client)
    apto = resolver_apartamento(client.db, "TORRE 1", "101")
    agregar_ocupante(client.db, apto, "Residente Wa", whatsapp_usuario="residente.wa")
    client.db.commit()
    p = announce(
        client.db, anunciante_telefono="3001234567", anunciante_nombre="Portero",
        destinatario=Destinatario.declarado_por_cliente("Alguien Mas"), apartamento=apto,
    )
    client.db.commit()
    from app.domain.paquete_correccion_service import candidatos_correccion

    idx = next(
        i for i, c in enumerate(candidatos_correccion(client.db, p)) if c["nombre"] == "RESIDENTE WA"
    )

    r = client.post(f"/paquetes/{p.id}/corregir", data={"candidato_idx": str(idx)}, follow_redirects=False)

    assert r.status_code == 303
    client.db.expire_all()
    p2 = client.db.get(Paquete, p.id)
    assert p2.recipient_name == "RESIDENTE WA"
    assert p2.recipient_whatsapp == "residente.wa"


def test_migracion_llena_el_whatsapp_de_paquetes_para_el_propio_anunciante(client):
    import importlib.util
    from pathlib import Path

    _login_staff(client)
    p = _anunciar_por_whatsapp(client)
    p.recipient_whatsapp = None  # como quedaron los paquetes anteriores a la columna
    client.db.commit()

    ruta = next(Path(__file__).resolve().parents[2].glob("alembic/versions/0058_*.py"))
    spec = importlib.util.spec_from_file_location("migracion_0058", ruta)
    migracion = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migracion)
    migracion.llenar_recipient_whatsapp(client.db.connection())
    client.db.commit()

    client.db.expire_all()
    assert client.db.get(Paquete, p.id).recipient_whatsapp == "juan.test"
