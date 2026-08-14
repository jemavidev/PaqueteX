# -*- coding: utf-8 -*-
"""Instancia Jinja2 compartida por las rutas de la capa web (server-rendered)."""

from datetime import datetime, timedelta
from datetime import timezone as _timezone
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

# Bogotá/Lima/Quito -- UTC-5 FIJO, sin horario de verano nunca (a diferencia
# de EE.UU./Europa, esta franja no lo observa) -- un offset fijo alcanza, sin
# depender de tzdata/IANA (`zoneinfo.ZoneInfo` puede fallar en una imagen
# Docker mínima sin el paquete `tzdata` instalado) ni de la variable de
# entorno `TZ` del contenedor/servidor (que hoy NINGÚN código de esta app lee
# -- `.env.staging.example` la declara pero nadie la consume, conversación
# 2026-08-14). La BD sigue guardando UTC siempre (`_utcnow()` en cada modelo
# de dominio) -- esto es puramente de presentación.
ZONA_HORARIA_APP = _timezone(timedelta(hours=-5), name="America/Bogota")

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


def hora_local(dt: datetime | None) -> datetime | None:
    """Convierte un datetime UTC-aware (todo lo que guarda la BD) a
    `ZONA_HORARIA_APP` (Bogotá/Lima/Quito) para MOSTRAR -- usar SIEMPRE antes
    de `.strftime()`/`.hour` en una plantilla, nunca formatear el valor crudo
    de la BD directo. `None` pasa igual (campos opcionales como
    `delivered_at` no deben romper la plantilla).

    Uso: `{% set local = paquete.received_at|hora_local %}` y de ahí
    `local.strftime(...)`/`local.hour` -- convertir UNA vez y reusar, no
    llamar el filtro de nuevo por cada uso del mismo datetime.
    """
    if dt is None:
        return None
    return dt.astimezone(ZONA_HORARIA_APP)


templates.env.filters["hora_local"] = hora_local
