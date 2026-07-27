# -*- coding: utf-8 -*-
"""Instancia Jinja2 compartida por las rutas de la capa web (server-rendered)."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from .security import CUSTOMER_SESSION_KEY, ROLE_SESSION_KEY, SESSION_KEY

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# base.html necesita saber si hay sesión de cliente/staff (y el rol de staff)
# para decidir qué nav mostrar (ver DEC-09) — expuestas como globals para no
# duplicar el string de la clave fuera de `security.py`, única fuente de
# verdad de esos nombres.
templates.env.globals["SESSION_KEY"] = SESSION_KEY
templates.env.globals["CUSTOMER_SESSION_KEY"] = CUSTOMER_SESSION_KEY
templates.env.globals["ROLE_SESSION_KEY"] = ROLE_SESSION_KEY
