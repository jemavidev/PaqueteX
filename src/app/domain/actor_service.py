# -*- coding: utf-8 -*-
"""
Resolución de nombre de actor para presentación (Grupo 11, Ronda 2 de
`ajustes-post-referencia-funcional`).

Las FK de actor de `Paquete` (`received_by_usuario_id`, etc.) ya existen desde
el diseño original — esto solo resuelve un id a un nombre para mostrarlo, sin
relationship ORM (mismo estilo explícito que el resto del dominio, ver
`packages.py::_nombre_no_coincide`).
"""

from sqlalchemy.orm import Session

from .usuario import Usuario


def nombre_usuario(session: Session, usuario_id) -> str | None:
    """Nombre del `Usuario` con `usuario_id`, o `None` si no hay actor
    (`usuario_id` es `None`) o el `Usuario` no existe."""
    if usuario_id is None:
        return None
    usuario = session.get(Usuario, usuario_id)
    return usuario.nombre if usuario else None
