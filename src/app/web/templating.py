# -*- coding: utf-8 -*-
"""Instancia Jinja2 compartida por las rutas de la capa web (server-rendered)."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
