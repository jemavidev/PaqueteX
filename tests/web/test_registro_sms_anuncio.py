# -*- coding: utf-8 -*-
"""
Capa web — el SMS de "Anunciado" queda registrado, y borrar ese paquete no falla (issue 380, `.scratch/pendientes-cliente`).

Con FastAPI 0.104 el commit de `get_db` corre DESPUÉS de las BackgroundTasks: la tarea que envía el SMS intentaba
registrar el envío apuntando a un Paquete que todavía no estaba guardado, la llave foránea lo rechazaba y
`registrar_envio` se tragaba el error -- el SMS salía, pero el tablero de SMS no lo contaba. Arreglado eso, borrar un
Anunciado con su SMS registrado habría dado 500 (la FK no tenía `ON DELETE`): ahora el registro se conserva sin paquete.
"""

import sqlalchemy as sa

from app.domain.paquete import Paquete
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario
from app.web.notifications import get_notification_sender

_PW = "Contrasena1"


class _ProveedorQueConfirma:
    """Como un proveedor real: confirma el envío, así que debe quedar en `registros_sms`."""

    def enviar(self, destino, mensaje):
        return "LIWA"


def _registros(client):
    client.db.expire_all()
    return client.db.execute(sa.text("select paquete_id, evento, exitoso from registros_sms")).all()


def _con_proveedor(client):
    client.app.dependency_overrides[get_notification_sender] = lambda: _ProveedorQueConfirma()


def test_anunciar_publico_registra_el_sms_del_anuncio(client):
    _con_proveedor(client)

    client.post("/anunciar", data={"nombre": "Ana Prueba", "telefono": "3001234567", "acepta_tyc": "1"})

    p = client.db.query(Paquete).one()
    assert _registros(client) == [(p.id, "ANUNCIADO", True)]


def test_anunciar_desde_staff_registra_el_sms_del_anuncio(client):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    create_staff(client.db, admin, "op@club.com", "Opa", _PW, RolUsuario.OPERADOR)
    client.db.commit()
    client.post("/ingresar", data={"email": "op@club.com", "password": _PW})
    _con_proveedor(client)

    client.post("/announce", data={"telefono": "3001234567", "nombre": "Ana"})

    p = client.db.query(Paquete).one()
    assert _registros(client) == [(p.id, "ANUNCIADO", True)]


def test_eliminar_un_anunciado_con_su_sms_registrado_no_falla_y_conserva_el_registro(client):
    _con_proveedor(client)
    client.post("/anunciar", data={"nombre": "Ana Prueba", "telefono": "3001234567", "acepta_tyc": "1"})
    p = client.db.query(Paquete).one()
    assert len(_registros(client)) == 1
    create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": "admin@club.com", "password": _PW})

    r = client.post(f"/paquetes/{p.id}/eliminar", follow_redirects=False)

    assert r.status_code == 303
    assert client.db.query(Paquete).count() == 0
    assert _registros(client) == [(None, "ANUNCIADO", True)]  # el costo del SMS no se pierde
