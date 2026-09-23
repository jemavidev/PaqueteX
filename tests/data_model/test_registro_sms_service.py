# -*- coding: utf-8 -*-
"""
Servicio de dominio de `RegistroSms` (`.scratch/estadisticas-cobro-
dashboard`, ticket 11) -- contar por proveedor/tipo/rango de fechas, y la
fecha del primer registro que exista. La integración real (quién llama a
`registrar_envio` y cuándo) se prueba en `test_notificacion_service.py`;
acá solo la aritmética del servicio en sí, sobre filas ya sembradas
directo.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.registro_sms import RegistroSms, TipoRegistroSms
from app.domain.registro_sms_service import contar_envios, fecha_primer_registro, registrar_envio

pytestmark = pytest.mark.integration


def _local(anio, mes, dia, hora=12):
    from app.domain.zona_horaria import ZONA_HORARIA_APP

    return datetime(anio, mes, dia, hora, 0, 0, tzinfo=ZONA_HORARIA_APP).astimezone(timezone.utc)


def _sembrar(session, tipo, exitoso, proveedor=None, cuando=None):
    registrar_envio(session, tipo, exitoso, proveedor=proveedor)
    if cuando is not None:
        registro = session.query(RegistroSms).order_by(RegistroSms.created_at.desc()).first()
        registro.created_at = cuando
        session.flush()


def test_contar_envios_sin_filtros_es_el_total(db_session):
    _sembrar(db_session, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="AWS_SNS")
    _sembrar(db_session, TipoRegistroSms.AVISO_PAQUETE, False)
    db_session.commit()

    assert contar_envios(db_session) == 2


def test_contar_envios_filtra_por_proveedor(db_session):
    _sembrar(db_session, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="AWS_SNS")
    _sembrar(db_session, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="LIWA")
    db_session.commit()

    assert contar_envios(db_session, proveedor="AWS_SNS") == 1
    assert contar_envios(db_session, proveedor="LIWA") == 1
    assert contar_envios(db_session, proveedor="TWILIO") == 0


def test_contar_envios_filtra_por_tipo_y_exitoso(db_session):
    _sembrar(db_session, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="AWS_SNS")
    _sembrar(db_session, TipoRegistroSms.AVISO_PAQUETE, False)
    db_session.commit()

    assert contar_envios(db_session, tipo=TipoRegistroSms.AVISO_PAQUETE, exitoso=True) == 1
    assert contar_envios(db_session, tipo=TipoRegistroSms.AVISO_PAQUETE, exitoso=False) == 1


def test_contar_envios_filtra_por_rango_de_fechas(db_session):
    _sembrar(db_session, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="AWS_SNS", cuando=_local(2026, 9, 1))
    _sembrar(db_session, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="AWS_SNS", cuando=_local(2026, 9, 16))
    db_session.commit()

    assert contar_envios(db_session, desde=_local(2026, 9, 15), hasta=_local(2026, 9, 20)) == 1
    assert contar_envios(db_session, desde=_local(2026, 9, 1), hasta=_local(2026, 9, 30)) == 2


def test_fecha_primer_registro(db_session):
    _sembrar(db_session, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="AWS_SNS", cuando=_local(2026, 9, 16))
    _sembrar(db_session, TipoRegistroSms.AVISO_PAQUETE, True, proveedor="AWS_SNS", cuando=_local(2026, 9, 1))
    db_session.commit()

    assert fecha_primer_registro(db_session) == _local(2026, 9, 1)


def test_fecha_primer_registro_sin_ninguno_es_none(db_session):
    assert fecha_primer_registro(db_session) is None


def test_contar_envios_con_base_vacia_es_cero(db_session):
    assert contar_envios(db_session) == 0
