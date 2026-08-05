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
from app.domain.apartamento_service import (
    listar_catalogo_por_torre,
    move_resident,
    resolver_apartamento,
)
from app.domain.ocupante import Ocupante
from app.domain.ocupante_service import (
    MAX_OCUPANTES_ACTIVOS,
    agregar_ocupante,
    asociar_telefono_a_ocupante,
    confirmar_ocupante,
    dar_de_baja_ocupante,
    desvincular_telefono_ocupante,
    editar_telefono_ocupante,
    listar_ocupantes,
    ocupante_activo_de_persona,
    promover_a_principal,
)
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


def _contexto_detalle(db: Session, staff: Usuario, persona: Persona) -> dict:
    """Contexto común a la ficha de cliente y a cualquier re-render tras un
    error o una acción sobre Ocupantes (.scratch/mis-datos, ticket 10)."""
    apto = _apartamento_actual(db, persona)
    return {
        "staff": staff,
        "persona": persona,
        "apartamento": apto,
        "catalogo_torres": listar_catalogo_por_torre(db),
        "ocupantes": _ocupantes_de(db, apto),
        "sms_activo": _sms_activo_en_todos_los_eventos(db, persona),
        "limite_ocupantes": MAX_OCUPANTES_ACTIVOS,
    }


def _render_detalle_con_error(
    request: Request, db: Session, staff: Usuario, persona: Persona, mensaje: str
) -> HTMLResponse:
    db.rollback()
    contexto = _contexto_detalle(db, staff, persona)
    contexto["request"] = request
    contexto["error"] = mensaje
    return templates.TemplateResponse(
        "customers_manage/detail.html", contexto, status_code=400
    )


