# -*- coding: utf-8 -*-
"""
Seam A — Configuración de proveedores de notificación (habilitado/orden),
`.scratch/administracion-proveedores/spec.md`, issue 01.

Comportamiento observable: guardar habilitado/orden para un `(canal,
proveedor)` crea o actualiza su fila, y deja un registro append-only en el
historial con el actor y el valor completo de antes/después; sin actor
(`usuario_id=None`) es honesto, no un dato inventado; `listar_config`
devuelve las filas de un canal ordenadas por precedencia.
"""

import pytest

from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.proveedor_config import ProveedorConfig
from app.domain.proveedor_config_historial import ProveedorConfigHistorial
from app.domain.proveedor_config_service import (
    armar_candidatos,
    guardar_habilitado_orden,
    habilitado_orden_efectivos,
    listar_config,
)
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration


def _usuario(session) -> Usuario:
    u = Usuario(nombre="Admin", rol=RolUsuario.ADMIN)
    session.add(u)
    session.flush()
    return u


# "OTRO_SMS": una clave que la migración de siembra NO crea (a diferencia de
# AWS_SNS/LIWA/TWILIO/SMTP, ya sembrados) -- simula un proveedor agregado al
# catálogo de código DESPUÉS de esa migración, para ejercitar el camino de
# "crear fila nueva" sin chocar con datos ya sembrados.


def test_guardar_en_proveedor_nuevo_crea_la_fila(db_session):
    config = guardar_habilitado_orden(
        db_session, CanalNotificacion.SMS, "OTRO_SMS", habilitado=False, orden=2
    )

    assert config.canal == "SMS"
    assert config.proveedor == "OTRO_SMS"
    assert config.habilitado is False
    assert config.orden == 2


def test_guardar_en_proveedor_nuevo_deja_historial_con_anterior_none(db_session):
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "OTRO_SMS", habilitado=True, orden=1)

    fila = (
        db_session.query(ProveedorConfigHistorial)
        .filter_by(canal="SMS", proveedor="OTRO_SMS")
        .one()
    )
    assert fila.habilitado_anterior is None
    assert fila.habilitado_nuevo is True
    assert fila.orden_anterior is None
    assert fila.orden_nuevo == 1


def test_guardar_de_nuevo_actualiza_la_misma_fila_no_crea_otra(db_session):
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "TWILIO", habilitado=True, orden=3)
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "TWILIO", habilitado=False, orden=3)

    filas = db_session.query(ProveedorConfig).filter_by(canal="SMS", proveedor="TWILIO").all()
    assert len(filas) == 1
    assert filas[0].habilitado is False


def test_guardar_de_nuevo_el_historial_captura_el_anterior_correcto(db_session):
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "TWILIO", habilitado=True, orden=3)
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "TWILIO", habilitado=False, orden=1)

    historial = (
        db_session.query(ProveedorConfigHistorial)
        .filter_by(canal="SMS", proveedor="TWILIO")
        .order_by(ProveedorConfigHistorial.created_at)
        .all()
    )
    assert len(historial) == 2
    segundo = historial[1]
    assert segundo.habilitado_anterior is True
    assert segundo.habilitado_nuevo is False
    assert segundo.orden_anterior == 3
    assert segundo.orden_nuevo == 1


def test_historial_es_append_only_nunca_se_pisa(db_session):
    for i in range(3):
        guardar_habilitado_orden(
            db_session, CanalNotificacion.EMAIL, "SMTP", habilitado=True, orden=None
        )

    assert (
        db_session.query(ProveedorConfigHistorial).filter_by(canal="EMAIL", proveedor="SMTP").count()
        == 3
    )


def test_guardar_sin_actor_deja_usuario_id_null_honesto(db_session):
    config = guardar_habilitado_orden(
        db_session, CanalNotificacion.SMS, "AWS_SNS", habilitado=True, orden=1
    )

    assert config.updated_by is None
    historial = db_session.query(ProveedorConfigHistorial).one()
    assert historial.usuario_id is None


def test_guardar_con_actor_lo_registra_en_config_y_en_historial(db_session):
    admin = _usuario(db_session)

    config = guardar_habilitado_orden(
        db_session,
        CanalNotificacion.SMS,
        "AWS_SNS",
        habilitado=True,
        orden=1,
        usuario_id=admin.id,
    )

    assert config.updated_by == admin.id
    historial = db_session.query(ProveedorConfigHistorial).one()
    assert historial.usuario_id == admin.id


