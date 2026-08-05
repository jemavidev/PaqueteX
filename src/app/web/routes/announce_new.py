# -*- coding: utf-8 -*-
"""
Ruta `/announce` — formulario completo de staff (Grupo 6 de
`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`).

Gated por `current_staff` (CUALQUIER rol — tarea operativa rutinaria, no
administrativa). Tres bloques, todos opcionales salvo la regla de cada uno:

  1. Apartamento — Torre/Apartamento del catálogo cerrado (`.scratch/
     apartamento-catalogo-confirmacion`, ticket 05), los 2 vacíos o los 2
     llenos. El Conjunto es único y global -- no se le pide al staff.
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

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.domain.apartamento_service import (
    listar_catalogo_por_torre,
    resolver_apartamento,
    set_apartamento_actual,
)
from app.domain.notification_sender import NotificationSender
from app.domain.notificacion_service import preparar_notificacion
from app.domain.ocupante_service import agregar_ocupante, listar_ocupantes, ocupante_de_persona
from app.domain.paquete import EstadoPaquete
from app.domain.paquete_service import Destinatario, announce
from app.domain.persona_service import get_or_create_persona
from app.domain.telefono import normalizar_telefono
from app.domain.texto import normalizar_nombre
from app.domain.usuario import Usuario

from ..db import get_db
from ..notifications import enviar_en_segundo_plano, get_notification_sender
from ..security import current_staff
from ..templating import templates

router = APIRouter()


def _blank_to_none(valor):
    valor = (valor or "").strip()
    return valor or None


@router.get("/announce", response_class=HTMLResponse)
def announce_new_form(
    request: Request, db: Session = Depends(get_db), staff: Usuario = Depends(current_staff)
):
    return templates.TemplateResponse(
        "announce_new/form.html",
        {"request": request, "staff": staff, "catalogo_torres": listar_catalogo_por_torre(db)},
    )


@router.post("/announce", response_class=HTMLResponse)
async def announce_new_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    sender: NotificationSender = Depends(get_notification_sender),
):
    form = await request.form()
    torre = _blank_to_none(form.get("torre"))
    apartamento_v = _blank_to_none(form.get("apartamento"))
    nombres = [str(n).strip() for n in form.getlist("nombre")]
    telefonos = [str(t).strip() for t in form.getlist("telefono")]

    anuncio_telefono = _blank_to_none(form.get("anuncio_telefono"))
    anuncio_nombre = _blank_to_none(form.get("anuncio_nombre"))
    anuncio_notif_telefono = _blank_to_none(form.get("anuncio_notif_telefono"))

    _CAMPOS_MARCABLES = (
        "torre", "apartamento",
        "anuncio_telefono", "anuncio_nombre", "anuncio_notif_telefono",
    )

    def _error(mensaje: str, campos: list[str] = None):
        # `campos` marca los inputs específicos en rojo (retroalimentación en
        # vivo 2026-08-02) -- las filas dinámicas de "Residentes" quedan
        # fuera a propósito: son inputs clonados por JS sin macro/error box
        # propio, y el toast ya identifica el problema con suficiente
        # claridad para esta herramienta interna de staff.
        return templates.TemplateResponse(
            "announce_new/form.html",
            {
                "request": request,
                "staff": staff,
                "catalogo_torres": listar_catalogo_por_torre(db),
                "error": mensaje,
                "torre": torre or "",
                "apartamento": apartamento_v or "",
                "anuncio_telefono": anuncio_telefono or "",
                "anuncio_nombre": anuncio_nombre or "",
                "anuncio_notif_telefono": anuncio_notif_telefono or "",
                **{
                    f"error_{c}": (mensaje if c in (campos or []) else None)
                    for c in _CAMPOS_MARCABLES
                },
            },
            status_code=400,
        )

    # --- Bloque 1: Apartamento (Torre + Apartamento, los dos vacíos o los dos llenos) --- #
    partes_apto = [torre, apartamento_v]
    if any(partes_apto) and not all(partes_apto):
        campos_vacios = [c for c, v in zip(["torre", "apartamento"], partes_apto) if not v]
        return _error("Completa Torre y Apartamento, o deja los dos vacíos.", campos=campos_vacios)

    # --- Bloque 2: Residentes (Ocupantes) de esa unidad --------------------- #
    filas = []
    for nombre, telefono in zip(nombres, telefonos):
        if not nombre and not telefono:
            continue  # fila vacía: se ignora
        if not nombre:
            return _error("Cada residente con datos necesita un nombre.")
        filas.append((nombre, telefono or None))

    if filas and not all(partes_apto):
        campos_vacios = [c for c, v in zip(["torre", "apartamento"], partes_apto) if not v]
        return _error("Indica el Apartamento antes de agregar residentes.", campos=campos_vacios)

    apto = None
    if all(partes_apto):
        # Catálogo cerrado (`.scratch/apartamento-catalogo-confirmacion`,
        # ticket 05): Torre + Apartamento se eligen del catálogo -- ya no hay
        # Conjunto que el staff escriba, es único y global.
        try:
            apto = resolver_apartamento(db, torre, apartamento_v)
        except ValueError as exc:
            return _error(str(exc), campos=["torre", "apartamento"])

        if not filas:
            return _error("Agrega al menos un residente de la unidad.")

        # Reenviar el mismo formulario (doble clic, o declarar la misma
        # unidad de nuevo para otro trámite) no debe duplicar a un residente
        # que YA está activo en esta unidad -- mismo espíritu que la guardia
        # de idempotencia de /mis-datos (ticket 01), pero acá hay varias
        # filas a la vez. Con teléfono, la identidad es la Persona; sin
        # teléfono, el único indicio disponible es el nombre normalizado
        # (.scratch/pendientes-cliente/issues/41).
        nombres_sin_telefono_ya_activos = {
            o.nombre for o in listar_ocupantes(db, apto) if o.persona_id is None
        }
        try:
            for nombre, telefono in filas:
                if telefono:
                    persona = get_or_create_persona(db, telefono, nombre)
                    if ocupante_de_persona(db, apto, persona.id) is not None:
                        continue  # ya es Ocupante activo de esta misma unidad
                    ocupante = agregar_ocupante(db, apto, nombre, telefono)
                    if ocupante.persona_id is not None:
                        set_apartamento_actual(db, telefono, apto)
                else:
                    nombre_norm = normalizar_nombre(nombre)
                    if nombre_norm in nombres_sin_telefono_ya_activos:
                        continue
                    agregar_ocupante(db, apto, nombre, None)
                    nombres_sin_telefono_ya_activos.add(nombre_norm)
        except ValueError as exc:
            db.rollback()
            return _error(str(exc))

    # --- Bloque 3: Anunciar un paquete (opcional) --------------------------- #
    paquete = None
    if anuncio_telefono or anuncio_nombre:
        if not anuncio_telefono or not anuncio_nombre:
            campos_vacios = [
                c for c, v in [("anuncio_telefono", anuncio_telefono), ("anuncio_nombre", anuncio_nombre)] if not v
            ]
            return _error("Para anunciar un paquete, indica teléfono y nombre.", campos=campos_vacios)
        destinatario = Destinatario.declarado_por_cliente(anuncio_nombre)
        try:
            paquete = announce(
                db,
                anuncio_telefono,
                anuncio_nombre,
                destinatario,
                apartamento=apto,
                staff_actor=staff,
            )
        except ValueError as exc:
            db.rollback()
            return _error(str(exc), campos=["anuncio_telefono"])
        if anuncio_notif_telefono:
            try:
                paquete.recipient_phone = normalizar_telefono(anuncio_notif_telefono)
            except ValueError as exc:
                db.rollback()
                return _error(str(exc), campos=["anuncio_notif_telefono"])
            db.flush()
        resultado = preparar_notificacion(db, paquete, EstadoPaquete.ANUNCIADO)
        if resultado is not None:
            background_tasks.add_task(enviar_en_segundo_plano, sender, *resultado)

    return templates.TemplateResponse(
        "announce_new/form.html",
        {
            "request": request,
            "staff": staff,
            "catalogo_torres": listar_catalogo_por_torre(db),
            "apartamento_creado": apto,
            "paquete_creado": paquete,
        },
    )
