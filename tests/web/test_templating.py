# -*- coding: utf-8 -*-
"""
Capa web — `hora_local` (conversación 2026-08-14): la BD guarda todo en UTC
(`_utcnow()` en cada modelo de dominio); las plantillas deben convertir a
Bogotá/Lima/Quito (UTC-5 fijo, sin horario de verano nunca) antes de
formatear, para que la hora mostrada no dependa de en qué huso corra el
proceso de uvicorn/el contenedor.
"""

from datetime import datetime, timezone

from app.web.templating import hora_local


def test_hora_local_resta_cinco_horas_a_un_utc_aware():
    utc = datetime(2026, 8, 14, 15, 30, tzinfo=timezone.utc)
    local = hora_local(utc)
    assert local.hour == 10
    assert local.minute == 30
    assert local.utcoffset().total_seconds() == -5 * 3600


def test_hora_local_cruza_medianoche_hacia_el_dia_anterior():
    # 02:00 UTC es 21:00 del día ANTERIOR en Bogotá -- el caso que hacía
    # flaky comparar `.strftime('%d/%m')` contra el valor UTC crudo.
    utc = datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc)
    local = hora_local(utc)
    assert local.day == 13
    assert local.hour == 21


def test_hora_local_none_pasa_igual():
    assert hora_local(None) is None
