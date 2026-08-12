# -*- coding: utf-8 -*-
"""
Clasificación de un valor de contacto tecleado en un solo campo -- Teléfono o
WhatsApp, sin que quien escribe tenga que elegir un tipo de campo a mano
(`.scratch/ocupante-principal-escenarios`, ticket 01).

Única fuente de verdad para esta regla -- antes vivía privada en
`announce_new.py` (`_clasificar`, que además reconoce Torre+Apto, un caso
propio de esa vista que NO se generaliza acá).
"""

_MIN_LARGO_WHATSAPP = 3


def clasificar_contacto(valor: str) -> str:
    """`"telefono"` | `"whatsapp"` | `"ninguno"`.

    - Empieza en `3`, 10 dígitos exactos (celular colombiano,
      `telefono.normalizar_telefono`) -> `"telefono"`.
    - Empieza con una letra, al menos 3 caracteres -> `"whatsapp"`.
    - Cualquier otro caso (vacío, a medio teclear, formato que no calza con
      ninguno de los dos) -> `"ninguno"`.

    Exige el valor COMPLETO, no un prefijo -- un teléfono a medio teclear no
    debe clasificar como inválido mientras la persona todavía está
    escribiendo (ver `announce_new.py` para el hallazgo original de este
    comportamiento en code-review)."""
    valor = (valor or "").strip()
    if not valor:
        return "ninguno"
    primero = valor[0]
    if primero == "3" and valor.isdigit():
        return "telefono" if len(valor) == 10 else "ninguno"
    if primero.isalpha():
        return "whatsapp" if len(valor) >= _MIN_LARGO_WHATSAPP else "ninguno"
    return "ninguno"
