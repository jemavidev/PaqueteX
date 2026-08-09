# -*- coding: utf-8 -*-
"""
Vista de staff `/paquetes` — lista + acciones del ciclo de vida.

Protegida por `current_staff`: el `Usuario` de la sesión es el **actor** de cada
transición (recibir/entregar/cancelar), nunca un id enviado por el cliente. Las
acciones exitosas redirigen a `/paquetes` (PRG); las transiciones inválidas
re-muestran la lista con un aviso, sin efecto.
"""

import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, sessionmaker

from app.domain.actor_service import nombre_usuario
from app.domain.apartamento_service import buscar_apartamento_por_terna
from app.domain.foto_storage import FotoStorage
from app.domain.notification_sender import NotificationSender
from app.domain.notificacion_service import preparar_notificacion
from app.domain.ocupante_service import agregar_ocupante, telefono_notificacion_ocupante
from app.domain.paquete import CondicionPaquete, EstadoPaquete, MotivoCancelacion, Paquete, TipoPaquete
from app.domain.paquete_correccion_service import candidatos_correccion
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

from ..db import get_db, get_session_factory
from ..fotos import get_foto_storage, subir_fotos_diferido
from ..notifications import enviar_en_segundo_plano, get_notification_sender
from ..security import current_staff
from ..templating import templates

router = APIRouter()

_POR_PAGINA = 20


def _notificar_diferido(background_tasks, db, paquete, evento, sender):
    """Resuelve destino+mensaje SÍNCRONO (rápido, solo BD) y difiere el envío
    real a un BackgroundTask -- ver `notificacion_service.preparar_notificacion`
    y `notifications.enviar_en_segundo_plano`. Compartido por
    recibir/entregar/cancelar, las 3 transiciones de este archivo que
    notifican."""
    resultado = preparar_notificacion(db, paquete, evento)
    if resultado is not None:
        background_tasks.add_task(enviar_en_segundo_plano, sender, *resultado)


def _nombre_no_coincide(db: Session, paquete: Paquete) -> bool:
    """True si el nombre anunciado difiere del nombre YA REGISTRADO del
    Anunciante — calculado al leer (no se guarda), así que si el staff corrige
    el nombre de la Persona la advertencia desaparece sola."""
    persona = db.get(Persona, paquete.announced_by_persona_id)
    if persona is None or not persona.nombre:
        return False
    return persona.nombre.strip().lower() != (paquete.recipient_name or "").strip().lower()


def _actor_ultima_accion(db: Session, paquete: Paquete) -> str | None:
    """Quién hizo la transición más avanzada que ya ocurrió (Grupo 11, Ronda
    2) — Cancelado y Entregado son mutuamente excluyentes (ambos terminales),
    por eso el orden de prioridad alcanza para desambiguar."""
    for usuario_id in (
        paquete.cancelled_by_usuario_id,
        paquete.delivered_by_usuario_id,
        paquete.received_by_usuario_id,
    ):
        nombre = nombre_usuario(db, usuario_id)
        if nombre is not None:
            return nombre
    nombre_staff_anuncio = nombre_usuario(db, paquete.announced_by_usuario_id)
    if nombre_staff_anuncio is not None:
        return nombre_staff_anuncio
    persona = db.get(Persona, paquete.announced_by_persona_id)
    return persona.nombre if persona and persona.nombre else None


def _listar(
    db: Session,
    estado: str = None,
    q: str = None,
    pagina: int = 1,
):
    """Lista filtrada y paginada. `estado` se combina con `q` por AND; `q` es un
    único criterio de texto que cubre, todos combinados con OR y todos con
    coincidencia parcial: código de acceso, guía, nombre del destinatario,
    nombre registrado y usuario de WhatsApp del Anunciante (requiere join a
    Persona), teléfono (anunciante o destinatario), Torre y Apartamento del
    snapshot -- un solo campo para "cualquier dato que el staff recuerde" en
    vez de cajas separadas por criterio (.scratch/paquetes-busqueda-viva)."""
    query = db.query(Paquete)

    if estado:
        query = query.filter(Paquete.estado == estado)

    q = (q or "").strip()
    if q:
        patron = f"%{q}%"
        query = query.outerjoin(Persona, Paquete.announced_by_persona_id == Persona.id)
        condiciones = [
            Paquete.access_code.ilike(patron),
            Paquete.guide_number.ilike(patron),
            Paquete.recipient_name.ilike(patron),
            Persona.nombre.ilike(patron),
            Persona.whatsapp_usuario.ilike(patron),
            Paquete.snapshot_torre.ilike(patron),
            Paquete.snapshot_apartamento.ilike(patron),
        ]
        try:
            telefono = normalizar_telefono(q)
        except ValueError:
            telefono = None
        if telefono is not None:
            condiciones.append(Paquete.announced_by_phone == telefono)
            condiciones.append(Paquete.recipient_phone == telefono)
        query = query.filter(or_(*condiciones))

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
        # Atributos transitorios (no persistidos), solo para la plantilla.
        p.advertencia_nombre = _nombre_no_coincide(db, p)
        p.actor_ultima_accion = _actor_ultima_accion(db, p)
        p.candidatos_correccion = (
            candidatos_correccion(db, p) if p.estado is EstadoPaquete.ANUNCIADO else []
        )

    return paquetes, pagina, total_paginas


