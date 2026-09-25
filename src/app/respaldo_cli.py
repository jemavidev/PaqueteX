# -*- coding: utf-8 -*-
"""
Respaldos de esta instalación (`.scratch/respaldos-y-restauracion`).

Un solo comando para el cron diario, el paso previo al deploy y el botón
"Respaldar ahora": todos respaldan por el mismo camino (`respaldo_service`).

Variables de entorno:
    DATABASE_URL            la base de esta instalación
    PUBLIC_BASE_URL         de ahí sale el dominio (la carpeta del respaldo en S3, y la
                            protección de "mismo sistema" al restaurar)
    RESPALDO_DIR            carpeta de respaldos locales (default `/respaldos`)
    RESPALDO_CHECKOUT_DIR   checkout desplegado, montado en solo lectura (default
                            `/app/checkout`): de ahí sale el commit
    RESPALDO_CODIGO_DIR     carpeta con `alembic.ini` del código instalado (default `/app`)
    RESPALDO_S3_BUCKET, RESPALDO_AWS_ACCESS_KEY_ID, RESPALDO_AWS_SECRET_ACCESS_KEY, AWS_REGION
                            bucket de respaldos y la llave de solo subida de este servidor (ver
                            `infra/respaldos/`). Sin ellas el respaldo queda solo en el disco.
    RESPALDO_CORREO_AVISOS  correos (separados por coma) que reciben los avisos; salen por el SMTP del sistema

Uso (dentro del contenedor, desde `/app/src`; ver `scripts/respaldos/`):
    python -m app.respaldo_cli respaldar --motivo diario
    python -m app.respaldo_cli verificar /respaldos/<carpeta>
    python -m app.respaldo_cli restaurar /respaldos/<carpeta> --confirmacion <dominio> [--otro-destino]
    python -m app.respaldo_cli probar --url-bd-temporal <url de una base VACÍA y aparte>   (domingos)
    python -m app.respaldo_cli resumen                                                    (lunes)
    python -m app.respaldo_cli copiar-fotos [--operacion <id>]    fotos de S3 -> RESPALDO_FOTOS_DIR (incremental)
    (restaurar se corre desde `scripts/respaldos/restaurar.sh`, que detiene y enciende la app)

Código de salida distinto de cero si el respaldo no se completó (o si ya había otro en curso).
"""

import argparse
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain import smtp_email_sender
from app.domain.operacion_respaldo_service import registrar_avance, terminar_operacion
from app.domain.respaldo_fotos_service import S3OrigenFotos, copiar_fotos
from app.domain.email_sender import ConsoleEmailSender
from app.domain.respaldo_service import (
    Avisos,
    Instalacion,
    MotivoRespaldo,
    RespaldoEnCurso,
    RespaldoFallido,
    RestauracionRechazada,
    S3DestinoRespaldos,
    ejecutar_respaldo,
    enviar_resumen_semanal,
    leer_commit,
    probar_restauracion,
    restaurar,
    verificar_respaldo,
)


def _requerida(nombre: str) -> str:
    valor = os.environ.get(nombre)
    if not valor:
        raise SystemExit(f"Falta {nombre} en el entorno.")
    return valor


def _instalacion() -> Instalacion:
    dominio = urlparse(_requerida("PUBLIC_BASE_URL")).hostname
    if not dominio:
        raise SystemExit("PUBLIC_BASE_URL no tiene un dominio válido.")
    checkout = Path(os.environ.get("RESPALDO_CHECKOUT_DIR", "/app/checkout"))
    return Instalacion(
        database_url=_requerida("DATABASE_URL"), dominio=dominio, commit=leer_commit(checkout), checkout=checkout
    )


def _destino_s3():
    bucket = os.environ.get("RESPALDO_S3_BUCKET")
    if not bucket:
        return None
    return S3DestinoRespaldos(
        bucket=bucket,
        region=os.environ.get("AWS_REGION", "us-east-1"),
        access_key_id=_requerida("RESPALDO_AWS_ACCESS_KEY_ID"),
        secret_access_key=_requerida("RESPALDO_AWS_SECRET_ACCESS_KEY"),
    )


def _avisos() -> Avisos:
    destinatarios = [c.strip() for c in os.environ.get("RESPALDO_CORREO_AVISOS", "").split(",") if c.strip()]
    sender = smtp_email_sender.SmtpEmailSender() if smtp_email_sender.configurado() else ConsoleEmailSender()
    return Avisos(sender=sender, destinatarios=destinatarios)


def _terminar(operacion_id, ok: bool, detalle: str) -> None:
    """Marca la operación de la pantalla "Respaldos" con su resultado (ticket 09)."""
    if operacion_id is None:
        return
    engine = create_engine(_requerida("DATABASE_URL"))
    try:
        with Session(engine) as session:
            terminar_operacion(session, operacion_id, ok, detalle)
            session.commit()
    finally:
        engine.dispose()


