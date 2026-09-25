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

Uso (dentro del contenedor, desde `/app/src`; ver `scripts/respaldos/`):
    python -m app.respaldo_cli respaldar --motivo diario

Código de salida distinto de cero si el respaldo no se completó (o si ya había otro en curso).
"""

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from app.domain.respaldo_service import (
    MotivoRespaldo,
    OrigenRespaldo,
    RespaldoEnCurso,
    RespaldoFallido,
    crear_respaldo,
    leer_commit,
)


def _requerida(nombre: str) -> str:
    valor = os.environ.get(nombre)
    if not valor:
        raise SystemExit(f"Falta {nombre} en el entorno.")
    return valor


def _origen() -> OrigenRespaldo:
    dominio = urlparse(_requerida("PUBLIC_BASE_URL")).hostname
    if not dominio:
        raise SystemExit("PUBLIC_BASE_URL no tiene un dominio válido.")
    checkout = Path(os.environ.get("RESPALDO_CHECKOUT_DIR", "/app/checkout"))
    return OrigenRespaldo(database_url=_requerida("DATABASE_URL"), dominio=dominio, commit=leer_commit(checkout))


def main() -> int:
    parser = argparse.ArgumentParser(description="Respaldos de PaqueteX")
    sub = parser.add_subparsers(dest="accion", required=True)
    respaldar = sub.add_parser("respaldar", help="Saca un respaldo ahora")
    respaldar.add_argument("--motivo", choices=[m.value for m in MotivoRespaldo], default=MotivoRespaldo.DIARIO.value)
    args = parser.parse_args()

    carpeta = Path(os.environ.get("RESPALDO_DIR", "/respaldos"))
    try:
        respaldo = crear_respaldo(_origen(), carpeta, MotivoRespaldo(args.motivo))
    except RespaldoEnCurso as exc:
        print(f"No se respaldó: {exc}", file=sys.stderr)
        return 3
    except RespaldoFallido as exc:
        print(f"Respaldo FALLIDO en el paso «{exc.paso}»: {exc.detalle}", file=sys.stderr)
        return 1
    print(f"Respaldo listo: {respaldo.carpeta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
