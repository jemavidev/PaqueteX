# -*- coding: utf-8 -*-
"""
Normalización canónica del Teléfono — la llave universal de la Persona (ADR-0003).

Regla (Colombia celular por defecto, resto del mundo vía "+" explícito):
  1. Se descartan todos los caracteres que no sean dígitos (espacios, guiones,
     paréntesis, puntos), conservando si el original traía un `+` inicial.
  2. **Sin `+`**: se asume celular colombiano — 10 dígitos empezando por `3`,
     o el equivalente con indicativo país explícito (12 dígitos, `57` + un `3`).
     Cualquier otro caso sin `+` es inválido (no hay forma de saber el país).
  3. **Con `+`**: cualquier cantidad de dígitos entre 10 y 15 (rango real de
     E.164) se acepta tal cual, sin validar de qué país es — se guarda
     `"+" + dígitos`.

Así, todos estos formatos resuelven al MISMO valor `+573001234567`:
  "3001234567", "300 123 4567", "300-123-4567", "(300) 123-4567",
  "+57 300 123 4567", "+573001234567", "57 300 123 4567".

Un número internacional como "+13002596319" se acepta tal cual (no se puede
corregir/quitar indicativos de países que no sean Colombia sin saber cuáles
son) — queda `"+13002596319"`, sin más validación.

La normalización se aplica ANTES de persistir; la unicidad y el emparejamiento
operan sobre esta forma canónica.
"""

import re

INDICATIVO_COLOMBIA = "57"
_MIN_DIGITOS_E164 = 10
_MAX_DIGITOS_E164 = 15


def normalizar_telefono(raw: str) -> str:
    """Devuelve la forma canónica del teléfono.

    Args:
        raw: el teléfono en cualquier formato de entrada.

    Returns:
        El teléfono canónico, p.ej. ``"+573001234567"`` (Colombia) o
        ``"+13002596319"`` (internacional, tal cual).

    Raises:
        ValueError: si ``raw`` es ``None``, no contiene ningún dígito, o no se
            puede determinar el país (sin `+` y no calza con un celular
            colombiano; o con `+` pero fuera del rango de dígitos válido).
    """
    if raw is None:
        raise ValueError("El teléfono es obligatorio (la llave universal de la Persona).")

    raw_str = str(raw).strip()
    tenia_mas = raw_str.startswith("+")
    digits = re.sub(r"\D", "", raw_str)
    if not digits:
        raise ValueError(f"El teléfono no contiene dígitos: {raw!r}")

    if not tenia_mas:
        if len(digits) == 10 and digits[0] == "3":
            return "+" + INDICATIVO_COLOMBIA + digits
        if (
            len(digits) == 12
            and digits.startswith(INDICATIVO_COLOMBIA)
            and digits[2] == "3"
        ):
            return "+" + digits
        raise ValueError(
            f"Teléfono inválido: {raw!r}. Sin '+', debe ser un celular "
            "colombiano (10 dígitos empezando por 3)."
        )

    if _MIN_DIGITOS_E164 <= len(digits) <= _MAX_DIGITOS_E164:
        return "+" + digits
    raise ValueError(
        f"Teléfono inválido: {raw!r}. Con '+', debe tener entre "
        f"{_MIN_DIGITOS_E164} y {_MAX_DIGITOS_E164} dígitos."
    )