def _copiar_fotos(operacion_id) -> int:
    origen = S3OrigenFotos(
        bucket=_requerida("AWS_S3_BUCKET_NAME"),
        prefijo=os.environ.get("AWS_S3_PREFIX_FOTOS", "paquetes-recibidos-imagenes/"),  # mismo default que S3FotoStorage
        region=os.environ.get("AWS_REGION", "us-east-1"),
        access_key_id=_requerida("AWS_S3_ACCESS_KEY_ID"),
        secret_access_key=_requerida("AWS_S3_SECRET_ACCESS_KEY"),
    )
    engine = create_engine(_requerida("DATABASE_URL"))

    def avanzar(actual: int, total: int) -> None:
        if operacion_id is None:
            return
        with Session(engine) as session:
            registrar_avance(session, operacion_id, actual, total)
            session.commit()

    try:
        resultado = copiar_fotos(origen, Path(os.environ.get("RESPALDO_FOTOS_DIR", "/fotos-copia")), al_avanzar=avanzar)
    except Exception as exc:
        _terminar(operacion_id, False, f"{type(exc).__name__}: {exc}")
        print(f"Copia de fotos FALLIDA: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    mensaje = f"{resultado.copiadas} fotos nuevas copiadas, {resultado.ya_estaban} ya estaban."
    _terminar(operacion_id, True, mensaje)
    print(mensaje)
    return 0


def _resumen(carpeta: Path) -> str:
    m = verificar_respaldo(carpeta)
    conteos = ", ".join(f"{n} {t}" for t, n in m.conteos.items())
    return (
        f"Respaldo {carpeta.name}: {m.dominio} ({m.conjunto}), {m.fecha_hora_colombia} hora Colombia, "
        f"motivo {m.motivo.value}, commit {m.commit[:12]}, versión {m.version_bd}. Huellas OK. {conteos}."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Respaldos de PaqueteX")
    sub = parser.add_subparsers(dest="accion", required=True)
    respaldar = sub.add_parser("respaldar", help="Saca un respaldo ahora")
    respaldar.add_argument("--motivo", choices=[m.value for m in MotivoRespaldo], default=MotivoRespaldo.DIARIO.value)
    respaldar.add_argument("--operacion", type=uuid.UUID, help="Operación de la pantalla Respaldos a marcar al terminar")
    verificar = sub.add_parser("verificar", help="Comprueba las huellas de un respaldo y dice qué contiene")
    verificar.add_argument("carpeta", type=Path)
    rest = sub.add_parser("restaurar", help="Restaura un respaldo (usar scripts/respaldos/restaurar.sh)")
    rest.add_argument("carpeta", type=Path)
    rest.add_argument("--confirmacion", required=True, help="El dominio de esta instalación, escrito a mano")
    rest.add_argument("--otro-destino", action="store_true", help="Restaurar un respaldo de otro dominio a propósito")
    probar = sub.add_parser("probar", help="Prueba de restauración del último respaldo en una base temporal")
    probar.add_argument("--url-bd-temporal", required=True, help="Base VACÍA y aparte (nunca la de la instalación)")
    sub.add_parser("resumen", help="Envía el resumen semanal por correo")
    copiar = sub.add_parser("copiar-fotos", help="Copia las fotos de S3 al servidor (solo las que falten)")
    copiar.add_argument("--operacion", type=uuid.UUID, help="Operación de la pantalla Respaldos a marcar al terminar")
    args = parser.parse_args()

    carpeta = Path(os.environ.get("RESPALDO_DIR", "/respaldos"))
    try:
        if args.accion == "verificar":
            print(_resumen(args.carpeta))
            return 0
        if args.accion == "probar":
            if args.url_bd_temporal == os.environ.get("DATABASE_URL"):
                raise SystemExit("La base temporal no puede ser la de la instalación.")
            resultado = probar_restauracion(carpeta, args.url_bd_temporal, _avisos())
            print(("Prueba OK: " if resultado.ok else "Prueba FALLIDA: ") + resultado.detalle)
            return 0 if resultado.ok else 1
        if args.accion == "copiar-fotos":
            return _copiar_fotos(args.operacion)
        if args.accion == "resumen":
            dominio = urlparse(_requerida("PUBLIC_BASE_URL")).hostname
            print(enviar_resumen_semanal(carpeta, dominio, _avisos()))
            return 0
        if args.accion == "restaurar":
            restaurar(
                args.carpeta,
                _instalacion(),
                confirmacion=args.confirmacion,
                carpeta_respaldos=carpeta,
                codigo=Path(os.environ.get("RESPALDO_CODIGO_DIR", "/app")),
                permitir_otro_destino=args.otro_destino,
            )
            print(f"Restaurado: {args.carpeta.name}")
            return 0
        destino = _destino_s3()
        try:
            respaldo = ejecutar_respaldo(_instalacion(), carpeta, MotivoRespaldo(args.motivo), destino, _avisos())
        except (RespaldoFallido, RespaldoEnCurso) as exc:
            _terminar(args.operacion, False, str(exc))
            raise
        mensaje = f"Respaldo listo: {respaldo.carpeta.name}" + ("" if destino else " (sin S3: solo en el disco)")
        _terminar(args.operacion, True, mensaje)
        print(mensaje)
        return 0
    except RestauracionRechazada as exc:
        print(f"NO se restauró: {exc}", file=sys.stderr)
        return 2
    except RespaldoEnCurso as exc:
        print(f"No se hizo: {exc}", file=sys.stderr)
        return 3
    except RespaldoFallido as exc:
        print(f"Respaldo FALLIDO en el paso «{exc.paso}»: {exc.detalle}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
