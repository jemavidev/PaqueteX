# -*- coding: utf-8 -*-
"""
Limpieza previa del staging antes de la primera pasada del importador espejo
v1 → v2 (`.scratch/importador-v1-espejo`, ticket 10).

Borra todo lo de residentes y paquetes; conserva configuración, usuarios,
apartamentos y contactos externos (lista exacta en
`app/domain/limpieza_staging_service.py`). Se niega a correr si ya hay datos
importados de la v1.

Antes de correrlo de verdad: sacar un dump de la base (ver la guía de puesta
en marcha).

Uso (dentro del contenedor de la v2, desde `/app/src`; ver
`scripts/importador_v1/importar_v1_cron.sh`):
    python -m app.limpieza_staging_cli --simular
    python -m app.limpieza_staging_cli --confirmar
"""

import argparse
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.limpieza_staging_service import LimpiezaRechazada, limpiar_datos_de_residentes


def main() -> int:
    parser = argparse.ArgumentParser(description="Limpieza previa del staging (importador v1)")
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--simular", action="store_true", help="Solo muestra los conteos")
    grupo.add_argument("--confirmar", action="store_true", help="Borra de verdad")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("Falta DATABASE_URL en el entorno.")

    engine = create_engine(database_url)
    session = sessionmaker(bind=engine)()
    try:
        resumen = limpiar_datos_de_residentes(session, simular=args.simular)
        session.commit()
    except LimpiezaRechazada as exc:
        session.rollback()
        print(f"RECHAZADA: {exc}")
        return 1
    finally:
        session.close()
        engine.dispose()

    print("SIMULACIÓN -- no se borró nada" if resumen.simular else "Limpieza aplicada")
    print("  Se borran:" if resumen.simular else "  Borrado:")
    for tabla, n in resumen.borrados.items():
        print(f"    {tabla}: {n}")
    print("  Se conservan:")
    for tabla, n in resumen.conservados.items():
        print(f"    {tabla}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