@router.get("/residentes/{persona_id}", response_class=HTMLResponse)
def customers_manage_detail(
    persona_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    persona = _get_persona_o_404(db, persona_id)
    contexto = _contexto_detalle(db, staff, persona)
    contexto["request"] = request
    return templates.TemplateResponse("customers_manage/detail.html", contexto)


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
        # Único origen de ValueError acá es el formato del email (ver
        # persona_service.update_datos_personales) -- seguro marcar ese
        # campo siempre.
        db.rollback()
        contexto = _contexto_detalle(db, staff, persona)
        contexto.update({"request": request, "error": str(exc), "error_email": str(exc)})
        return templates.TemplateResponse(
            "customers_manage/detail.html", contexto, status_code=400
        )

    # Checkbox: presente (marcado) = True; ausente (desmarcado) = False —
    # distinto del resto de campos, cuya ausencia significa "no tocar".
    # Ver docstring de `_sms_activo_en_todos_los_eventos`.
    activar_canal_en_todos_los_eventos(
        db, persona.id, CanalNotificacion.SMS, notificaciones_activas is not None
    )

    contexto = _contexto_detalle(db, staff, persona)
    contexto["request"] = request
    contexto["guardado"] = True
    return templates.TemplateResponse("customers_manage/detail.html", contexto)


@router.post("/residentes/{persona_id}/apartamento", response_class=HTMLResponse)
def customers_manage_asignar_apartamento(
    persona_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    torre: str = Form(None),
    apartamento: str = Form(None),
):
    """Asigna, cambia o desvincula la Torre/Apartamento de un cliente --
    única vía para tocar `apartamento_actual_id` ahora que `/mis-datos` es de
    solo lectura para el residente (.scratch/pendientes-cliente): la
    asignación es exclusiva del personal de Papyrus."""
    persona = _get_persona_o_404(db, persona_id)
    torre_v = _blank_to_none(torre)
    apartamento_v = _blank_to_none(apartamento)
    partes = [torre_v, apartamento_v]

    if any(partes) and not all(partes):
        return _render_detalle_con_error(
            request, db, staff, persona, "Completa Torre y Apartamento, o deja los dos vacíos."
        )

    nuevo_apto = None
    if all(partes):
        try:
            nuevo_apto = resolver_apartamento(db, torre_v, apartamento_v)
        except ValueError as exc:
            return _render_detalle_con_error(request, db, staff, persona, str(exc))

    # Mismo guard que tenía el autoservicio del residente (.scratch/
    # apartamento-catalogo-confirmacion): reasignar mientras queden otros
    # Residentes activos en la unidad ACTUAL dejaría ese roster huérfano --
    # promover a otro principal o dar de baja a todos primero.
    mi_ocupante = ocupante_activo_de_persona(db, persona.id)
    if mi_ocupante is not None and (
        nuevo_apto is None or mi_ocupante.apartamento_id != nuevo_apto.id
    ):
        return _render_detalle_con_error(
            request, db, staff, persona,
            "No se puede reasignar mientras este cliente tenga otros Residentes "
            "activos en su unidad actual -- convierte a otro en principal, o "
            "dales de baja a todos antes de reasignar.",
        )

    move_resident(db, persona.telefono, nuevo_apto)
    contexto = _contexto_detalle(db, staff, persona)
    contexto["request"] = request
    contexto["guardado"] = True
    return templates.TemplateResponse("customers_manage/detail.html", contexto)


def _ocupante_o_404(db: Session, ocupante_id: str) -> Ocupante:
    try:
        oid = uuid.UUID(ocupante_id)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocupante no encontrado")
    ocupante = db.get(Ocupante, oid)
    if ocupante is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocupante no encontrado")
    return ocupante


@router.post("/residentes/{persona_id}/ocupantes", response_class=HTMLResponse)
def customers_manage_ocupante_crear(
    persona_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    nombre: str = Form(None),
    telefono: str = Form(None),
):
    """Staff sin restricción (.scratch/mis-datos, ticket 10) — mismas
    funciones de dominio que `/mis-datos` (ticket 03), sin exigir que el
    staff sea "principal" de nada."""
    persona = _get_persona_o_404(db, persona_id)
    apto = _apartamento_actual(db, persona)
    nombre_v = _blank_to_none(nombre)
    if apto is None or not nombre_v:
        return _render_detalle_con_error(
            request, db, staff, persona,
            "Este cliente no tiene apartamento asignado, o falta el nombre." if apto is None
            else "El nombre del Ocupante es obligatorio.",
        )
    try:
        agregar_ocupante(db, apto, nombre_v, _blank_to_none(telefono))
    except ValueError as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc))
    return RedirectResponse(f"/residentes/{persona.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post(
    "/residentes/{persona_id}/ocupantes/{ocupante_id}/telefono", response_class=HTMLResponse
)
def customers_manage_ocupante_asociar_telefono(
    persona_id: str,
    ocupante_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    telefono: str = Form(None),
):
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)
    telefono_v = _blank_to_none(telefono)
    if not telefono_v:
        return _render_detalle_con_error(request, db, staff, persona, "El teléfono es obligatorio.")
    try:
        if ocupante.persona_id is None:
            asociar_telefono_a_ocupante(db, ocupante, telefono_v)
        else:
            # Editar un teléfono YA asociado (pedido del cliente,
            # `.scratch/pendientes-cliente/issues/35`) -- el principal se
            # sigue excluyendo (ver `editar_telefono_ocupante`); no hay hoy
            # una vía de staff para renombrar el teléfono PROPIO de un
            # principal, mismo estado que antes de este pedido.
            editar_telefono_ocupante(db, ocupante, telefono_v)
    except ValueError as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc))
    return RedirectResponse(f"/residentes/{persona.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post(
    "/residentes/{persona_id}/ocupantes/{ocupante_id}/desvincular-telefono",
    response_class=HTMLResponse,
)
def customers_manage_ocupante_desvincular_telefono(
    persona_id: str,
    ocupante_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)
    try:
        desvincular_telefono_ocupante(db, ocupante)
    except ValueError as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc))
    return RedirectResponse(f"/residentes/{persona.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/residentes/{persona_id}/ocupantes/{ocupante_id}/baja", response_class=HTMLResponse)
def customers_manage_ocupante_dar_de_baja(
    persona_id: str,
    ocupante_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)
    try:
        dar_de_baja_ocupante(db, ocupante)
    except ValueError as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc))
    return RedirectResponse(f"/residentes/{persona.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post(
    "/residentes/{persona_id}/ocupantes/{ocupante_id}/confirmar", response_class=HTMLResponse
)
def customers_manage_ocupante_confirmar(
    persona_id: str,
    ocupante_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    """Confirma un Ocupante pending (`.scratch/apartamento-catalogo-
    confirmacion`, ticket 07) — cualquier rol de staff, sin restricción
    (mismo patrón que el resto de esta gestión). Si es el primero de su
    Apartamento, queda como principal en el mismo acto (`confirmar_ocupante`)."""
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)
    try:
        confirmar_ocupante(db, ocupante, staff)
    except (PermissionError, ValueError) as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc))
    return RedirectResponse(f"/residentes/{persona.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post(
    "/residentes/{persona_id}/ocupantes/{ocupante_id}/promover", response_class=HTMLResponse
)
def customers_manage_ocupante_promover(
    persona_id: str,
    ocupante_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    persona = _get_persona_o_404(db, persona_id)
    ocupante = _ocupante_o_404(db, ocupante_id)
    try:
        promover_a_principal(db, ocupante)
    except ValueError as exc:
        return _render_detalle_con_error(request, db, staff, persona, str(exc))
    return RedirectResponse(f"/residentes/{persona.id}", status_code=status.HTTP_303_SEE_OTHER)


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