def test_listar_config_devuelve_solo_el_canal_pedido_ordenado_por_precedencia(db_session):
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "TWILIO", habilitado=True, orden=3)
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "AWS_SNS", habilitado=True, orden=1)
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "LIWA", habilitado=True, orden=2)
    guardar_habilitado_orden(db_session, CanalNotificacion.EMAIL, "SMTP", habilitado=True, orden=None)

    filas = listar_config(db_session, CanalNotificacion.SMS)

    assert [f.proveedor for f in filas] == ["AWS_SNS", "LIWA", "TWILIO"]


# --------------------------------------------------------------------------- #
# armar_candidatos -- issue 02: combina el catálogo (orden por defecto) con
# habilitado/orden de la BD, lista para `sms_failover.construir_sender()`.
# --------------------------------------------------------------------------- #


def test_armar_candidatos_respeta_el_orden_sembrado_por_la_migracion(db_session):
    # La migración 0037 siembra AWS_SNS=1, LIWA=2, TWILIO=3 -- mismo orden
    # histórico que tenía el código antes de esta feature.
    proveedores = [
        ("AWS_SNS", True, "sender-sns"),
        ("LIWA", True, "sender-liwa"),
        ("TWILIO", True, "sender-twilio"),
    ]
    resultado = armar_candidatos(db_session, CanalNotificacion.SMS, proveedores)
    assert resultado == [(True, "sender-sns"), (True, "sender-liwa"), (True, "sender-twilio")]


def test_armar_candidatos_orden_distinto_en_bd_cambia_el_orden_real(db_session):
    # Ticket 02, criterio explícito: sembrar un orden DISTINTO al histórico
    # (Twilio primero) y verificar que el resultado lo respeta -- prueba que
    # de verdad lee la BD, no un fallback oculto a la constante vieja.
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "TWILIO", habilitado=True, orden=1)
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "AWS_SNS", habilitado=True, orden=2)
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "LIWA", habilitado=True, orden=3)

    proveedores = [
        ("AWS_SNS", True, "sender-sns"),
        ("LIWA", True, "sender-liwa"),
        ("TWILIO", True, "sender-twilio"),
    ]
    resultado = armar_candidatos(db_session, CanalNotificacion.SMS, proveedores)

    assert resultado == [(True, "sender-twilio"), (True, "sender-sns"), (True, "sender-liwa")]


def test_armar_candidatos_deshabilitado_en_bd_excluye_aunque_este_configurado(db_session):
    # Los tres CON fila explícita (escenario realista -- la migración 0037
    # siempre siembra los tres) -- un `orden` explícito solo en LIWA
    # adelantaría a los otros dos (sin fila = sin orden, va al final), que
    # no es lo que este test quiere demostrar.
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "AWS_SNS", habilitado=True, orden=1)
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "LIWA", habilitado=False, orden=2)
    guardar_habilitado_orden(db_session, CanalNotificacion.SMS, "TWILIO", habilitado=True, orden=3)

    proveedores = [("AWS_SNS", True, "sns"), ("LIWA", True, "liwa"), ("TWILIO", True, "twilio")]
    resultado = armar_candidatos(db_session, CanalNotificacion.SMS, proveedores)

    assert resultado == [(True, "sns"), (False, "liwa"), (True, "twilio")]


def test_armar_candidatos_habilitado_en_bd_pero_sin_credenciales_excluye(db_session):
    # habilitado=True en BD, pero `esta_configurado=False` (sin credenciales
    # completas en .env) -- las dos condiciones son necesarias a la vez.
    proveedores = [("AWS_SNS", True, "sns"), ("LIWA", False, "liwa"), ("TWILIO", True, "twilio")]
    resultado = armar_candidatos(db_session, CanalNotificacion.SMS, proveedores)

    assert resultado == [(True, "sns"), (False, "liwa"), (True, "twilio")]


def test_armar_candidatos_sin_fila_en_bd_asume_habilitado_por_defecto(db_session):
    # "OTRO_SMS" no está en el catálogo real ni sembrado por la migración --
    # simula un proveedor agregado al catálogo DESPUÉS de esa migración, sin
    # guardado explícito todavía. Mismo comportamiento implícito de antes de
    # esta feature: la sola presencia de credenciales bastaba.
    proveedores = [("OTRO_SMS", True, "otro")]
    resultado = armar_candidatos(db_session, CanalNotificacion.SMS, proveedores)

    assert resultado == [(True, "otro")]


def test_habilitado_orden_efectivos_sin_config_asume_habilitado_true_orden_none():
    assert habilitado_orden_efectivos(None) == (True, None)


def test_habilitado_orden_efectivos_con_config_devuelve_sus_valores(db_session):
    config = guardar_habilitado_orden(
        db_session, CanalNotificacion.SMS, "AWS_SNS", habilitado=False, orden=2
    )
    assert habilitado_orden_efectivos(config) == (False, 2)
