# -*- coding: utf-8 -*-
"""
Interruptor del periodo de transición del importador espejo v1 → v2
(`.scratch/importador-v1-espejo`, decisión 2026-09-24).

Mientras `IMPORTADOR_V1_ESPEJO_ACTIVO=1`, la v2 no envía avisos para paquetes
importados de la v1 (`Paquete.origen_v1_id` no nulo): la v2 corre con
`WEB_ENV=production` (SMS reales), así que sin esto una prueba en la v2 sobre un
paquete importado le escribiría a un residente real de la v1. Los paquetes
nativos, el OTP y todo lo demás no cambian. En el corte se quita la variable
(guía de puesta en marcha, §7) y los importados vuelven a notificar.

Hoja sin dependencias del modelo, para que `notificacion_service` la use sin
importar el importador.
"""

import os


def espejo_v1_activo() -> bool:
    return os.environ.get("IMPORTADOR_V1_ESPEJO_ACTIVO") == "1"


def silenciar_avisos_de(paquete) -> bool:
    """¿Este paquete NO debe notificarse ahora? Solo los importados de la v1,
    y solo mientras el espejo está activo."""
    return espejo_v1_activo() and getattr(paquete, "origen_v1_id", None) is not None
