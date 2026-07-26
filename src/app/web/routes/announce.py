# -*- coding: utf-8 -*-
"""
Ruta `/announce` — anunciar un paquete (vista pública, sin privilegios).

`GET` muestra el formulario; `POST` mapea la selección a un `Destinatario`, llama
al servicio de dominio `announce` (que congela el snapshot y crea el Paquete en
`ANUNCIADO`) y muestra la confirmación con el número de seguimiento. Sin captura
de número de guía (la captura el staff al recibir).
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.paquete_service import Destinatario, announce

from ..db import get_db
from ..templating import templates

router = APIRouter()

_OPCIONES_A_NOMBRE = ("yo_mismo", "registrada", "solo_nombre")


@router.get("/anunciar", response_class=HTMLResponse)
def announce_form(request: Request):
    return templates.TemplateResponse(
        "announce/form.html", {"request": request, "a_nombre_de": "yo_mismo"}
    )


@router.post("/anunciar", response_class=HTMLResponse)
def announce_submit(
    request: Request,
    db: Session = Depends(get_db),
    nombre: str = Form(None),
    telefono: str = Form(None),
    acepta_tyc: str = Form(None),
    a_nombre_de: str = Form(None),
    destinatario_telefono: str = Form(None),
    destinatario_nombre: str = Form(None),
):
    # Valores para re-renderizar conservando lo que el usuario escribió.
    valores = {
        "nombre": nombre or "",
        "telefono": telefono or "",
        "a_nombre_de": a_nombre_de if a_nombre_de in _OPCIONES_A_NOMBRE else "yo_mismo",
        "destinatario_telefono": destinatario_telefono or "",
        "destinatario_nombre": destinatario_nombre or "",
    }

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
    if a_nombre_de not in _OPCIONES_A_NOMBRE:
        return _error("Selecciona a nombre de quién llega el paquete.")

    # --- Resolver el Destinatario ------------------------------------------ #
    if a_nombre_de == "yo_mismo":
        destinatario = Destinatario.yo_mismo()
    elif a_nombre_de == "registrada":
        if not (destinatario_telefono or "").strip():
            return _error("Indica el teléfono de la persona registrada.")
        destinatario = Destinatario.persona_registrada(destinatario_telefono)
    else:  # solo_nombre
        if not (destinatario_nombre or "").strip():
            return _error("Indica el nombre del destinatario.")
        destinatario = Destinatario.solo_nombre(destinatario_nombre)

    # --- Anunciar ----------------------------------------------------------- #
    try:
        paquete = announce(db, telefono, nombre, destinatario)
    except LookupError:
        db.rollback()  # deshace el registro implícito del anunciante
        return _error(
            "Ese teléfono no está registrado; usa la opción 'Solo un nombre'."
        )
    except ValueError as exc:
        db.rollback()
        return _error(str(exc))

    return templates.TemplateResponse(
        "announce/confirmacion.html",
        {
            "request": request,
            "tracking_number": paquete.tracking_number,
            "access_code": paquete.access_code,
            "recipient_name": paquete.recipient_name,
        },
    )
