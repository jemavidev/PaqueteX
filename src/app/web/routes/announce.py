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

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.paquete_service import Destinatario, announce

from ..db import get_db
from ..templating import templates

router = APIRouter()


@router.get("/anunciar", response_class=HTMLResponse)
def announce_form(request: Request):
    return templates.TemplateResponse("announce/form.html", {"request": request})


@router.post("/anunciar", response_class=HTMLResponse)
def announce_submit(
    request: Request,
    db: Session = Depends(get_db),
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
