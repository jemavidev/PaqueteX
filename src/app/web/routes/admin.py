# -*- coding: utf-8 -*-
"""
Ruta `/administracion/personal` — alta + gestión de cuentas de staff.

Protegida por `require_admin`. El actor de cada acción sale SIEMPRE de la
sesión (`require_admin`), nunca de un campo del formulario. Grupo 18 (Ronda
2) agregó la gestión de cuentas existentes (editar, resetear contraseña,
activar/desactivar) sobre `staff_service`, ya probado a nivel de dominio —
esta rebanada es solo el cableado HTTP.
"""

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.domain.configuracion_conjunto_service import (
    obtener_nombre_conjunto,
    renombrar_conjunto,
)
from app.domain.notificacion_service import (
    ORIGEN_ANUNCIO_CLIENTE,
    ORIGEN_ANUNCIO_STAFF,
    guardar_plantilla,
    obtener_texto_actual,
)
from app.domain.paquete import EstadoPaquete, MotivoCancelacion
from app.domain.staff_service import (
    create_staff,
    editar_staff,
    listar_staff,
    resetear_password,
    set_activo_staff,
)
from app.domain.usuario import RolUsuario, Usuario

from ..db import get_db
from ..security import require_admin
from ..templating import templates

router = APIRouter()

_EVENTOS_SIN_MOTIVO = (EstadoPaquete.RECIBIDO, EstadoPaquete.ENTREGADO)


def _get_usuario_o_404(db: Session, usuario_id: str) -> Usuario:
    try:
        uid = uuid.UUID(usuario_id)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada")
    usuario = db.get(Usuario, uid)
    if usuario is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada")
    return usuario


@router.get("/administracion/personal", response_class=HTMLResponse)
def admin_staff_form(
    request: Request, db: Session = Depends(get_db), admin: Usuario = Depends(require_admin)
):
    return templates.TemplateResponse(
        "admin/staff.html",
        {
            "request": request,
            "admin": admin,
            "roles": list(RolUsuario),
            "staff_list": listar_staff(db),
        },
    )


@router.post("/administracion/personal", response_class=HTMLResponse)
def admin_staff_submit(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    email: str = Form(None),
    nombre: str = Form(None),
    password: str = Form(None),
    rol: str = Form(None),
):
    def _error(mensaje: str, campos: list[str] = None):
        return templates.TemplateResponse(
            "admin/staff.html",
            {
                "request": request,
                "admin": admin,
                "roles": list(RolUsuario),
                "staff_list": listar_staff(db),
                "error": mensaje,
                "email": email or "",
                "nombre": nombre or "",
                "error_email": mensaje if "email" in (campos or []) else None,
                "error_nombre": mensaje if "nombre" in (campos or []) else None,
                "error_password": mensaje if "password" in (campos or []) else None,
            },
            status_code=400,
        )

    if not (email or "").strip() or not (nombre or "").strip() or not (password or ""):
        campos_vacios = [
            c for c, v in [("email", email), ("nombre", nombre), ("password", password)]
            if not (v or "").strip()
        ]
        return _error("Email, nombre y contraseña son obligatorios.", campos=campos_vacios)

    try:
        rol_enum = RolUsuario(rol)
    except ValueError:
        # Sin campo que marcar: `rol` es un grupo de chips (radio), no un
        # `input_texto` -- ese macro no tiene estado de error propio, y
        # agregarlo solo para este caso (prácticamente inalcanzable sin
        # manipular el HTML a mano) no vale la pena. Se queda en el toast.
        return _error("Selecciona un rol válido.")

    try:
        creado = create_staff(db, admin, email, nombre, password, rol_enum)
    except (PermissionError, ValueError) as exc:
        mensaje = str(exc)
        # Clasificación por prefijo del mensaje (mismo criterio que
        # password_reset.py): create_staff/staff_service solo produce estos
        # 3 prefijos posibles.
        if mensaje.startswith("El email") or mensaje.startswith("Ya existe un usuario"):
            campo = "email"
        elif mensaje.startswith("La contraseña"):
            campo = "password"
        elif mensaje.startswith("El nombre"):
            campo = "nombre"
        else:
            campo = None
        return _error(mensaje, campos=[campo] if campo else [])

    return templates.TemplateResponse(
        "admin/staff.html",
        {
            "request": request,
            "admin": admin,
            "roles": list(RolUsuario),
            "staff_list": listar_staff(db),
            "creado": creado,
        },
    )


@router.post("/administracion/personal/{usuario_id}/editar", response_class=HTMLResponse)
def admin_staff_editar(
    usuario_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    nombre: str = Form(None),
    rol: str = Form(None),
):
    usuario = _get_usuario_o_404(db, usuario_id)

    def _error(mensaje: str):
        return templates.TemplateResponse(
            "admin/staff.html",
            {
                "request": request,
                "admin": admin,
                "roles": list(RolUsuario),
                "staff_list": listar_staff(db),
                "error": mensaje,
            },
            status_code=400,
        )

    try:
        rol_enum = RolUsuario(rol)
    except ValueError:
        return _error("Selecciona un rol válido.")

    try:
        editar_staff(db, admin, usuario, nombre=nombre, rol=rol_enum)
    except (PermissionError, ValueError) as exc:
        return _error(str(exc))

    return RedirectResponse("/administracion/personal", status_code=status.HTTP_303_SEE_OTHER)


