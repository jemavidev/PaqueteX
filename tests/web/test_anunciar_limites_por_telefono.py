# -*- coding: utf-8 -*-
"""
`/anunciar`: límites por teléfono y mensaje amigable (issue 385, `.scratch/pendientes-cliente`).

`/anunciar` es público y cada anuncio podía mandar un SMS a cualquier número. Ahora, por teléfono: máximo 3 anuncios
pendientes si nunca se le recibió un paquete (5 si ya tiene historial), máximo 5 anuncios por día, y un solo SMS de
"Anunciado" por día. Al llegar a un tope, el mensaje invita -- con tono amigable -- a activar la recepción automática
("Autorizo a Papyrus para recibir todos los paquetes a mi nombre", en Mis datos) o a pedírsela a portería.
"""

from datetime import datetime, timedelta, timezone

from app.domain.paquete import Paquete
from app.domain.paquete_lifecycle import deliver, receive
from app.domain.persona import Persona
from app.domain.staff_service import create_initial_admin
from app.web.notifications import get_notification_sender

_TEL = "3001234567"
_CANON = "+573001234567"


class _SenderEspia:
    def __init__(self):
        self.enviados = []

    def enviar(self, destino, mensaje):
        self.enviados.append(destino)
        return None


def _anunciar(client):
    return client.post(
        "/anunciar", data={"nombre": "Ana", "telefono": _TEL, "acepta_tyc": "on", "confirmar_multiple": "1"}
    )


def _staff(client):
    staff = create_initial_admin(client.db, "admin@club.com", "Admin", "Contrasena1")
    client.db.commit()
    return staff


def _dar_historial(client, staff):
    """Un paquete ya recibido y entregado para este teléfono, fuera de las últimas 24 h."""
    _anunciar(client)
    p = client.db.query(Paquete).one()
    receive(client.db, p, staff)
    deliver(client.db, p, staff)
    p.announced_at = datetime.now(timezone.utc) - timedelta(days=3)
    client.db.commit()


def _pendientes(client):
    client.db.expire_all()
    return client.db.query(Paquete).filter(Paquete.estado == "ANUNCIADO").count()


def test_sin_historial_el_tope_es_3_pendientes_con_mensaje_amigable(client):
    for _ in range(3):
        assert _anunciar(client).status_code == 200

    r = _anunciar(client)

    assert r.status_code == 400
    assert _pendientes(client) == 3
    assert "¡Ya tienes 3 paquetes anunciados esperando llegar!" in r.text
    assert "Autorizo a Papyrus para recibir todos los paquetes a mi nombre" in r.text
    assert "portería" in r.text


def test_con_historial_el_tope_es_5_pendientes(client):
    _dar_historial(client, _staff(client))
    for _ in range(5):
        assert _anunciar(client).status_code == 200

    r = _anunciar(client)

    assert r.status_code == 400
    assert _pendientes(client) == 5
    assert "¡Ya tienes 5 paquetes anunciados esperando llegar!" in r.text


def test_como_maximo_5_anuncios_por_dia_aunque_se_vayan_recibiendo(client):
    staff = _staff(client)
    _dar_historial(client, staff)
    for _ in range(5):
        assert _anunciar(client).status_code == 200
        for p in client.db.query(Paquete).filter(Paquete.estado == "ANUNCIADO").all():
            receive(client.db, p, staff)  # la cola se vacía: el tope que actúa es el diario
        client.db.commit()

    r = _anunciar(client)

    assert r.status_code == 400
    assert "Hoy ya anunciaste 5 paquetes" in r.text


def test_con_recepcion_automatica_activa_el_mensaje_dice_que_no_hace_falta_anunciar(client):
    for _ in range(3):
        _anunciar(client)
    persona = client.db.query(Persona).filter(Persona.telefono == _CANON).one()
    persona.autoriza_recepcion_automatica = True
    client.db.commit()

    r = _anunciar(client)

    assert r.status_code == 400
    assert "Como tienes activada la recepción automática" in r.text
    assert "Autorizo a Papyrus" not in r.text


def test_solo_un_sms_de_anunciado_por_telefono_por_dia(client):
    espia = _SenderEspia()
    client.app.dependency_overrides[get_notification_sender] = lambda: espia

    _anunciar(client)
    _anunciar(client)
    _anunciar(client)

    assert _pendientes(client) == 3  # los tres anuncios se registraron...
    assert espia.enviados == [_CANON]  # ...pero solo el primero mandó SMS


def test_el_sms_vuelve_a_salir_al_dia_siguiente(client):
    espia = _SenderEspia()
    client.app.dependency_overrides[get_notification_sender] = lambda: espia
    _anunciar(client)
    p = client.db.query(Paquete).one()
    p.announced_at = datetime.now(timezone.utc) - timedelta(hours=25)
    client.db.commit()

    _anunciar(client)

    assert espia.enviados == [_CANON, _CANON]
