# -*- coding: utf-8 -*-
"""
Servicio de dominio de ConfiguracionConjunto (Seam A).

Único valor global, editable solo por ADMIN (`.scratch/apartamento-catalogo-
confirmacion/spec.md`). Renombrar propaga a las 804 filas de `Apartamento`
que ya comparten el nombre anterior, para que ninguna quede desincronizada.
"""

from sqlalchemy.orm import Session

from .apartamento import Apartamento
from .configuracion_conjunto import ConfiguracionConjunto
from .texto import normalizar_nombre
from .usuario import RolUsuario, Usuario

# Misma forma canónica que `Apartamento.conjunto` (`normalizar_nombre`) -- así
# el nombre vigente siempre compara/sincroniza igual contra la terna del
# Apartamento, sin importar el casing con que un ADMIN lo haya escrito.
NOMBRE_CONJUNTO_POR_DEFECTO = normalizar_nombre("El Club")


def _fila_vigente(session: Session) -> ConfiguracionConjunto | None:
    return session.query(ConfiguracionConjunto).first()


def obtener_nombre_conjunto(session: Session) -> str:
    """El nombre vigente del Conjunto -- personalizado si algún ADMIN ya lo
    renombró, si no el default."""
    fila = _fila_vigente(session)
    return fila.nombre if fila is not None else NOMBRE_CONJUNTO_POR_DEFECTO


def renombrar_conjunto(session: Session, nuevo_nombre: str, actor: Usuario) -> str:
    """Fija el nombre vigente del Conjunto y sincroniza `Apartamento.conjunto`
    para las filas que ya tenían el nombre anterior.

    Raises:
        PermissionError: si `actor` no es un ADMIN.
        ValueError: si `nuevo_nombre` queda vacío tras `strip()`.
    """
    if actor is None or actor.rol != RolUsuario.ADMIN:
        raise PermissionError("Solo un ADMIN puede renombrar el Conjunto.")

    nombre_limpio = normalizar_nombre(nuevo_nombre) or ""
    if not nombre_limpio:
        raise ValueError("El nombre del Conjunto no puede quedar vacío.")

    nombre_anterior = obtener_nombre_conjunto(session)

    fila = _fila_vigente(session)
    if fila is None:
        fila = ConfiguracionConjunto(nombre=nombre_limpio)
        session.add(fila)
    else:
        fila.nombre = nombre_limpio

    if nombre_anterior != nombre_limpio:
        session.query(Apartamento).filter(
            Apartamento.conjunto == nombre_anterior
        ).update({"conjunto": nombre_limpio}, synchronize_session=False)

    session.flush()
    return nombre_limpio
