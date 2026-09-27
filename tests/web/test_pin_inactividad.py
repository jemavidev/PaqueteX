# -*- coding: utf-8 -*-
"""
Capa web — Bloqueo por inactividad, lado servidor (`.scratch/pin-operador-dispositivo`, ticket 04).

El navegador avisa de la actividad local como máximo una vez por minuto, así que el servidor bloquea tras los segundos
configurados MÁS ese minuto de margen (`MARGEN_AVISO_SEGUNDOS`): quien manda el Bloqueo en pantalla es el navegador, el
servidor es la red de seguridad. Un `fetch` bloqueado recibe 423 (el cliente lo convierte en la capa de bloqueo); una
navegación, la pantalla de bloqueo. En ambos casos la acción no se aplica.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain import operador_dispositivo_service as ods
from app.domain.configuracion_conjunto_service import actualizar_seguridad_sesion
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin, create_staff
from app.domain.usuario import RolUsuario
from app.web.security import MARGEN_AVISO_SEGUNDOS

pytestmark = pytest.mark.pin_manual

_PW = "Contrasena1"
_FETCH = {"Sec-Fetch-Mode": "cors"}
_AUTOMATICO = {"Sec-Fetch-Mode": "cors", "X-PaqueteX-Automatico": "1"}


class _Reloj:
    def __init__(self, monkeypatch):
        self.ahora = datetime.now(timezone.utc)
        monkeypatch.setattr(ods, "_ahora", lambda: self.ahora)

    def avanzar(self, segundos):
        self.ahora += timedelta(seconds=segundos)


@pytest.fixture()
def reloj(monkeypatch):
    return _Reloj(monkeypatch)


def _sembrar(client):
    admin = create_initial_admin(client.db, "admin@club.com", "Admin", _PW)
    ana = create_staff(client.db, admin, "ana@club.com", "Ana", _PW, RolUsuario.OPERADOR)
    beto = create_staff(client.db, admin, "beto@club.com", "Beto", _PW, RolUsuario.OPERADOR)
    ods.definir_pin(client.db, admin, "1000")
    ods.definir_pin(client.db, ana, "1111")
    ods.definir_pin(client.db, beto, "2222")
    client.db.commit()
    return admin, ana, beto


def _ingresar(client, email):
    client.post("/ingresar", data={"email": email, "password": _PW})


_LIMITE = 300 + MARGEN_AVISO_SEGUNDOS


def test_una_navegacion_tras_la_inactividad_va_a_la_pantalla_de_bloqueo(client, reloj):
    _sembrar(client)
    _ingresar(client, "ana@club.com")
    reloj.avanzar(_LIMITE - 1)
    assert client.get("/paquetes", follow_redirects=False).status_code == 200
    reloj.avanzar(_LIMITE + 1)
    r = client.get("/paquetes", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/bloqueo?siguiente=/paquetes"


def test_un_fetch_bloqueado_recibe_423_y_la_accion_no_se_aplica(client, reloj):
    _sembrar(client)
    paquete = announce(
        client.db, anunciante_telefono="3001234567", anunciante_nombre="Carla", destinatario=Destinatario.yo_mismo()
    )
    client.db.commit()
    _ingresar(client, "ana@club.com")
    reloj.avanzar(_LIMITE + 1)

    r = client.post(f"/paquetes/{paquete.id}/recibir", data={"posicion": "41"}, headers=_FETCH, follow_redirects=False)
    assert r.status_code == 423
    assert r.headers["X-PaqueteX-Bloqueo"] == "1"
    client.db.expire_all()
    assert client.db.get(Paquete, paquete.id).estado == EstadoPaquete.ANUNCIADO


def test_las_peticiones_del_usuario_y_el_aviso_de_actividad_renuevan_la_marca(client, reloj):
    _sembrar(client)
    _ingresar(client, "ana@club.com")
    for _ in range(3):
        reloj.avanzar(_LIMITE - 10)
        assert client.post("/actividad", headers=_FETCH).status_code == 204
    for _ in range(3):
        reloj.avanzar(_LIMITE - 10)
        assert client.get("/paquetes", follow_redirects=False).status_code == 200


def test_una_peticion_automatica_no_renueva_la_marca(client, reloj):
    _sembrar(client)
    _ingresar(client, "ana@club.com")
    reloj.avanzar(_LIMITE - 10)
    assert client.get("/paquetes", headers=_AUTOMATICO, follow_redirects=False).status_code == 200
    reloj.avanzar(20)
    assert client.get("/paquetes", headers=_FETCH, follow_redirects=False).status_code == 423


def test_el_aviso_de_actividad_con_el_equipo_bloqueado_devuelve_la_senal(client, reloj):
    _sembrar(client)
    _ingresar(client, "ana@club.com")
    reloj.avanzar(_LIMITE + 1)
    r = client.post("/actividad", headers=_FETCH)
    assert r.status_code == 423
    reloj.avanzar(1)
    # Bloqueado es bloqueado: ni un aviso posterior lo reabre.
    assert client.post("/actividad", headers=_FETCH).status_code == 423


def test_un_cambio_de_configuracion_aplica_en_la_siguiente_peticion(client, reloj):
    admin, _, _ = _sembrar(client)
    _ingresar(client, "ana@club.com")
    reloj.avanzar(60 + MARGEN_AVISO_SEGUNDOS + 5)
    assert client.get("/paquetes", follow_redirects=False).status_code == 200
    actualizar_seguridad_sesion(client.db, segundos_inactividad=60, dias_registro_dispositivo=15, actor=admin)
    client.db.commit()
    reloj.avanzar(60 + MARGEN_AVISO_SEGUNDOS + 5)
    assert client.get("/paquetes", follow_redirects=False).status_code == 303


def test_la_pagina_lleva_los_segundos_configurados_para_el_contador_del_navegador(client, reloj):
    _sembrar(client)
    _ingresar(client, "ana@club.com")
    assert 'data-segundos-inactividad="300"' in client.get("/paquetes").text


def test_desbloquear_desde_la_capa_dice_si_cambio_el_operador(client, reloj):
    _sembrar(client)
    _ingresar(client, "ana@club.com")
    _ingresar(client, "beto@club.com")  # ahora el Operador activo es Beto
    reloj.avanzar(_LIMITE + 1)
    assert client.post("/actividad", headers=_FETCH).status_code == 423

    r = client.post("/bloqueo", data={"pin": "2222", "siguiente": "/paquetes"}, headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.json() == {"mismo_operador": True, "destino": "/paquetes", "es_admin": False}

    reloj.avanzar(_LIMITE + 1)
    client.post("/actividad", headers=_FETCH)
    r = client.post("/bloqueo", data={"pin": "1111", "siguiente": "/paquetes"}, headers={"Accept": "application/json"})
    assert r.json() == {"mismo_operador": False, "destino": "/paquetes", "es_admin": False}

    r = client.post("/bloqueo", data={"pin": "9999", "siguiente": "/paquetes"}, headers={"Accept": "application/json"})
    assert r.status_code == 400
    assert r.json() == {"error": "PIN incorrecto."}


def test_bloqueado_no_muestra_nada_de_staff(client, reloj):
    _sembrar(client)
    _ingresar(client, "ana@club.com")
    client.post("/bloquear")
    texto = client.get("/bloqueo").text
    assert 'action="/bloquear"' not in texto  # sin menú de cuenta de staff
    assert 'data-segundos-inactividad' not in texto  # sin contador en la propia pantalla de bloqueo


def test_vencida_la_inactividad_la_busqueda_ya_no_muestra_la_vista_de_staff(client, reloj):
    """Hallazgo de la revisión: `/consultar` y `/entrar` miran la sesión sin pasar por `current_staff`. El middleware
    bloquea el equipo antes de cualquier ruta, así que tampoco ellas ven un Operador activo vencido."""
    _sembrar(client)
    _ingresar(client, "ana@club.com")
    assert client.get("/entrar", follow_redirects=False).headers["location"] == "/paquetes"
    reloj.avanzar(_LIMITE + 1)
    r = client.get("/entrar", follow_redirects=False)
    assert r.status_code == 200  # ya no la trata como staff con sesión
