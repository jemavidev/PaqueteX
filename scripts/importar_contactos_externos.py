#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Importa y fusiona docs/contacts.csv (export de Google Contacts) + un export
de la tabla `customers` de producción v1.0 en un solo conjunto de
`ContactoExterno` (módulo "Consolidación de contactos externos",
`.scratch/contactos-externos`).

El export de producción es un paso MANUAL, documentado acá, no algo que
este script haga por su cuenta (fuera de alcance, ver `.scratch/contactos-
externos/spec.md`):

    ssh paquetex
    cd ~/paqueteria/CODE
    DBURL=$(grep '^DATABASE_URL=' .env | cut -d= -f2- | tr -d '"')
    psql "$DBURL" -c "\\copy (SELECT phone, first_name, last_name FROM customers) \
        TO '/tmp/customers_export.csv' WITH CSV HEADER"
    # luego traé /tmp/customers_export.csv a esta máquina (scp/rsync).

Uso:
    DATABASE_URL=postgresql://... .venv/bin/python scripts/importar_contactos_externos.py \
        --contacts docs/contacts.csv --produccion /ruta/a/customers_export.csv
"""

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.domain.contacto_externo_service import (  # noqa: E402
    FUENTE_GOOGLE_CONTACTS,
    FUENTE_PRODUCCION_V1,
    FilaFuenteContacto,
    importar_contactos_externos,
)


def _filas_desde_google_contacts(ruta: str) -> list[FilaFuenteContacto]:
    filas = []
    with open(ruta, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            nombre = f"{row.get('First Name', '')} {row.get('Last Name', '')}".strip()
            telefonos = tuple(
                v.strip()
                for v in (row.get("Phone 1 - Value", ""), row.get("Phone 2 - Value", ""))
                if v.strip()
            )
            filas.append(
                FilaFuenteContacto(nombre=nombre, telefonos=telefonos, fuente=FUENTE_GOOGLE_CONTACTS)
            )
    return filas


def _filas_desde_produccion(ruta: str) -> list[FilaFuenteContacto]:
    filas = []
    with open(ruta, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            nombre = f"{row.get('first_name', '')} {row.get('last_name', '')}".strip()
            telefono = (row.get("phone") or "").strip()
            filas.append(
                FilaFuenteContacto(
                    nombre=nombre,
                    telefonos=(telefono,) if telefono else (),
                    fuente=FUENTE_PRODUCCION_V1,
                )
            )
    return filas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contacts", required=True, help="Ruta a docs/contacts.csv")
    parser.add_argument(
        "--produccion", required=True, help="Ruta al export de customers de producción"
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("Falta DATABASE_URL en el entorno.")

    filas = _filas_desde_google_contacts(args.contacts) + _filas_desde_produccion(args.produccion)

    engine = create_engine(database_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        resumen = importar_contactos_externos(session, filas)
        session.commit()
    finally:
        session.close()
        engine.dispose()

    print(f"Creados: {resumen.creados}")
    print(f"Enriquecidos: {resumen.enriquecidos}")
    if resumen.conflictos:
        print(f"Conflictos ({len(resumen.conflictos)}) -- revisión manual:")
        for c in resumen.conflictos:
            print(f"  - {c}")


if __name__ == "__main__":
    main()
