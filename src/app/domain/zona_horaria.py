# -*- coding: utf-8 -*-
"""
Zona horaria de la aplicación — Bogotá/Lima/Quito, UTC-5 FIJO, sin horario de
verano nunca (a diferencia de EE.UU./Europa, esta franja no lo observa) — un
offset fijo alcanza, sin depender de tzdata/IANA (`zoneinfo.ZoneInfo` puede
fallar en una imagen Docker mínima sin el paquete `tzdata` instalado) ni de la
variable de entorno `TZ` del contenedor/servidor (que hoy ningún código de esta
app lee). La BD sigue guardando UTC siempre (`_utcnow()` en cada modelo de
dominio) — esto es puramente de zona horaria de negocio/presentación.

Vivía como `ZONA_HORARIA_APP` en `app/web/templating.py` (uso original: el
filtro Jinja `hora_local`, solo para MOSTRAR fechas). Se relocó al dominio
(issue estadisticas-cobro-dashboard, ticket 01) porque el tablero de
estadísticas necesita esta misma zona para CALCULAR — límites de "Hoy",
"Esta semana", "Este mes", ritmo por día y hora pico — y el dominio no debe
importar de la capa web. `templating.py` reexporta este símbolo para no rendir
un import roto en el resto de la app.
"""

from datetime import timedelta
from datetime import timezone as _timezone

ZONA_HORARIA_APP = _timezone(timedelta(hours=-5), name="America/Bogota")
