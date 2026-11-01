# -*- coding: utf-8 -*-
"""
Posición de almacenamiento (`Paquete.posicion`) -- el compartimento del estante donde se guarda un Paquete al
Recibir (glosario en `CONTEXT.md`, `.scratch/posicion-almacenamiento`).

Un solo estante físico de 7 filas × 2 lados, FIJO en código (sin configuración). Código de dos dígitos
fila + lado: fila 1 (abajo) … 7 (arriba); lado 1 = derecha, 2 = izquierda -- tal cual las etiquetas del estante.
Cada Posición guarda varios paquetes: no hay control de ocupación.

Pedido del cliente 2026-10-31: además del estante, `"00"` = EL PISO (`POSICION_PISO`), para lo que no cabe en el
estante. NO es una fila: siempre se puede elegir, no aparece en Administración → Posiciones y nunca la apaga
`filas_desactivadas` (`posicion_service.py`). En la grilla va debajo de 12|11, ocupando el ancho de las dos columnas.

Mismo patrón que `guia.py`: una hoja sin dependencias del modelo, así la columna (su CHECK), el ciclo de vida
(`receive`), la ruta y la grilla del modal comparten UNA sola lista.
"""

FILAS = 7

# La grilla del modal, fila por fila de ARRIBA hacia ABAJO y cada fila `(izquierda, derecha)` -- espejo del
# estante visto de frente: 72|71 arriba … 12|11 abajo.
GRILLA_ESTANTE = tuple((f"{fila}2", f"{fila}1") for fila in range(FILAS, 0, -1))

# El piso: debajo del estante, fuera de `GRILLA_ESTANTE` (no es una fila).
POSICION_PISO = "00"

POSICIONES_VALIDAS = frozenset(codigo for fila in GRILLA_ESTANTE for codigo in fila) | {POSICION_PISO}

# El CHECK de la columna (modelo y migraciones 0066/0075) sale de la misma lista.
SQL_CHECK_POSICION = "posicion IS NULL OR posicion IN ({})".format(
    ", ".join(f"'{codigo}'" for codigo in sorted(POSICIONES_VALIDAS))
)


class PosicionInvalida(ValueError):
    """El código no es una de las Posiciones del estante (ni el piso). El mensaje es el que se le muestra al Operador."""

    def __init__(self, valor: str):
        super().__init__(f"La posición «{valor}» no existe en el estante.")


def normalizar_posicion(valor: str | None) -> str | None:
    """La Posición validada, o `None` si viene vacía.

    Raises:
        PosicionInvalida: si no es una de `POSICIONES_VALIDAS`.
    """
    posicion = (valor or "").strip() or None
    if posicion is not None and posicion not in POSICIONES_VALIDAS:
        raise PosicionInvalida(posicion)
    return posicion