@router.post(
    "/administracion/personal/{usuario_id}/resetear-password", response_class=HTMLResponse
)
def admin_staff_resetear_password(
    usuario_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    password: str = Form(None),
):
    usuario = _get_usuario_o_404(db, usuario_id)
    try:
        resetear_password(db, admin, usuario, password)
    except (PermissionError, ValueError) as exc:
        return templates.TemplateResponse(
            "admin/staff.html",
            {
                "request": request,
                "admin": admin,
                "roles": list(RolUsuario),
                "staff_list": listar_staff(db),
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse("/administracion/personal", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/administracion/personal/{usuario_id}/activar", response_class=HTMLResponse)
def admin_staff_activar(
    usuario_id: str,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
):
    usuario = _get_usuario_o_404(db, usuario_id)
    set_activo_staff(db, admin, usuario, True)
    return RedirectResponse("/administracion/personal", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/administracion/personal/{usuario_id}/desactivar", response_class=HTMLResponse)
def admin_staff_desactivar(
    usuario_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
):
    usuario = _get_usuario_o_404(db, usuario_id)
    try:
        set_activo_staff(db, admin, usuario, False)
    except ValueError as exc:
        return templates.TemplateResponse(
            "admin/staff.html",
            {
                "request": request,
                "admin": admin,
                "roles": list(RolUsuario),
                "staff_list": listar_staff(db),
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse("/administracion/personal", status_code=status.HTTP_303_SEE_OTHER)


def _filas_plantillas(db: Session):
    """Una fila por `ANUNCIADO · Cliente` y `ANUNCIADO · Staff` (Grupo 19,
    Ronda 2), una por cada evento sin motivo (RECIBIDO/ENTREGADO), y una por
    cada `MotivoCancelacion` para `CANCELADO` — con su texto vigente
    (personalizado o el default)."""
    filas = [
        {
            "evento": EstadoPaquete.ANUNCIADO,
            "motivo": origen,
            "texto": obtener_texto_actual(db, EstadoPaquete.ANUNCIADO, origen),
        }
        for origen in (ORIGEN_ANUNCIO_CLIENTE, ORIGEN_ANUNCIO_STAFF)
    ]
    filas += [
        {
            "evento": e,
            "motivo": None,
            "texto": obtener_texto_actual(db, e),
        }
        for e in _EVENTOS_SIN_MOTIVO
    ]
    for m in MotivoCancelacion:
        filas.append(
            {
                "evento": EstadoPaquete.CANCELADO,
                "motivo": m.value,
                "texto": obtener_texto_actual(db, EstadoPaquete.CANCELADO, m.value),
            }
        )
    return filas


@router.get("/administracion/notificaciones", response_class=HTMLResponse)
def admin_notificaciones_form(
    request: Request, db: Session = Depends(get_db), admin: Usuario = Depends(require_admin)
):
    return templates.TemplateResponse(
        "admin/notificaciones.html",
        {"request": request, "admin": admin, "filas": _filas_plantillas(db)},
    )


@router.post("/administracion/notificaciones", response_class=HTMLResponse)
def admin_notificaciones_guardar(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    evento: str = Form(None),
    motivo: str = Form(None),
    texto: str = Form(None),
):
    def _error(mensaje: str, marcar_fila: bool = False):
        return templates.TemplateResponse(
            "admin/notificaciones.html",
            {
                "request": request,
                "admin": admin,
                "filas": _filas_plantillas(db),
                "error": mensaje,
                # Identifica CUÁL de las N filas (cada una su propio <form>)
                # falló, para marcar solo ese textarea -- retroalimentación
                # en vivo 2026-08-02.
                "error_evento": evento if marcar_fila else None,
                "error_motivo": (motivo or None) if marcar_fila else None,
            },
            status_code=400,
        )

    try:
        evento_enum = EstadoPaquete(evento)
    except ValueError:
        # Sin fila que marcar: `evento` viene de un input hidden -- si esto
        # falla es manipulación directa del HTML, no un error de usuario
        # real: el toast alcanza.
        return _error("Evento inválido.")

    if not (texto or "").strip():
        return _error("El texto no puede quedar vacío.", marcar_fila=True)

    guardar_plantilla(db, evento_enum, motivo or None, texto)

    return templates.TemplateResponse(
        "admin/notificaciones.html",
        {
            "request": request,
            "admin": admin,
            "filas": _filas_plantillas(db),
            "guardado": True,
        },
    )


@router.get("/administracion/conjunto", response_class=HTMLResponse)
def admin_conjunto_form(
    request: Request, db: Session = Depends(get_db), admin: Usuario = Depends(require_admin)
):
    return templates.TemplateResponse(
        "admin/conjunto.html",
        {"request": request, "admin": admin, "nombre": obtener_nombre_conjunto(db)},
    )


@router.post("/administracion/conjunto", response_class=HTMLResponse)
def admin_conjunto_guardar(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
    nombre: str = Form(""),
):
    try:
        nombre_guardado = renombrar_conjunto(db, nombre, admin)
    except ValueError as exc:
        return templates.TemplateResponse(
            "admin/conjunto.html",
            {
                "request": request,
                "admin": admin,
                "nombre": obtener_nombre_conjunto(db),
                "error": str(exc),
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        "admin/conjunto.html",
        {
            "request": request,
            "admin": admin,
            "nombre": nombre_guardado,
            "guardado": True,
        },
    )
