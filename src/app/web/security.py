# -*- coding: utf-8 -*-
"""
Dependencias de autenticación de la capa web (rebuild PaqueteXv.2).

`current_staff` lee la sesión (cookie firmada), carga el `Usuario` y lo entrega:
es el **actor** de la máquina de estados y la **puerta** de las rutas con
privilegios. `require_admin` añade la exigencia de rol ADMIN. El id del usuario
sale SIEMPRE de la sesión verificada, nunca de un parámetro del cliente.
"""

import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.domain.persona import Persona
from app.domain.usuario import RolUsuario, Usuario

from .db import get_db

SESSION_KEY = "usuario_id"
# Clave INDEPENDIENTE de SESSION_KEY: staff y cliente son sesiones separadas que
# coexisten en el mismo navegador sin pisarse (CONTEXT.md: "Usuario = staff;
# Persona/Cliente = residente").
CUSTOMER_SESSION_KEY = "persona_id"


def current_staff(request: Request, db: Session = Depends(get_db)) -> Usuario:
    """El `Usuario` de la sesión actual. Sin sesión válida → 401.

    Un 401 lo convierte el app en un redirect a `/ingresar` (ver app factory).
    """
    raw = request.session.get(SESSION_KEY)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="No autenticado")
    try:
        usuario_id = uuid.UUID(str(raw))
    except (ValueError, TypeError):
        request.session.pop(SESSION_KEY, None)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida")

    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        request.session.pop(SESSION_KEY, None)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida")
    return usuario


def require_admin(usuario: Usuario = Depends(current_staff)) -> Usuario:
    """Como `current_staff`, pero exige rol ADMIN. Si no → 403."""
    if usuario.rol != RolUsuario.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Requiere rol ADMIN")
    return usuario


def current_customer(request: Request, db: Session = Depends(get_db)) -> Persona:
    """La `Persona` de la sesión de CLIENTE actual (independiente de `current_staff`).

    Sin sesión válida → 401. El id sale SIEMPRE de la sesión verificada, nunca de
    un parámetro del cliente.
    """
    raw = request.session.get(CUSTOMER_SESSION_KEY)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="No autenticado")
    try:
        persona_id = uuid.UUID(str(raw))
    except (ValueError, TypeError):
        request.session.pop(CUSTOMER_SESSION_KEY, None)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida")

    persona = db.get(Persona, persona_id)
    if persona is None:
        request.session.pop(CUSTOMER_SESSION_KEY, None)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida")
    return persona
