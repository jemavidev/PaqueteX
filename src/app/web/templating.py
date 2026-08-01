# -*- coding: utf-8 -*-
"""Instancia Jinja2 compartida por las rutas de la capa web (server-rendered)."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from .config import whatsapp_soporte_numero
from .icons import ICONOS_NAV
from .security import (
    CUSTOMER_NOMBRE_SESSION_KEY,
    CUSTOMER_SESSION_KEY,
    NOMBRE_SESSION_KEY,
    ROLE_SESSION_KEY,
    SESSION_KEY,
)

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# base.html necesita saber si hay sesión de cliente/staff (y el rol/nombre de
# staff) para decidir qué nav mostrar (ver DEC-09) — expuestas como globals
# para no duplicar el string de la clave fuera de `security.py`, única fuente
# de verdad de esos nombres.
templates.env.globals["SESSION_KEY"] = SESSION_KEY
templates.env.globals["CUSTOMER_SESSION_KEY"] = CUSTOMER_SESSION_KEY
templates.env.globals["ROLE_SESSION_KEY"] = ROLE_SESSION_KEY
templates.env.globals["NOMBRE_SESSION_KEY"] = NOMBRE_SESSION_KEY
templates.env.globals["CUSTOMER_NOMBRE_SESSION_KEY"] = CUSTOMER_NOMBRE_SESSION_KEY
# Se expone la FUNCIÓN (no el valor) para que se lea la variable de entorno en
# cada request, no una sola vez al importar el módulo (Grupo 10, Ronda 2).
templates.env.globals["whatsapp_soporte_numero"] = whatsapp_soporte_numero
# Global (no una variable local de base.html): los macros de componentes se
# importan con `{% from ... import %}` y no heredan el contexto de quien los
# llama -- `_inputs.html`/`_botones.html` necesitan poder usar un ícono por
# nombre igual que `base.html` (ver icons.py).
templates.env.globals["iconos_nav"] = ICONOS_NAV
