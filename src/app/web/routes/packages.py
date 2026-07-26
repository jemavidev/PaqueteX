# -*- coding: utf-8 -*-
"""
Vista de staff `/paquetes` — lista + acciones del ciclo de vida.

Protegida por `current_staff`: el `Usuario` de la sesión es el **actor** de cada
transición (recibir/entregar/cancelar), nunca un id enviado por el cliente. Las
acciones exitosas redirigen a `/paquetes` (PRG); las transiciones inválidas
re-muestran la lista con un aviso, sin efecto.
"""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.domain.foto_storage import FotoStorage
from app.domain.notification_sender import NotificationSender
from app.domain.notificacion_service import notificar_evento
from app.domain.paquete import CondicionPaquete, EstadoPaquete, MotivoCancelacion, Paquete, TipoPaquete
from app.domain.paquete_foto_service import agregar_foto, listar_fotos
from app.domain.paquete_lifecycle import (
    TransicionInvalida,
    cancel,
    corregir_destinatario,
    deliver,
    receive,
)
from app.domain.persona import Persona
from app.domain.telefono import normalizar_telefono
from app.domain.usuario import Usuario

from ..db import get_db
from ..fotos import get_foto_storage
from ..notifications import get_notification_sender
from ..security import current_staff
from ..templating import templates

router = APIRouter()

_POR_PAGINA = 20


def _nombre_no_coincide(db: Session, paquete: Paquete) -> bool:
    """True si el nombre anunciado difiere del nombre YA REGISTRADO del
    Anunciante — calculado al leer (no se guarda), así que si el staff corrige
    el nombre de la Persona la advertencia desaparece sola."""
    persona = db.get(Persona, paquete.announced_by_persona_id)
    if persona is None or not persona.nombre:
        return False
    return persona.nombre.strip().lower() != (paquete.recipient_name or "").strip().lower()


