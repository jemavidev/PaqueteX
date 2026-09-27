# -*- coding: utf-8 -*-
"""
Capa web — fotos en cola exentas del Bloqueo (`.scratch/pin-operador-dispositivo`, ticket 05).

La cola del equipo (`asociar=1`) sigue subiendo aunque el equipo esté bloqueado: solo exige un registro de dispositivo
vigente, y sus peticiones no cuentan como actividad. La subida progresiva desde el modal Recibir (sin `asociar`) es
una acción del Usuario y sigue exigiendo el desbloqueo, igual que cualquier otra ruta de Paquete.
"""

import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from app.domain import operador_dispositivo_service as ods
from app.domain.paquete_foto import PaqueteFoto
from app.domain.paquete_lifecycle import receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin
from app.web.security import MARGEN_AVISO_SEGUNDOS

pytestmark = pytest.mark.pin_manual

_PW = "Contrasena1"
_FETCH_AUTOMATICO = {"Sec-Fetch-Mode": "cors", "X-PaqueteX-Automatico": "1"}


def _jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color=(120, 90, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture()
def reloj(monkeypatch):
    estado = {"ahora": datetime.now(timezone.utc)}
    monkeypatch.setattr(ods, "_ahora", lambda: estado["ahora"])
    return estado


def _preparar(client, recibido=True):
    admin = create_initial_admin(client.db, "staff@club.com", "Operador", _PW)
    ods.definir_pin(client.db, admin, "1234")
    p = announce(client.db, anunciante_telefono="3001234567", anunciante_nombre="Ana",
                 destinatario=Destinatario.yo_mismo())
    if recibido:
        receive(client.db, p, admin, None)
    client.db.commit()
    client.post("/ingresar", data={"email": "staff@club.com", "password": _PW})
    return p


def _subir(client, p, asociar=True):
    return client.post(
        f"/paquetes/{p.id}/fotos",
        data={"asociar": "1"} if asociar else {},
        files={"foto": ("foto.jpg", _jpeg(), "image/jpeg")},
        headers=_FETCH_AUTOMATICO,
        follow_redirects=False,
    )


def _fotos(client, p):
    client.db.expire_all()
    return client.db.query(PaqueteFoto).filter(PaqueteFoto.paquete_id == p.id).count()


def test_la_cola_sube_con_el_equipo_bloqueado(client):
    p = _preparar(client)
    client.post("/bloquear")
    r = _subir(client, p)
    assert r.status_code == 200
    assert _fotos(client, p) == 1


def test_la_cola_sube_tras_la_inactividad(client, reloj):
    p = _preparar(client)
    reloj["ahora"] += timedelta(seconds=300 + MARGEN_AVISO_SEGUNDOS + 5)
    assert _subir(client, p).status_code == 200
    assert _fotos(client, p) == 1


def test_sin_registro_de_dispositivo_la_cola_no_sube(client):
    p = _preparar(client)
    client.cookies.delete("paquetex_dispositivo")
    r = _subir(client, p)
    assert r.status_code in (401, 303)
    assert _fotos(client, p) == 0


def test_la_subida_de_la_cola_no_renueva_la_actividad(client, reloj):
    p = _preparar(client)
    reloj["ahora"] += timedelta(seconds=300)
    assert _subir(client, p).status_code == 200
    reloj["ahora"] += timedelta(seconds=MARGEN_AVISO_SEGUNDOS + 5)
    assert client.get("/paquetes", headers={"Sec-Fetch-Mode": "cors"}).status_code == 423


def test_la_subida_progresiva_del_modal_sigue_exigiendo_el_desbloqueo(client):
    p = _preparar(client, recibido=False)
    client.post("/bloquear")
    r = _subir(client, p, asociar=False)
    assert r.status_code == 423


def test_otra_ruta_de_paquete_con_el_equipo_bloqueado_sigue_rechazada(client):
    p = _preparar(client, recibido=False)
    client.post("/bloquear")
    r = client.post(f"/paquetes/{p.id}/recibir", data={"posicion": "41"}, headers={"Sec-Fetch-Mode": "cors"})
    assert r.status_code == 423
