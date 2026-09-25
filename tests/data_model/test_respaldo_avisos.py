# -*- coding: utf-8 -*-
"""
Respaldos, ticket 05 (`.scratch/respaldos-y-restauracion`): el recorrido completo de una corrida -- respaldar,
subir a S3, avisar por correo si algo falla, avisar si el disco pasa del 80 % y dejar registro de la corrida. Seam A:
el módulo de respaldos contra un Postgres real, con un destino S3 falso y el `ConsoleEmailSender` existente.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

import _harness as H
from app.domain.email_sender import ConsoleEmailSender
from app.domain.respaldo_service import (
    Avisos,
    Instalacion,
    MotivoRespaldo,
    RespaldoFallido,
    ejecutar_respaldo,
    leer_historial,
)

_AHORA = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
_CORREOS = ["jveyes@gmail.com", "info@papyrus.com.co"]


class DestinoFalso:
    def __init__(self, falla=False):
        self.objetos = {}
        self.falla = falla

    def subir(self, clave, ruta, tipo):
        if self.falla:
            raise ConnectionError("S3 no responde")
        self.objetos[clave] = tipo


@pytest.fixture(scope="module")
def bd(postgres_server_url):
    server = postgres_server_url.rsplit("/", 1)[0]
    nombre = f"av_{uuid.uuid4().hex[:10]}"
    admin = create_engine(server + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{nombre}"'))
    url = server + "/" + nombre
    H.run_alembic(url, "upgrade", "head")
    yield url
    with admin.connect() as conn:
        conn.execute(
            text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n AND pid <> pg_backend_pid()"),
            {"n": nombre},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{nombre}"'))
    admin.dispose()


def _instalacion(url):
    return Instalacion(database_url=url, dominio="test.papyrus.com.co", commit="abc1234")


def _avisos(correo, uso_disco=0.20):
    return Avisos(sender=correo, destinatarios=_CORREOS, uso_disco=lambda _carpeta: uso_disco)


def test_si_la_subida_falla_llega_un_correo_a_cada_destinatario_con_el_paso(bd, tmp_path):
    correo = ConsoleEmailSender()

    with pytest.raises(RespaldoFallido):
        ejecutar_respaldo(
            _instalacion(bd), tmp_path, MotivoRespaldo.DIARIO, DestinoFalso(falla=True), _avisos(correo), ahora=_AHORA
        )

    assert sorted(d for d, *_ in correo.enviados) == sorted(_CORREOS)
    _, asunto, cuerpo, _ = correo.enviados[0]
    assert "test.papyrus.com.co" in asunto and "FALLÓ" in asunto
    assert "subida a S3" in cuerpo and "S3 no responde" in cuerpo


def test_si_la_base_no_responde_el_correo_dice_ese_paso(tmp_path, postgres_server_url):
    correo = ConsoleEmailSender()
    inexistente = postgres_server_url.rsplit("/", 1)[0] + "/no_existe_esta_base"

    with pytest.raises(RespaldoFallido):
        ejecutar_respaldo(_instalacion(inexistente), tmp_path, MotivoRespaldo.DIARIO, DestinoFalso(), _avisos(correo), ahora=_AHORA)

    assert "base de datos" in correo.enviados[0][2]


def test_una_corrida_buena_no_manda_correos(bd, tmp_path):
    correo = ConsoleEmailSender()

    ejecutar_respaldo(_instalacion(bd), tmp_path, MotivoRespaldo.DIARIO, DestinoFalso(), _avisos(correo), ahora=_AHORA)

    assert correo.enviados == []


@pytest.mark.parametrize("uso, avisa", [(0.85, True), (0.80, False), (0.50, False)])
def test_avisa_por_correo_si_el_disco_pasa_del_80_por_ciento(bd, tmp_path, uso, avisa):
    correo = ConsoleEmailSender()

    ejecutar_respaldo(_instalacion(bd), tmp_path, MotivoRespaldo.DIARIO, DestinoFalso(), _avisos(correo, uso), ahora=_AHORA)

    asuntos = [a for _, a, _, _ in correo.enviados]
    assert bool(asuntos) is avisa
    if avisa:
        assert all("disco" in a.lower() for a in asuntos) and "85 %" in correo.enviados[0][2]


def test_cada_corrida_queda_registrada_buena_o_fallida(bd, tmp_path):
    ejecutar_respaldo(_instalacion(bd), tmp_path, MotivoRespaldo.DIARIO, DestinoFalso(), _avisos(ConsoleEmailSender()), ahora=_AHORA)
    with pytest.raises(RespaldoFallido):
        ejecutar_respaldo(
            _instalacion(bd), tmp_path, MotivoRespaldo.A_PEDIDO, DestinoFalso(falla=True), _avisos(ConsoleEmailSender()), ahora=_AHORA
        )

    buena, fallida = leer_historial(tmp_path)
    assert buena["ok"] is True and buena["motivo"] == "diario" and buena["subido_a"] == ["diario"] and buena["tamano"] > 0
    assert fallida["ok"] is False and fallida["motivo"] == "a_pedido" and "S3" in fallida["error"]