def _listar(
    db: Session,
    estado: str = None,
    q: str = None,
    torre: str = None,
    apartamento: str = None,
    pagina: int = 1,
):
    """Lista filtrada y paginada. Los filtros se combinan con AND; `q` cubre
    varios campos a la vez (código de acceso, guía, nombre parcial, teléfono)."""
    query = db.query(Paquete)

    if estado:
        query = query.filter(Paquete.estado == estado)

    q = (q or "").strip()
    if q:
        condiciones = [
            Paquete.access_code == q,
            Paquete.guide_number == q,
            Paquete.recipient_name.ilike(f"%{q}%"),
        ]
        try:
            telefono = normalizar_telefono(q)
        except ValueError:
            telefono = None
        if telefono is not None:
            condiciones.append(Paquete.announced_by_phone == telefono)
            condiciones.append(Paquete.recipient_phone == telefono)
        query = query.filter(or_(*condiciones))

    torre = (torre or "").strip()
    if torre:
        query = query.filter(Paquete.snapshot_torre.ilike(f"%{torre}%"))

    apartamento = (apartamento or "").strip()
    if apartamento:
        query = query.filter(Paquete.snapshot_apartamento.ilike(f"%{apartamento}%"))

    total = query.count()
    total_paginas = max(1, -(-total // _POR_PAGINA))  # ceil sin importar float
    pagina = max(1, min(pagina, total_paginas))

    paquetes = (
        query.order_by(Paquete.announced_at.desc())
        .offset((pagina - 1) * _POR_PAGINA)
        .limit(_POR_PAGINA)
        .all()
    )
    for p in paquetes:
        # Atributo transitorio (no persistido), solo para la plantilla.
        p.advertencia_nombre = _nombre_no_coincide(db, p)

    return paquetes, pagina, total_paginas


def _render_lista(
    request,
    db,
    staff,
    error=None,
    status_code=200,
    estado=None,
    q=None,
    torre=None,
    apartamento=None,
    pagina=1,
):
    paquetes, pagina_actual, total_paginas = _listar(
        db, estado=estado, q=q, torre=torre, apartamento=apartamento, pagina=pagina
    )
    return templates.TemplateResponse(
        "packages/list.html",
        {
            "request": request,
            "paquetes": paquetes,
            "staff": staff,
            "error": error,
            "motivos": list(MotivoCancelacion),
            "tipos": list(TipoPaquete),
            "condiciones": list(CondicionPaquete),
            "estados": list(EstadoPaquete),
            "filtro_estado": estado or "",
            "filtro_q": q or "",
            "filtro_torre": torre or "",
            "filtro_apartamento": apartamento or "",
            "pagina_actual": pagina_actual,
            "total_paginas": total_paginas,
        },
        status_code=status_code,
    )


def _get_paquete_o_404(db: Session, paquete_id: str) -> Paquete:
    try:
        pid = uuid.UUID(paquete_id)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Paquete no encontrado")
    paquete = db.get(Paquete, pid)
    if paquete is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Paquete no encontrado")
    return paquete


@router.get("/paquetes", response_class=HTMLResponse)
def packages_list(
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    estado: str = None,
    q: str = None,
    torre: str = None,
    apartamento: str = None,
    pagina: int = 1,
):
    return _render_lista(
        request, db, staff, estado=estado, q=q, torre=torre, apartamento=apartamento,
        pagina=pagina,
    )


@router.post("/paquetes/{paquete_id}/recibir")
async def receive_action(
    paquete_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    sender: NotificationSender = Depends(get_notification_sender),
    storage: FotoStorage = Depends(get_foto_storage),
    guide_number: str = Form(None),
    package_type: str = Form(None),
    package_condition: str = Form(None),
    foto: UploadFile = File(None),
):
    paquete = _get_paquete_o_404(db, paquete_id)
    guia = (guide_number or "").strip() or None
    tipo = TipoPaquete(package_type) if package_type else None
    condicion = CondicionPaquete(package_condition) if package_condition else None
    try:
        receive(db, paquete, staff, guia, package_type=tipo, package_condition=condicion)
    except TransicionInvalida as exc:
        return _render_lista(request, db, staff, error=str(exc), status_code=400)
    if foto is not None and foto.filename:
        contenido = await foto.read()
        if contenido:
            agregar_foto(db, paquete, storage, foto.filename, contenido)
    notificar_evento(db, paquete, EstadoPaquete.RECIBIDO, sender)
    return RedirectResponse("/paquetes", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/paquetes/{paquete_id}/entregar")
def deliver_action(
    paquete_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    sender: NotificationSender = Depends(get_notification_sender),
):
    paquete = _get_paquete_o_404(db, paquete_id)
    try:
        deliver(db, paquete, staff)
    except TransicionInvalida as exc:
        return _render_lista(request, db, staff, error=str(exc), status_code=400)
    notificar_evento(db, paquete, EstadoPaquete.ENTREGADO, sender)
    return RedirectResponse("/paquetes", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/paquetes/{paquete_id}/cancelar")
def cancel_action(
    paquete_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    sender: NotificationSender = Depends(get_notification_sender),
    motivo: str = Form(None),
):
    paquete = _get_paquete_o_404(db, paquete_id)
    try:
        cancel(db, paquete, staff, motivo)
    except (TransicionInvalida, ValueError) as exc:
        return _render_lista(request, db, staff, error=str(exc), status_code=400)
    notificar_evento(db, paquete, EstadoPaquete.CANCELADO, sender)
    return RedirectResponse("/paquetes", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/paquetes/{paquete_id}/corregir")
def correct_recipient_action(
    paquete_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    recipient_name: str = Form(None),
    recipient_phone: str = Form(None),
):
    """Corrige destinatario de un Paquete `ANUNCIADO` — excepción acotada a
    ADR-0001 (ver `paquete_lifecycle.corregir_destinatario`)."""
    paquete = _get_paquete_o_404(db, paquete_id)
    try:
        corregir_destinatario(db, paquete, staff, recipient_name, recipient_phone)
    except (TransicionInvalida, ValueError) as exc:
        return _render_lista(request, db, staff, error=str(exc), status_code=400)
    return RedirectResponse("/paquetes", status_code=status.HTTP_303_SEE_OTHER)
