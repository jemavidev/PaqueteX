# -*- coding: utf-8 -*-
"""
Ruta `/anunciar` — anunciar un paquete (vista pública, sin privilegios).

Simplificada (Grupo 1 de `ajustes-post-referencia-funcional/REQUERIMIENTOS.md`):
el cliente solo declara Nombre + Teléfono + Términos y Condiciones — no elige
"a nombre de quién llega". El nombre declarado se guarda tal cual
(`Destinatario.declarado_por_cliente`); si no coincide con el nombre ya
registrado del Anunciante, el staff lo verá señalado en `/paquetes` y lo
resuelve desde `/announce` (rebanada aparte). Sin captura de guía del
transportador (la captura el staff al recibir).
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.notification_sender import NotificationSender
from app.domain.notificacion_service import preparar_notificacion
from app.domain.paquete import EstadoPaquete
from app.domain.paquete_service import Destinatario, announce

from ..db import get_db
from ..notifications import enviar_en_segundo_plano, get_notification_sender
from ..templating import templates

router = APIRouter()


@router.get("/anunciar", response_class=HTMLResponse)
def announce_form(request: Request):
    return templates.TemplateResponse("announce/form.html", {"request": request})


@router.post("/anunciar", response_class=HTMLResponse)
def announce_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    sender: NotificationSender = Depends(get_notification_sender),
    nombre: str = Form(None),
    telefono: str = Form(None),
    acepta_tyc: str = Form(None),
):
    # Valores para re-renderizar conservando lo que el usuario escribió.
    valores = {"nombre": nombre or "", "telefono": telefono or ""}

    def _error(mensaje: str):
        return templates.TemplateResponse(
            "announce/form.html",
            {"request": request, "error": mensaje, **valores},
            status_code=400,
        )

    # --- Validación de campos obligatorios --------------------------------- #
    if not (nombre or "").strip():
        return _error("El nombre es obligatorio.")
    if not (telefono or "").strip():
        return _error("El teléfono es obligatorio.")
    if not acepta_tyc:
        return _error("Debes aceptar los Términos y Condiciones.")

    # --- Anunciar ----------------------------------------------------------- #
    try:
        paquete = announce(
            db, telefono, nombre, Destinatario.declarado_por_cliente(nombre)
        )
    except ValueError as exc:
        db.rollback()
        return _error(str(exc))

    resultado = preparar_notificacion(db, paquete, EstadoPaquete.ANUNCIADO)
    if resultado is not None:
        background_tasks.add_task(enviar_en_segundo_plano, sender, *resultado)

    return templates.TemplateResponse(
        "announce/confirmacion.html",
        {
            "request": request,
            "nombre": paquete.recipient_name,
            "telefono": paquete.announced_by_phone,
            "access_code": paquete.access_code,
            "snapshot_conjunto": paquete.snapshot_conjunto,
            "snapshot_torre": paquete.snapshot_torre,
            "snapshot_apartamento": paquete.snapshot_apartamento,
        },
    )
