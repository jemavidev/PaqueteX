# -*- coding: utf-8 -*-
"""
Normalización canónica del Teléfono — la llave universal de la Persona (ADR-0003).

Regla (Colombia por defecto):
  1. Se descartan todos los caracteres que no sean dígitos (espacios, guiones,
     paréntesis, puntos y un eventual `+` inicial).
  2. Si quedan 12 dígitos y empiezan por el indicativo país `57`, se retira ese
     indicativo (queda el número nacional de 10 dígitos).
  3. La forma canónica es siempre `+57` seguido del número nacional.

Así, todos estos formatos resuelven al MISMO valor `+573001234567`:
  "3001234567", "300 123 4567", "300-123-4567", "(300) 123-4567",
  "+57 300 123 4567", "+573001234567", "57 300 123 4567".

La normalización se aplica ANTES de persistir; la unicidad y el emparejamiento
operan sobre esta forma canónica.
"""

import re

INDICATIVO_COLOMBIA = "57"


def normalizar_telefono(raw: str) -> str:
    """Devuelve la forma canónica del teléfono.

    Args:
        raw: el teléfono en cualquier formato de entrada.

    Returns:
        El teléfono canónico, p.ej. ``"+573001234567"``.

    Raises:
        ValueError: si ``raw`` es ``None`` o no contiene ningún dígito.
    """
    if raw is None:
        raise ValueError("El teléfono es obligatorio (la llave universal de la Persona).")

    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        raise ValueError(f"El teléfono no contiene dígitos: {raw!r}")

    # Retirar el indicativo país de Colombia cuando viene explícito (57 + 10).
    if len(digits) == 12 and digits.startswith(INDICATIVO_COLOMBIA):
        digits = digits[len(INDICATIVO_COLOMBIA):]

    return "+" + INDICATIVO_COLOMBIA + digits
