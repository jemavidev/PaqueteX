# -*- coding: utf-8 -*-
"""
Guía del transportador (`Paquete.guide_number`) -- normalización y largo máximo.

La Guía es una REFERENCIA opcional, no una llave (glosario en `CONTEXT.md`): no es única (un envío de varias
cajas la comparte), se captura al Recibir y solo se compara para avisar. Mismo patrón que `telefono.py` y
`texto.py`: una hoja sin dependencias del modelo, así el Paquete (la columna), el ciclo de vida (`receive`), el
servicio de conteo y la ruta comparten UNA sola regla en vez de que el límite viva repetido.

`.scratch/captura-guia-lector-camara`, tickets 04 y 08 (y la revisión de código posterior).
"""

from .texto import normalizar_nombre

# Largo máximo de la Guía. `Paquete.guide_number` es `String(LARGO_MAXIMO_GUIA)` (sin migración: la columna ya
# era de 50) y el navegador lo recibe como global de plantilla (`LARGO_MAXIMO_GUIA`, ver `templating.py`): el
# límite del campo, el de la cámara y el de la base de datos no pueden desincronizarse.
LARGO_MAXIMO_GUIA = 50


class GuiaDemasiadoLarga(ValueError):
    """La Guía, ya normalizada como se guarda, supera `LARGO_MAXIMO_GUIA`.

    El mensaje (`str(exc)`) es el que se le muestra al Operador: dice el largo real y el máximo."""

    def __init__(self, largo: int):
        super().__init__(f"La guía tiene {largo} caracteres; el máximo es {LARGO_MAXIMO_GUIA}.")


def normalizar_guia(guide_number: str | None) -> str | None:
    """La Guía en su forma canónica (mayúsculas, espacios colapsados y recortada), validando el largo.

    `None` y cadena vacía pasan intactos (la Guía es opcional). Nunca trunca: una guía cortada sería una guía
    equivocada. El largo se cuenta sobre la forma normalizada, en caracteres (no en bytes ni unidades UTF-16).

    Raises:
        GuiaDemasiadoLarga: si la forma normalizada supera `LARGO_MAXIMO_GUIA`.
    """
    guia = normalizar_nombre(guide_number)
    if guia and len(guia) > LARGO_MAXIMO_GUIA:
        raise GuiaDemasiadoLarga(len(guia))
    return guia
