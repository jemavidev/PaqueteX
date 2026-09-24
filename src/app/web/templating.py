# -*- coding: utf-8 -*-
"""Instancia Jinja2 compartida por las rutas de la capa web (server-rendered)."""

from datetime import datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

from ..domain.guia import LARGO_MAXIMO_GUIA
from ..domain.paquete import torre_sin_prefijo
from ..domain.zona_horaria import ZONA_HORARIA_APP
from .config import whatsapp_soporte_numero
from .icons import ICONOS_NAV
from .security import (
    CUSTOMER_NOMBRE_SESSION_KEY,
    CUSTOMER_SESSION_KEY,
    NOMBRE_SESSION_KEY,
    ROLE_SESSION_KEY,
    SESSION_KEY,
)

# Reexportado por compatibilidad -- `ZONA_HORARIA_APP` vivía acá; ahora la
# fuente de verdad es `app.domain.zona_horaria` (el tablero de estadísticas de
# cobro la necesita para CALCULAR, no solo para mostrar, y el dominio no debe
# importar de la capa web). Ver ese módulo para el porqué del offset fijo.

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
# Largo máximo de la Guía (`domain/guia.py`): el JS de `_recibir_paquete.html` lo usa para el campo y para la
# cámara, así el límite del navegador no puede desincronizarse del de la columna ni del servidor.
templates.env.globals["LARGO_MAXIMO_GUIA"] = LARGO_MAXIMO_GUIA


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

_MESES_CORTOS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def fecha_amigable(dt: datetime | None) -> str:
    """ "19 sep. 2026 · 3:40 p. m." en hora de Colombia (issue 392, .scratch/pendientes-cliente) -- para listas que
    lee un residente, en vez de "19/09/2026". Sin depender del locale del servidor."""
    local = hora_local(dt)
    if local is None:
        return ""
    hora = local.hour % 12 or 12
    return (
        f"{local.day} {_MESES_CORTOS[local.month - 1]}. {local.year} · "
        f"{hora}:{local.minute:02d} {'a' if local.hour < 12 else 'p'}. m."
    )


templates.env.filters["fecha_amigable"] = fecha_amigable
# `snapshot_torre` ya trae el prefijo "TORRE" del catálogo (ver
# `torre_sin_prefijo` en domain/paquete.py) -- cualquier template que
# anteponga su propio "Torre " literal debe pasar el valor por este filtro,
# o queda "Torre TORRE 10".
templates.env.filters["torre_sin_prefijo"] = torre_sin_prefijo
