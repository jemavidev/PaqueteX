# -*- coding: utf-8 -*-
"""
Respaldos, ticket 06 (`.scratch/respaldos-y-restauracion`): la prueba de restauración del domingo (restaurar el último
respaldo en una base temporal y aparte, y comparar versión y conteos con el manifiesto) y el resumen del lunes. Seam A
contra un Postgres real; la "base temporal" es una base vacía del Postgres de pruebas (en el servidor la levanta el
script del host en un contenedor desechable).
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import _harness as H
from app.domain.email_sender import ConsoleEmailSender
from app.domain.persona_service import get_or_create_persona
from app.domain.respaldo_service import (
    Avisos,
    Instalacion,
    MotivoRespaldo,
    RespaldoFallido,
    crear_respaldo,
    ejecutar_respaldo,
    enviar_resumen_semanal,
    leer_historial,
    probar_restauracion,
)
from app.domain.staff_service import create_initial_admin

_AHORA = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)  # domingo 04:00 hora Colombia
_CORREOS = ["jveyes@gmail.com", "info@papyrus.com.co"]


@pytest.fixture()
def servidor(postgres_server_url):
    """Crea bases propias de la prueba (vacías o migradas con datos) y las borra al final."""
    server = postgres_server_url.rsplit("/", 1)[0]
    admin = create_engine(server + "/postgres", isolation_level="AUTOCOMMIT")
    creadas = []

    def crear(migrada=False):
        nombre = f"pr_{uuid.uuid4().hex[:10]}"
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{nombre}"'))
        creadas.append(nombre)
        url = server + "/" + nombre
        if migrada:
            H.run_alembic(url, "upgrade", "head")
            engine = create_engine(url)
            with sessionmaker(bind=engine)() as s:
                create_initial_admin(s, "admin@club.com", "Admin", "Contrasena1")
                get_or_create_persona(s, "3001112233", "Ana")
                get_or_create_persona(s, "3004445566", "Luis")
                s.commit()
            engine.dispose()
        return url

    yield crear
    with admin.connect() as conn:
        for nombre in creadas:
            conn.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n AND pid <> pg_backend_pid()"),
                {"n": nombre},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{nombre}"'))
    admin.dispose()


def _respaldo(servidor, carpeta):
    url = servidor(migrada=True)
    instalacion = Instalacion(database_url=url, dominio="test.papyrus.com.co", commit="abc1234")
    return crear_respaldo(instalacion, carpeta, MotivoRespaldo.DIARIO, ahora=_AHORA - timedelta(hours=1))


def _avisos(correo):
    return Avisos(sender=correo, destinatarios=_CORREOS, uso_disco=lambda _c: 0.30)


def test_la_prueba_restaura_el_ultimo_respaldo_en_una_base_aparte_y_cuadra_con_el_manifiesto(servidor, tmp_path):
    _respaldo(servidor, tmp_path)
    correo = ConsoleEmailSender()

    resultado = probar_restauracion(tmp_path, servidor(), _avisos(correo), ahora=_AHORA)

    assert resultado.ok
    assert resultado.conteos["personas"] == 2 and resultado.conteos["usuarios"] == 1
    assert correo.enviados == []
    assert leer_historial(tmp_path)[-1]["motivo"] == "prueba_restauracion"
    assert leer_historial(tmp_path)[-1]["ok"] is True


def test_si_los_conteos_no_cuadran_la_prueba_falla_y_avisa_de_inmediato(servidor, tmp_path):
    respaldo = _respaldo(servidor, tmp_path)
    manifiesto = respaldo.carpeta / "manifiesto.txt"
    manifiesto.write_text(manifiesto.read_text().replace("personas = 2", "personas = 3"))
    correo = ConsoleEmailSender()

    resultado = probar_restauracion(tmp_path, servidor(), _avisos(correo), ahora=_AHORA)

    assert not resultado.ok and "personas" in resultado.detalle
    assert sorted(d for d, *_ in correo.enviados) == sorted(_CORREOS)
    assert "prueba de restauración" in correo.enviados[0][1].lower()


def test_un_respaldo_danado_hace_fallar_la_prueba(servidor, tmp_path):
    respaldo = _respaldo(servidor, tmp_path)
    with open(respaldo.carpeta / "base_datos.dump", "ab") as f:
        f.write(b"x")

    resultado = probar_restauracion(tmp_path, servidor(), _avisos(ConsoleEmailSender()), ahora=_AHORA)

    assert not resultado.ok and "base_datos.dump" in resultado.detalle


class _Destino:
    def subir(self, clave, ruta, tipo):
        pass


class _DestinoCaido:
    def subir(self, clave, ruta, tipo):
        raise ConnectionError("S3 no responde")


def _semana(servidor, carpeta, falla_el_dia=None):
    """Una semana de respaldos diarios (lun 21 a dom 27 de sep, 03:00 Colombia) y la prueba del domingo."""
    url = servidor(migrada=True)
    instalacion = Instalacion(database_url=url, dominio="test.papyrus.com.co", commit="abc1234")
    for dia in range(21, 28):
        destino = _DestinoCaido() if dia == falla_el_dia else _Destino()
        try:
            ejecutar_respaldo(instalacion, carpeta, MotivoRespaldo.DIARIO, destino, _avisos(ConsoleEmailSender()),
                              ahora=datetime(2026, 9, dia, 8, 0, tzinfo=timezone.utc))
        except RespaldoFallido:
            pass
    probar_restauracion(carpeta, servidor(), _avisos(ConsoleEmailSender()), ahora=_AHORA)


_LUNES = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)  # lunes 07:00 hora Colombia


def test_el_resumen_del_lunes_llega_aunque_todo_haya_salido_bien(servidor, tmp_path):
    _semana(servidor, tmp_path)
    correo = ConsoleEmailSender()

    enviar_resumen_semanal(tmp_path, "test.papyrus.com.co", _avisos(correo), ahora=_LUNES)

    assert sorted(d for d, *_ in correo.enviados) == sorted(_CORREOS)
    _, asunto, cuerpo, _ = correo.enviados[0]
    assert "test.papyrus.com.co" in asunto and "OK" in asunto
    assert "7 de 7" in cuerpo
    assert "Prueba de restauración (domingo): OK" in cuerpo and "2 personas" in cuerpo
    assert "30 %" in cuerpo  # espacio en disco


def test_el_resumen_dice_cuantos_fallaron_en_la_semana(servidor, tmp_path):
    _semana(servidor, tmp_path, falla_el_dia=24)
    correo = ConsoleEmailSender()

    enviar_resumen_semanal(tmp_path, "test.papyrus.com.co", _avisos(correo), ahora=_LUNES)

    _, asunto, cuerpo, _ = correo.enviados[0]
    assert "ATENCIÓN" in asunto
    assert "6 de 7" in cuerpo and "2026-09-24" in cuerpo
