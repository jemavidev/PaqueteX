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
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.domain.apartamento import Apartamento
from app.domain.ocupante import Ocupante
from app.domain.ocupante_service import listar_ocupantes
from app.domain.persona import Persona
from app.domain.persona_service import anonimizar_persona, update_datos_personales
from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.preferencia_notificacion_service import (
    EVENTOS,
    activar_canal_en_todos_los_eventos,
    preferencia_activa,
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


def _sms_activo_en_todos_los_eventos(db: Session, persona: Persona) -> bool:
    """Estado del toggle simplificado de staff (Grupo 13, Ronda 2): SMS
    activo para los 4 eventos a la vez. Si el cliente ya personalizó su
    matriz de forma desigual desde `/mis-datos`, se ve como "desactivado"
    aquí (representación honesta de un control binario para un estado que ya
    no es binario) — el detalle fino solo se edita desde `/mis-datos`."""
    return all(
        preferencia_activa(db, persona.id, CanalNotificacion.SMS, evento)
        for evento in EVENTOS
    )


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


def _buscar_residentes(db: Session, termino: str) -> list[Persona]:
    """Búsqueda extendida (Grupo 17, Ronda 2): teléfono o nombre de la
    Persona principal, torre/apartamento de su unidad, nombre/teléfono de su
    segundo contacto, o nombre de cualquier Ocupante (con o sin teléfono
    propio) de su misma unidad — un match por Ocupante resuelve a la Persona
    **principal** de ese Apartamento (los Ocupantes sin teléfono no tienen
    ficha propia). Resultados únicos, sin duplicar si varios criterios
    coinciden con la misma Persona."""
    encontradas: dict = {}  # id -> Persona, dedup preservando orden de hallazgo

    def _agregar_todas(personas):
        for p in personas:
            encontradas.setdefault(p.id, p)

    try:
        telefono = normalizar_telefono(termino)
    except ValueError:
        telefono = None

    filtros_persona = [
        Persona.nombre.ilike(f"%{termino}%"),
        Persona.segundo_contacto.ilike(f"%{termino}%"),
    ]
    if telefono is not None:
        filtros_persona.append(Persona.telefono == telefono)
    _agregar_todas(db.query(Persona).filter(or_(*filtros_persona)).all())

    apartamentos_match = (
        db.query(Apartamento)
        .filter(
            or_(
                Apartamento.torre.ilike(f"%{termino}%"),
                Apartamento.apartamento.ilike(f"%{termino}%"),
            )
        )
        .all()
    )
    if apartamentos_match:
        apto_ids = [a.id for a in apartamentos_match]
        _agregar_todas(
            db.query(Persona).filter(Persona.apartamento_actual_id.in_(apto_ids)).all()
        )

    ocupantes_match = db.query(Ocupante).filter(Ocupante.nombre.ilike(f"%{termino}%")).all()
    if ocupantes_match:
        apto_ids_de_ocupantes = {o.apartamento_id for o in ocupantes_match}
        principales = (
            db.query(Ocupante)
            .filter(
                Ocupante.apartamento_id.in_(apto_ids_de_ocupantes),
                Ocupante.es_principal.is_(True),
            )
            .all()
        )
        persona_ids = [o.persona_id for o in principales if o.persona_id is not None]
        if persona_ids:
            _agregar_todas(db.query(Persona).filter(Persona.id.in_(persona_ids)).all())

    return sorted(encontradas.values(), key=lambda p: p.nombre or "")


@router.get("/residentes", response_class=HTMLResponse)
def customers_manage_search(
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    q: str = None,
):
    termino = _blank_to_none(q)
    resultados = _buscar_residentes(db, termino) if termino else []
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
            "sms_activo": _sms_activo_en_todos_los_eventos(db, persona),
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
            segundo_contacto=_blank_to_none(segundo_contacto),
        )
    except ValueError as exc:
        db.rollback()
        # Único origen de ValueError acá es el formato del email (ver
        # persona_service.update_datos_personales) -- seguro marcar ese
        # campo siempre.
        mensaje = str(exc)
        return templates.TemplateResponse(
            "customers_manage/detail.html",
            {
                "request": request,
                "staff": staff,
                "persona": persona,
                "apartamento": _apartamento_actual(db, persona),
            "ocupantes": _ocupantes_de(db, _apartamento_actual(db, persona)),
                "error": mensaje,
                "error_email": mensaje,
            },
            status_code=400,
        )

    # Checkbox: presente (marcado) = True; ausente (desmarcado) = False —
    # distinto del resto de campos, cuya ausencia significa "no tocar".
    # Ver docstring de `_sms_activo_en_todos_los_eventos`.
    activar_canal_en_todos_los_eventos(
        db, persona.id, CanalNotificacion.SMS, notificaciones_activas is not None
    )

    return templates.TemplateResponse(
        "customers_manage/detail.html",
        {
            "request": request,
            "staff": staff,
            "persona": persona,
            "apartamento": _apartamento_actual(db, persona),
            "ocupantes": _ocupantes_de(db, _apartamento_actual(db, persona)),
            "sms_activo": _sms_activo_en_todos_los_eventos(db, persona),
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
