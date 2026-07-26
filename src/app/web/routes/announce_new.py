# -*- coding: utf-8 -*-
"""
Ruta `/announce` — formulario completo de staff (Grupo 6 de
`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`).

Gated por `current_staff` (CUALQUIER rol — tarea operativa rutinaria, no
administrativa). Tres bloques, todos opcionales salvo la regla de cada uno:

  1. Apartamento — Conjunto/Torre/Apartamento, los 3 vacíos o los 3 llenos.
  2. Residentes de esa unidad — filas nombre+teléfono, teléfono OPCIONAL por
     fila (a diferencia del formulario viejo). Usa la entidad Ocupante
     (ADR-0006): el primer residente de una unidad sin Ocupantes previos debe
     tener teléfono. Cada residente CON teléfono también sincroniza el
     `apartamento_actual` de su Persona (mecanismo existente).
  3. Anunciar un paquete — opcional; usa el mismo modo de `Destinatario` que
     `/anunciar` (Grupo 1), con más datos alrededor (teléfono de notificación
     distinto, apartamento explícito del bloque 1).

`POST` es `async` para leer las filas nombre/teléfono repetidas del formulario
vía `request.form()` (`Form(...)` no soporta listas de pares por posición).
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.apartamento_service import get_or_create_apartamento, set_apartamento_actual
from app.domain.notification_sender import NotificationSender
from app.domain.notificacion_service import notificar_evento
from app.domain.ocupante_service import agregar_ocupante
from app.domain.paquete import EstadoPaquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.telefono import normalizar_telefono
from app.domain.usuario import Usuario

from ..db import get_db
from ..notifications import get_notification_sender
from ..security import current_staff
from ..templating import templates

router = APIRouter()


def _blank_to_none(valor):
    valor = (valor or "").strip()
    return valor or None


@router.get("/announce", response_class=HTMLResponse)
def announce_new_form(request: Request, staff: Usuario = Depends(current_staff)):
    return templates.TemplateResponse(
        "announce_new/form.html", {"request": request, "staff": staff}
    )


@router.post("/announce", response_class=HTMLResponse)
async def announce_new_submit(
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    sender: NotificationSender = Depends(get_notification_sender),
):
    form = await request.form()
    conjunto = _blank_to_none(form.get("conjunto"))
    torre = _blank_to_none(form.get("torre"))
    apartamento_v = _blank_to_none(form.get("apartamento"))
    nombres = [str(n).strip() for n in form.getlist("nombre")]
    telefonos = [str(t).strip() for t in form.getlist("telefono")]

    anuncio_telefono = _blank_to_none(form.get("anuncio_telefono"))
    anuncio_nombre = _blank_to_none(form.get("anuncio_nombre"))
    anuncio_notif_telefono = _blank_to_none(form.get("anuncio_notif_telefono"))

    def _error(mensaje: str):
        return templates.TemplateResponse(
            "announce_new/form.html",
            {
                "request": request,
                "staff": staff,
                "error": mensaje,
                "conjunto": conjunto or "",
                "torre": torre or "",
                "apartamento": apartamento_v or "",
                "anuncio_telefono": anuncio_telefono or "",
                "anuncio_nombre": anuncio_nombre or "",
                "anuncio_notif_telefono": anuncio_notif_telefono or "",
            },
            status_code=400,
        )

    # --- Bloque 1: Apartamento (todos vacíos o todos llenos) --------------- #
    partes_apto = [conjunto, torre, apartamento_v]
    if any(partes_apto) and not all(partes_apto):
        return _error("Completa Conjunto, Torre y Apartamento, o deja los tres vacíos.")

    # --- Bloque 2: Residentes (Ocupantes) de esa unidad --------------------- #
    filas = []
    for nombre, telefono in zip(nombres, telefonos):
        if not nombre and not telefono:
            continue  # fila vacía: se ignora
        if not nombre:
            return _error("Cada residente con datos necesita un nombre.")
        filas.append((nombre, telefono or None))

    if filas and not all(partes_apto):
        return _error("Indica el Apartamento antes de agregar residentes.")

    apto = None
    if all(partes_apto):
        apto = get_or_create_apartamento(db, conjunto, torre, apartamento_v)

        if not filas:
            return _error("Agrega al menos un residente de la unidad.")

        try:
            for nombre, telefono in filas:
                ocupante = agregar_ocupante(db, apto, nombre, telefono)
                if ocupante.persona_id is not None and telefono:
                    set_apartamento_actual(db, telefono, apto)
        except ValueError as exc:
            db.rollback()
            return _error(str(exc))

    # --- Bloque 3: Anunciar un paquete (opcional) --------------------------- #
    paquete = None
    if anuncio_telefono or anuncio_nombre:
        if not anuncio_telefono or not anuncio_nombre:
            return _error("Para anunciar un paquete, indica teléfono y nombre.")
        destinatario = Destinatario.declarado_por_cliente(anuncio_nombre)
        try:
            paquete = announce(
                db, anuncio_telefono, anuncio_nombre, destinatario, apartamento=apto
            )
        except ValueError as exc:
            db.rollback()
            return _error(str(exc))
        if anuncio_notif_telefono:
            try:
                paquete.recipient_phone = normalizar_telefono(anuncio_notif_telefono)
            except ValueError as exc:
                db.rollback()
                return _error(str(exc))
            db.flush()
        notificar_evento(db, paquete, EstadoPaquete.ANUNCIADO, sender)

    return templates.TemplateResponse(
        "announce_new/form.html",
        {
            "request": request,
            "staff": staff,
            "apartamento_creado": apto,
            "paquete_creado": paquete,
        },
    )
