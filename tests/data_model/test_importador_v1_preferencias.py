# -*- coding: utf-8 -*-
"""
Importador espejo v1 → v2 (`.scratch/importador-v1-espejo`, ticket 05, decisión
2026-09-24 -- opción C): las preferencias de notificación de la v1 NO se
importan. Toda Persona importada queda con el default de la v2 (SMS solo en
Anunciado), y lo que el residente configure en la v2 nunca lo pisa el
importador. Seam: `sincronizar_desde_v1` + `preferencia_activa`.
"""

import pytest

from app.domain.importador_v1_service import ClienteV1, InstantaneaV1, sincronizar_desde_v1
from app.domain.paquete import EstadoPaquete
from app.domain.persona import Persona
from app.domain.preferencia_notificacion import CanalNotificacion, PersonaPreferenciaNotificacion
from app.domain.preferencia_notificacion_service import guardar_preferencia, preferencia_activa

pytestmark = pytest.mark.integration

INSTANTANEA = InstantaneaV1(clientes=[ClienteV1(id="c-1", telefono="+573001112233", nombre="Juan Perez")])


def _persona(db_session):
    return db_session.query(Persona).filter_by(origen_v1_id="c-1").one()


def test_la_persona_importada_queda_con_el_default_de_la_v2(db_session):
    sincronizar_desde_v1(db_session, INSTANTANEA)
    persona = _persona(db_session)

    assert db_session.query(PersonaPreferenciaNotificacion).count() == 0
    assert preferencia_activa(db_session, persona.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO) is True
    for evento in (EstadoPaquete.RECIBIDO, EstadoPaquete.ENTREGADO, EstadoPaquete.CANCELADO):
        assert preferencia_activa(db_session, persona.id, CanalNotificacion.SMS, evento) is False


def test_lo_que_el_residente_configura_en_la_v2_sobrevive_a_la_siguiente_pasada(db_session):
    sincronizar_desde_v1(db_session, INSTANTANEA)
    persona = _persona(db_session)
    guardar_preferencia(db_session, persona.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO, False)
    db_session.flush()

    sincronizar_desde_v1(db_session, INSTANTANEA)

    assert preferencia_activa(db_session, persona.id, CanalNotificacion.SMS, EstadoPaquete.ANUNCIADO) is False
