# -*- coding: utf-8 -*-
"""
Ruta `/residentes` — buscar + ver/editar cliente (staff).

Buscar y editar son operativos, abiertos a CUALQUIER rol de staff (a diferencia
de eliminar, gated por `require_admin` en el módulo de la acción destructiva).
Reutiliza `update_datos_personales` sin cambios, operando sobre la Persona de
OTRO (no la propia sesión, a diferencia de `/customer/verify`).
"""

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.domain.apartamento import Apartamento
from app.domain.ocupante_service import listar_ocupantes
from app.domain.persona import Persona
from app.domain.persona_service import (
    anonimizar_persona,
    set_notificaciones_activas,
    update_datos_personales,
)
from app.domain.telefono import normalizar_telefono
from app.domain.usuario import Usuario

from ..db import get_db
from ..security import current_staff, require_admin
from ..templating import templates

router = APIRouter()


def _blank_to_none(valor):
    valor = (valor or "").strip()
    return valor or None


def _apartamento_actual(db: Session, persona: Persona):
    if persona.apartamento_actual_id is None:
        return None
    return db.get(Apartamento, persona.apartamento_actual_id)


def _ocupantes_de(db: Session, apartamento):
    if apartamento is None:
        return []
    ocupantes = listar_ocupantes(db, apartamento)
    for o in ocupantes:
        # Atributo transitorio (no persistido) — Ocupante no tiene relationship
        # ORM a Persona, solo el FK crudo `persona_id`.
        o.telefono = db.get(Persona, o.persona_id).telefono if o.persona_id else None
    return ocupantes


def _get_persona_o_404(db: Session, persona_id: str) -> Persona:
    try:
        pid = uuid.UUID(persona_id)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cliente no encontrado")
    persona = db.get(Persona, pid)
    if persona is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cliente no encontrado")
    return persona


@router.get("/residentes", response_class=HTMLResponse)
def customers_manage_search(
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    q: str = None,
):
    termino = _blank_to_none(q)
    resultados = []
    if termino:
        try:
            telefono = normalizar_telefono(termino)
        except ValueError:
            telefono = None
        if telefono is not None:
            resultados = db.query(Persona).filter(Persona.telefono == telefono).all()
        else:
            resultados = (
                db.query(Persona).filter(Persona.nombre.ilike(f"%{termino}%")).all()
            )
    return templates.TemplateResponse(
        "customers_manage/search.html",
        {"request": request, "staff": staff, "q": termino or "", "resultados": resultados},
    )


@router.get("/residentes/{persona_id}", response_class=HTMLResponse)
def customers_manage_detail(
    persona_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    persona = _get_persona_o_404(db, persona_id)
    return templates.TemplateResponse(
        "customers_manage/detail.html",
        {
            "request": request,
            "staff": staff,
            "persona": persona,
            "apartamento": _apartamento_actual(db, persona),
            "ocupantes": _ocupantes_de(db, _apartamento_actual(db, persona)),
        },
    )


@router.post("/residentes/{persona_id}", response_class=HTMLResponse)
def customers_manage_update(
    persona_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    nombre: str = Form(None),
    email: str = Form(None),
    documento: str = Form(None),
    tipo_documento: str = Form(None),
    segundo_contacto: str = Form(None),
    notificaciones_activas: str = Form(None),
):
    persona = _get_persona_o_404(db, persona_id)

    try:
        update_datos_personales(
            db,
            persona,
            nombre=_blank_to_none(nombre),
            email=_blank_to_none(email),
            documento=_blank_to_none(documento),
            tipo_documento=_blank_to_none(tipo_documento),
            segundo_contacto=_blank_to_none(segundo_contacto),
        )
    except ValueError as exc:
        db.rollback()
        return templates.TemplateResponse(
            "customers_manage/detail.html",
            {
                "request": request,
                "staff": staff,
                "persona": persona,
                "apartamento": _apartamento_actual(db, persona),
            "ocupantes": _ocupantes_de(db, _apartamento_actual(db, persona)),
                "error": str(exc),
            },
            status_code=400,
        )

    # Checkbox: presente (marcado) = True; ausente (desmarcado) = False —
    # distinto del resto de campos, cuya ausencia significa "no tocar".
    set_notificaciones_activas(db, persona, notificaciones_activas is not None)

    return templates.TemplateResponse(
        "customers_manage/detail.html",
        {
            "request": request,
            "staff": staff,
            "persona": persona,
            "apartamento": _apartamento_actual(db, persona),
            "ocupantes": _ocupantes_de(db, _apartamento_actual(db, persona)),
            "guardado": True,
        },
    )


@router.post("/residentes/{persona_id}/eliminar")
def customers_manage_delete(
    persona_id: str,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
):
    """Elimina (anonimiza) un cliente. **Solo ADMIN** — acción destructiva
    (ADR-0005); la ruta se protege server-side, la UI no es la única barrera."""
    persona = _get_persona_o_404(db, persona_id)
    anonimizar_persona(db, persona)
    return RedirectResponse(
        "/residentes?eliminado=1", status_code=status.HTTP_303_SEE_OTHER
    )