def _render_lista(
    request,
    db,
    staff,
    error=None,
    status_code=200,
    estado=None,
    q=None,
    pagina=1,
    error_paquete_id=None,
    error_campo=None,
):
    paquetes, pagina_actual, total_paginas = _listar(db, estado=estado, q=q, pagina=pagina)
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
            "pagina_actual": pagina_actual,
            "total_paginas": total_paginas,
            # Identifica CUÁL paquete/modal tenía el error, para reabrirlo
            # y marcar su campo específico (retroalimentación en vivo
            # 2026-08-02) -- solo aplica hoy al modal "Corregir" (el único
            # con inputs de texto reales; los demás usan chips sin estado
            # de error propio, o no tienen ningún input de texto).
            "error_paquete_id": error_paquete_id,
            "error_campo": error_campo,
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
    pagina: int = 1,
):
    return _render_lista(request, db, staff, estado=estado, q=q, pagina=pagina)


@router.post("/paquetes/{paquete_id}/recibir")
async def receive_action(
    paquete_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    sender: NotificationSender = Depends(get_notification_sender),
    storage: FotoStorage = Depends(get_foto_storage),
    session_factory: sessionmaker = Depends(get_session_factory),
    guide_number: str = Form(None),
    package_type: str = Form(None),
    package_condition: str = Form(None),
    fotos: list[UploadFile] = File(None),
):
    paquete = _get_paquete_o_404(db, paquete_id)
    guia = (guide_number or "").strip() or None
    tipo = TipoPaquete(package_type) if package_type else None
    condicion = CondicionPaquete(package_condition) if package_condition else None
    try:
        receive(db, paquete, staff, guia, package_type=tipo, package_condition=condicion)
    except TransicionInvalida as exc:
        return _render_lista(request, db, staff, error=str(exc), status_code=400)
    # Commit explícito ACÁ (no esperar al commit normal del `get_db` al
    # cerrar el request): el BackgroundTask de fotos abre su PROPIA sesión y
    # busca este Paquete por id -- FastAPI no garantiza que el commit de la
    # sesión del request corra antes que los BackgroundTasks, así que sin
    # este commit hay una ventana de carrera real donde esa búsqueda no
    # encontraría todavía la transición a RECIBIDO.
    db.commit()
    # Hasta 3 fotos (Grupo 15, Ronda 2) -- el tope real vive en el servicio
    # (agregar_foto). Acá solo leemos los bytes a memoria (el `UploadFile` no
    # sobrevive fuera del request) y diferimos la subida real (S3, la parte
    # lenta) a un BackgroundTask -- recibir NUNCA depende de que las fotos
    # terminen de subir.
    archivos = []
    for archivo in fotos or []:
        if not archivo.filename:
            continue
        contenido = await archivo.read()
        if not contenido:
            continue
        archivos.append((archivo.filename, contenido))
    if archivos:
        background_tasks.add_task(
            subir_fotos_diferido, session_factory, storage, paquete.id, archivos
        )
    _notificar_diferido(background_tasks, db, paquete, EstadoPaquete.RECIBIDO, sender)
    return RedirectResponse("/paquetes", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/paquetes/{paquete_id}/entregar")
def deliver_action(
    paquete_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    sender: NotificationSender = Depends(get_notification_sender),
):
    paquete = _get_paquete_o_404(db, paquete_id)
    try:
        deliver(db, paquete, staff)
    except TransicionInvalida as exc:
        return _render_lista(request, db, staff, error=str(exc), status_code=400)
    _notificar_diferido(background_tasks, db, paquete, EstadoPaquete.ENTREGADO, sender)
    return RedirectResponse("/paquetes", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/paquetes/{paquete_id}/cancelar")
def cancel_action(
    paquete_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
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
    _notificar_diferido(background_tasks, db, paquete, EstadoPaquete.CANCELADO, sender)
    return RedirectResponse("/paquetes", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/paquetes/{paquete_id}/corregir")
def correct_recipient_action(
    paquete_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    candidato_idx: str = Form(None),
    recipient_name: str = Form(None),
    recipient_phone: str = Form(None),
    nuevo_ocupante_nombre: str = Form(None),
    nuevo_ocupante_telefono: str = Form(None),
):
    """Corrige destinatario de un Paquete `ANUNCIADO` — excepción acotada a
    ADR-0001 (ver `paquete_lifecycle.corregir_destinatario`).

    Grupo 16 (Ronda 2): si hay candidatos conocidos (Ocupantes del
    Apartamento del snapshot, o el propio Anunciante), la corrección SOLO
    puede seleccionar uno de ellos — nunca texto libre. Los candidatos se
    recalculan aquí mismo (nunca se confía en lo que mandó el cliente) para
    que la restricción sea real, no solo una ayuda de UI. Sin candidatos, se
    conserva el texto libre de siempre (única forma de que "Corregir" siga
    sirviendo para un paquete sin Apartamento resuelto).

    `candidato_idx == "nuevo"` (.scratch/mis-datos, ticket 09): en vez de
    elegir uno de la lista, el staff declara un Ocupante NUEVO para el
    Apartamento del snapshot (nombre + teléfono opcional) — crea el Ocupante
    (mismos límites que `/mis-datos`: máximo 5 activos, un teléfono un
    apartamento a la vez) y corrige el destinatario a él."""
    paquete = _get_paquete_o_404(db, paquete_id)
    candidatos = candidatos_correccion(db, paquete)

    if candidato_idx == "nuevo":
        apto = buscar_apartamento_por_terna(
            db, paquete.snapshot_conjunto, paquete.snapshot_torre, paquete.snapshot_apartamento
        )
        nombre_nuevo = (nuevo_ocupante_nombre or "").strip()
        if apto is None or not nombre_nuevo:
            return _render_lista(
                request, db, staff,
                error="Escribí el nombre del nuevo ocupante.",
                status_code=400,
                error_paquete_id=str(paquete.id),
            )
        telefono_nuevo = (nuevo_ocupante_telefono or "").strip() or None
        try:
            ocupante = agregar_ocupante(db, apto, nombre_nuevo, telefono_nuevo)
        except ValueError as exc:
            return _render_lista(
                request, db, staff, error=str(exc), status_code=400,
                error_paquete_id=str(paquete.id),
            )
        nombre, telefono = ocupante.nombre, telefono_notificacion_ocupante(db, ocupante)
    elif candidatos:
        try:
            idx = int(candidato_idx)
            candidato = candidatos[idx]
        except (TypeError, ValueError, IndexError):
            # Sin campo que marcar (la selección es un grupo de candidatos,
            # no un input_texto) -- sí se reabre el modal de este paquete
            # para que el toast aparezca con contexto visible.
            return _render_lista(
                request, db, staff,
                error="Seleccioná uno de los nombres de la lista.",
                status_code=400,
                error_paquete_id=str(paquete.id),
            )
        nombre, telefono = candidato["nombre"], candidato["telefono"]
    else:
        nombre, telefono = recipient_name, recipient_phone

    try:
        corregir_destinatario(db, paquete, staff, nombre, telefono)
    except TransicionInvalida as exc:
        # Sin campo ni modal que reabrir con sentido: el estado cambió
        # (ya no está ANUNCIADO) desde que se abrió la página -- el toast
        # ya lo explica.
        return _render_lista(request, db, staff, error=str(exc), status_code=400)
    except ValueError as exc:
        # Único origen posible es recipient_name vacío (ver docstring de
        # corregir_destinatario) -- seguro marcar ese campo siempre.
        return _render_lista(
            request, db, staff, error=str(exc), status_code=400,
            error_paquete_id=str(paquete.id), error_campo="recipient_name",
        )
    return RedirectResponse("/paquetes", status_code=status.HTTP_303_SEE_OTHER)
