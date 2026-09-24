# -*- coding: utf-8 -*-
"""
Importador espejo v1 → v2 (`.scratch/importador-v1-espejo`).

Lee la base de producción v1 en SOLO LECTURA y la vuelca al modelo de la v2.
Se corre repetidamente (cron cada 15 min en el servidor v2, o a mano): es
idempotente y la v1 manda sobre todo lo importado. Ver la guía de puesta en
marcha en `.scratch/importador-v1-espejo/`.

Variables de entorno:
    DATABASE_URL      base de la v2 (destino)
    V1_DATABASE_URL   base de la v1 (origen), con el rol de solo lectura
                      `paquetex_importador`
    Fotos: las de `app/domain/s3_copiador_fotos_v1.py` (lectura del bucket de
    la v1 + el bucket de fotos de la v2). Sin `AWS_S3_BUCKET_NAME`, las fotos
    quedan como error en el reporte y se reintentan en la siguiente pasada.

Uso (dentro del contenedor de la v2, desde `/app/src`; ver
`scripts/importador_v1/importar_v1_cron.sh`):
    python -m app.importador_v1_cli --simular # calcula y reporta, no escribe
    python -m app.importador_v1_cli          # pasada real
    python -m app.importador_v1_cli --final  # el día del corte: falla ante
                                             # cualquier choque o error

Código de salida distinto de cero si la pasada no fue exitosa: una alerta del
tope de borrado (en cualquier modo) o, en `--final`, cualquier choque o error.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.importador_v1_lector import leer_instantanea_v1
from app.domain.s3_copiador_fotos_v1 import S3CopiadorFotosV1
from app.domain.importador_v1_service import (
    ModoSincronizacion,
    ReporteSincronizacion,
    sincronizar_desde_v1,
)


def _requerida(nombre: str) -> str:
    valor = os.environ.get(nombre)
    if not valor:
        raise SystemExit(f"Falta {nombre} en el entorno.")
    return valor


def imprimir_reporte(reporte: ReporteSincronizacion) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] importador v1 -- modo {reporte.modo.value}")
    for entidad, c in reporte.contadores_por_entidad().items():
        print(
            f"  {entidad}: creados={c.creados} actualizados={c.actualizados} "
            f"adoptados={c.adoptados} sin_cambios={c.sin_cambios} borrados={c.borrados}"
        )
    for titulo, lista in (("alertas", reporte.alertas), ("choques", reporte.choques), ("errores", reporte.errores)):
        print(f"  {titulo}: {len(lista)}")
        for item in lista:
            print(f"    - {item}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Importador espejo v1 → v2")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--simular", action="store_true", help="Calcula y reporta sin escribir nada")
    grupo.add_argument("--final", action="store_true", help="Pasada del corte: falla ante cualquier choque o error")
    args = parser.parse_args()
    if args.simular:
        modo = ModoSincronizacion.SIMULAR
    elif args.final:
        modo = ModoSincronizacion.FINAL
    else:
        modo = ModoSincronizacion.NORMAL

    instantanea = leer_instantanea_v1(_requerida("V1_DATABASE_URL"))
    copiador = S3CopiadorFotosV1() if os.environ.get("AWS_S3_BUCKET_NAME") and not args.simular else None

    engine = create_engine(_requerida("DATABASE_URL"))
    session = sessionmaker(bind=engine)()
    try:
        reporte = sincronizar_desde_v1(session, instantanea, modo=modo, copiador_fotos=copiador)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()

    imprimir_reporte(reporte)
    if not reporte.exitosa:
        print("  RESULTADO: FALLIDA -- no se escribió nada en la base de la v2")
        return 1
    print("  RESULTADO: exitosa")
    return 0


if __name__ == "__main__":
    sys.exit(main())
