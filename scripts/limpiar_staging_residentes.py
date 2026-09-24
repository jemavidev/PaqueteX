#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Envoltorio para correr `app.limpieza_staging_cli` desde un checkout (desarrollo local).
En el servidor se corre como módulo dentro del contenedor (la imagen no
incluye `scripts/`): `python -m app.limpieza_staging_cli` desde `/app/src`."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from app.limpieza_staging_cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
