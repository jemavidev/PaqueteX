# -*- coding: utf-8 -*-
"""
Normalización de texto libre escrito por personas (nombres, guías) — Seam A.

Mismo problema que `telefono.normalizar_telefono()` resuelve para teléfonos:
la misma Persona/Paquete puede llegar por `/anunciar`, `/announce` (staff),
`/mis-datos`, o la administración de residentes, y cada ruta tipeó el nombre
distinto ("Camila", "CAMILA", "camila "). Canonicalizar UNA vez en la frontera
del dominio (antes de persistir) evita que la MISMA persona/paquete termine
con casing distinto según por dónde entró — la búsqueda ya es insensible a
mayúsculas vía `ilike` (`customers_manage._buscar_residentes`), así que el
gap real era solo de escritura, no de lectura.

Mayúsculas como forma canónica (no Title Case) porque es la convención YA
verificada en producción (`customer_name`/`guide_number` en
paquetex.papyrus.com.co se autocapitalizan con
`oninput="this.value = this.value.toUpperCase()"`).
"""

import re


def normalizar_nombre(valor: str | None) -> str | None:
    """Devuelve `valor` en su forma canónica: espacios colapsados/recortados
    + MAYÚSCULAS.

    `None` y cadenas vacías pasan intactos — la validación de "es
    obligatorio" es responsabilidad de quien llama, esta función solo
    normaliza el casing/espaciado de lo que SÍ vino.
    """
    if not valor:
        return valor
    return re.sub(r"\s+", " ", str(valor)).strip().upper()
