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
from sqlalchemy import or_, tuple_
from sqlalchemy.orm import Session, sessionmaker

from app.domain.apartamento import Apartamento
from app.domain.apartamento_service import (
    buscar_apartamento_por_terna,
    listar_catalogo_por_torre,
    resolver_apartamento,
)
from app.domain.contacto import clasificar_contacto
from app.domain.foto_storage import FotoStorage
from app.domain.notification_sender import NotificationSender
from app.domain.notificacion_service import preparar_notificacion
from app.domain.ocupante import Ocupante
from app.domain.ocupante_service import (
    agregar_ocupante,
    mensaje_ya_ocupante_activo,
    mover_ocupante,
    ocupante_activo_por_contacto,
    telefono_notificacion_ocupante,
)
from app.domain.paquete import CondicionPaquete, EstadoPaquete, MotivoCancelacion, Paquete, TipoPaquete
from app.domain.paquete_correccion_service import candidatos_correccion, candidatos_correccion_por_paquetes
from app.domain.paquete_lifecycle import (
    TransicionInvalida,
    cancel,
    corregir_apartamento,
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
from ..security import current_staff, require_admin
from ..templating import templates

router = APIRouter()

# Ganador del prototipo de tabla de /paquetes (conversación 2026-08-13, skill
# `prototype`: "Grid denso" -- ver .scratch/pendientes-cliente si se agrega un
# issue formal). Antes era 20; el rediseño a tabla pidió 10 explícitamente.
_POR_PAGINA = 10


def _notificar_diferido(background_tasks, db, paquete, evento, sender):
    """Resuelve destino+mensaje SÍNCRONO (rápido, solo BD) y difiere el envío
    real a un BackgroundTask -- ver `notificacion_service.preparar_notificacion`
    y `notifications.enviar_en_segundo_plano`. Compartido por
    recibir/entregar/cancelar, las 3 transiciones de este archivo que
    notifican."""
    resultado = preparar_notificacion(db, paquete, evento)
    if resultado is not None:
        background_tasks.add_task(enviar_en_segundo_plano, sender, *resultado)


def _personas_por_id(db: Session, ids: set) -> dict:
    """`{persona_id: Persona}` para todos los `ids` no nulos, en UNA sola
    consulta -- helper batch compartido por `_nombre_no_coincide`/
    `_actor_ultima_accion` (auditoría de rendimiento 2026-08-10, `.scratch/
    pendientes-cliente`: antes cada una buscaba su propia Persona por
    paquete, N+1 clásico bajo la lista completa de una página)."""
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {p.id: p for p in db.query(Persona).filter(Persona.id.in_(ids)).all()}


def _usuarios_por_id(db: Session, ids: set) -> dict:
    """`{usuario_id: Usuario}` para todos los `ids` no nulos, en UNA sola
    consulta -- mismo motivo/patrón que `_personas_por_id`."""
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {u.id: u for u in db.query(Usuario).filter(Usuario.id.in_(ids)).all()}


def _apartamentos_por_terna(db: Session, paquetes: list[Paquete]) -> dict:
    """`{(conjunto, torre, apartamento): Apartamento}` para todos los paquetes
    de la página que ya tienen unidad resuelta, en UNA sola consulta -- mismo
    criterio batch que `_personas_por_id`/`_usuarios_por_id` (columna
    "Ver"/"Dirección", issue 79: evita repetir `buscar_apartamento_por_terna`,
    una consulta por fila, por cada una de las hasta 10 filas de la página)."""
    ternas = {
        (p.snapshot_conjunto, p.snapshot_torre, p.snapshot_apartamento)
        for p in paquetes
        if p.snapshot_conjunto and p.snapshot_torre and p.snapshot_apartamento
    }
    if not ternas:
        return {}
    filas = (
        db.query(Apartamento)
        .filter(
            tuple_(Apartamento.conjunto, Apartamento.torre, Apartamento.apartamento).in_(ternas)
        )
        .all()
    )
    return {(a.conjunto, a.torre, a.apartamento): a for a in filas}


def _ocupantes_por_apartamento_id(db: Session, apartamento_ids: set) -> dict:
    """`{apartamento_id: [Ocupante, ...]}` (solo activos) para todos los
    apartamentos resueltos en la página, en UNA sola consulta -- mismo
    criterio batch de arriba. Usado por el modal "Ver" (residentes de la
    unidad)."""
    apartamento_ids = {i for i in apartamento_ids if i is not None}
    if not apartamento_ids:
        return {}
    ocupantes = (
        db.query(Ocupante)
        .filter(Ocupante.apartamento_id.in_(apartamento_ids), Ocupante.desvinculado_en.is_(None))
        .order_by(Ocupante.es_principal.desc(), Ocupante.nombre)
        .all()
    )
    resultado: dict = {}
    for o in ocupantes:
        resultado.setdefault(o.apartamento_id, []).append(o)
    return resultado


def _fecha_ultima_accion(paquete: Paquete):
    """Fecha del ÚLTIMO cambio de ESTADO (columna "Fecha", issue 79) -- mismo
    orden de prioridad que `_actor_ultima_accion` (cancelado > entregado >
    recibido > anunciado, los dos primeros mutuamente excluyentes por ser
    terminales), pero devolviendo el timestamp en vez del actor: si se
    anunció ayer pero se recibió hoy, esta columna debe mostrar HOY."""
    return (
        paquete.cancelled_at
        or paquete.delivered_at
        or paquete.received_at
        or paquete.announced_at
    )


def _direccion_corta(paquete: Paquete) -> str | None:
    """Formato compacto para la columna "Dirección" (issue 79): "Torre 10 ·
    Apt 101". `snapshot_torre` ya guarda el label completo del catálogo (ej.
    "TORRE 10"), así que se le quita un prefijo "torre" redundante antes de
    anteponer el propio -- si no, quedaría "Torre TORRE 10"."""
    if not paquete.snapshot_apartamento:
        return None
    torre = (paquete.snapshot_torre or "").strip()
    if torre[:5].lower() == "torre":
        torre = torre[5:].strip()
    return (
        f"Torre {torre} · Apt {paquete.snapshot_apartamento}"
        if torre
        else f"Apt {paquete.snapshot_apartamento}"
    )


def _nombre_no_coincide(persona: Persona | None, paquete: Paquete) -> bool:
    """True si el nombre anunciado difiere del nombre YA REGISTRADO del
    Anunciante — calculado al leer (no se guarda), así que si el staff corrige
    el nombre de la Persona la advertencia desaparece sola.

    Recibe la Persona YA resuelta (`_personas_por_id`, batch por página) en
    vez de buscarla ella misma -- ver docstring de `_personas_por_id`."""
    if persona is None or not persona.nombre:
        return False
    return persona.nombre.strip().lower() != (paquete.recipient_name or "").strip().lower()


def _actor_ultima_accion(paquete: Paquete, usuarios: dict, personas: dict) -> str | None:
    """Quién hizo la transición más avanzada que ya ocurrió (Grupo 11, Ronda
    2) — Cancelado y Entregado son mutuamente excluyentes (ambos terminales),
    por eso el orden de prioridad alcanza para desambiguar.

    Recibe `usuarios`/`personas` YA resueltos (batch por página, ver
    `_usuarios_por_id`/`_personas_por_id`) en vez de buscarlos ella misma."""
    for usuario_id in (
        paquete.cancelled_by_usuario_id,
        paquete.delivered_by_usuario_id,
        paquete.received_by_usuario_id,
    ):
        usuario = usuarios.get(usuario_id) if usuario_id is not None else None
        if usuario is not None:
            return usuario.nombre
    usuario_anuncio = (
        usuarios.get(paquete.announced_by_usuario_id)
        if paquete.announced_by_usuario_id is not None
        else None
    )
    if usuario_anuncio is not None:
        return usuario_anuncio.nombre
    persona = personas.get(paquete.announced_by_persona_id)
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

    # Resolución batch (auditoría de rendimiento 2026-08-10, `.scratch/
    # pendientes-cliente`): un puñado FIJO de consultas para la página
    # entera, en vez de varias POR paquete -- el N+1 anterior agotaba el
    # pool de conexiones de la BD bajo navegación/pestañas concurrentes.
    # Modal "Ver" (issue 79): unidad resuelta + sus Ocupantes activos, batch
    # por página (mismo criterio que el resto de esta sección) -- ANTES de
    # resolver `personas`, para poder sumar los `persona_id` de los
    # Ocupantes al mismo lote (evita una segunda consulta a Persona).
    apartamentos_por_terna = _apartamentos_por_terna(db, paquetes)
    apartamento_ids = {a.id for a in apartamentos_por_terna.values()}
    ocupantes_por_apartamento = _ocupantes_por_apartamento_id(db, apartamento_ids)

    persona_ids = {p.announced_by_persona_id for p in paquetes}
    for ocupantes in ocupantes_por_apartamento.values():
        persona_ids.update({o.persona_id for o in ocupantes if o.persona_id})
    usuario_ids = set()
    for p in paquetes:
        usuario_ids.update(
            {
                p.cancelled_by_usuario_id,
                p.delivered_by_usuario_id,
                p.received_by_usuario_id,
                p.announced_by_usuario_id,
            }
        )
    personas = _personas_por_id(db, persona_ids)
    usuarios = _usuarios_por_id(db, usuario_ids)

    anunciados = [p for p in paquetes if p.estado is EstadoPaquete.ANUNCIADO]
    candidatos_por_paquete = (
        candidatos_correccion_por_paquetes(db, anunciados) if anunciados else {}
    )

    for p in paquetes:
        # Atributos transitorios (no persistidos), solo para la plantilla.
        p.advertencia_nombre = _nombre_no_coincide(personas.get(p.announced_by_persona_id), p)
        p.actor_ultima_accion = _actor_ultima_accion(p, usuarios, personas)
        p.candidatos_correccion = candidatos_por_paquete.get(p.id, [])
        p.fecha_ultima_accion = _fecha_ultima_accion(p)
        p.direccion_corta = _direccion_corta(p)
        p.persona_anunciante = personas.get(p.announced_by_persona_id)
        apto = apartamentos_por_terna.get(
            (p.snapshot_conjunto, p.snapshot_torre, p.snapshot_apartamento)
        )
        p.residentes_unidad = [
            {
                "nombre": o.nombre,
                "es_principal": o.es_principal,
                "telefono": (
                    personas[o.persona_id].telefono
                    if o.persona_id and personas.get(o.persona_id)
                    else None
                ),
            }
            for o in ocupantes_por_apartamento.get(apto.id, [])
        ] if apto else []

    return paquetes, pagina, total_paginas


def _peticion_en_vivo(request: Request) -> bool:
    """True si la petición viene del fetch en vivo de la barra de búsqueda
    (`.scratch/paquetes-busqueda-viva`, ticket 03) -- el JS de
    `_busqueda_filtros.html` marca cada petición en segundo plano con este
    header. Distingue esa búsqueda "solo tarjetas+paginación" de la carga
    normal de página (que necesita el layout completo) y de los POST de
    acción (recibir/entregar/cancelar/corregir) que re-renderizan la lista
    en un error -- esos nunca traen el header, así que siguen devolviendo
    la página completa con su toast, sin cambios."""
    return request.headers.get("X-Requested-With") == "fetch"


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
    plantilla = (
        "packages/_resultados.html" if _peticion_en_vivo(request) else "packages/list.html"
    )
    return templates.TemplateResponse(
        plantilla,
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
            # Catálogo de Torre+Apartamento para el paso nuevo de Recibir
            # (.scratch/ocupante-principal-escenarios, ticket 05) -- declarar
            # unidad cuando el destinatario todavía no tiene una.
            "catalogo_torres": listar_catalogo_por_torre(db),
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
    torre: str = Form(None),
    apartamento: str = Form(None),
    candidato_idx: str = Form(None),
    nuevo_ocupante_nombre: str = Form(None),
    nuevo_ocupante_contacto: str = Form(None),
):
    paquete = _get_paquete_o_404(db, paquete_id)
    guia = (guide_number or "").strip() or None
    tipo = TipoPaquete(package_type) if package_type else None
    condicion = CondicionPaquete(package_condition) if package_condition else None

    # Paso nuevo, opcional (.scratch/ocupante-principal-escenarios, ticket
    # 05): declarar la unidad si al destinatario todavía no se le resolvió
    # ninguna, y/o confirmar-elegir-crear a quién exactamente corresponde --
    # mismo mecanismo que Corregir destinatario, reusado acá para que la
    # promoción automática a principal (ticket 04) tenga sobre quién actuar.
    # Ninguno de los dos sub-pasos corre si sus campos vienen vacíos -- sin
    # ellos, Recibir se comporta exactamente igual que siempre.
    torre_v = (torre or "").strip() or None
    apartamento_v = (apartamento or "").strip() or None
    if torre_v and apartamento_v and paquete.snapshot_apartamento is None:
        try:
            apto = resolver_apartamento(db, torre_v, apartamento_v)
            corregir_apartamento(db, paquete, staff, apto)
        except (ValueError, TransicionInvalida) as exc:
            return _render_lista(
                request, db, staff, error=str(exc), status_code=400,
                error_paquete_id=str(paquete.id),
            )

    if candidato_idx or (nuevo_ocupante_nombre or "").strip():
        nombre, telefono = _resolver_desde_candidato(
            db, paquete, candidato_idx, nuevo_ocupante_nombre, nuevo_ocupante_contacto
        )
        if nombre is None:
            return _render_lista(
                request, db, staff, error=telefono, status_code=400,
                error_paquete_id=str(paquete.id),
            )
        try:
            corregir_destinatario(db, paquete, staff, nombre, telefono)
        except TransicionInvalida as exc:
            # Mismo criterio que el ticket 09 (.scratch/ocupante-principal-
            # escenarios): si `_resolver_desde_candidato` ya creó un
            # Ocupante nuevo ("nuevo") antes de que ESTE paso fallara por una
            # carrera real (el paquete cambió de estado desde que se abrió
            # la página), ese Ocupante no debe quedar huérfano.
            db.rollback()
            return _render_lista(request, db, staff, error=str(exc), status_code=400)

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


@router.post("/paquetes/{paquete_id}/eliminar")
def delete_action(
    paquete_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(require_admin),
):
    """Borrado real de la fila (issue 79) -- sin precedente en esta app (todo
    lo demás se anonimiza, nunca se borra, por las llaves foráneas de
    auditoría -- ver `customers_manage.py`). Un Paquete SÍ se puede borrar de
    verdad porque, mientras sigue ANUNCIADO, no tiene fotos ni ninguna otra
    fila que dependa de él (`paquete_fotos` solo se llena al Recibir) -- es
    decir, borrar antes de eso no dejaría huérfanos ni rompería auditoría de
    nada que ya haya pasado. Server-side ES la barrera real (`require_admin`,
    igual que `customers_manage_delete`); la UI (`packages/_acciones.html`)
    es solo una ayuda visual, no la única puerta.

    Guard de estado repetido acá (no solo en la UI): si el paquete avanzó de
    estado entre que se abrió la página y que se confirmó el modal (carrera
    real, mismo caso que las demás acciones de este archivo), rechazar sin
    efecto en vez de borrar una fila que ya tiene historial real.
    """
    paquete = _get_paquete_o_404(db, paquete_id)
    if paquete.estado is not EstadoPaquete.ANUNCIADO:
        return _render_lista(
            request, db, admin,
            error="Solo se puede eliminar un paquete mientras está Anunciado "
            "(nunca se recibió). Para los demás casos, usa Cancelar.",
            status_code=400,
        )
    db.delete(paquete)
    db.commit()
    return RedirectResponse("/paquetes", status_code=status.HTTP_303_SEE_OTHER)


def _resolver_desde_candidato(
    db: Session,
    paquete: Paquete,
    candidato_idx: str,
    nuevo_ocupante_nombre: str,
    nuevo_ocupante_contacto: str,
    permitir_mover: bool = False,
    mover_de_otra_unidad: str = None,
) -> tuple[str, str] | tuple[None, str]:
    """`(nombre, telefono)` resuelto desde los mismos 3 campos que ya usa
    Corregir destinatario (`candidato_idx`/`nuevo_ocupante_*`) -- comparte
    esta lógica `correct_recipient_action` y el paso nuevo de Recibir
    (`receive_action`, `.scratch/ocupante-principal-escenarios` ticket 05),
    para no duplicarla. Sin fallback de texto libre acá (a diferencia de
    Corregir destinatario) -- ese caso no aplica al paso opcional de
    Recibir, que solo se muestra cuando SÍ hay candidatos.

    `nuevo_ocupante_contacto` (ticket 08): input único autoclasificado
    (Teléfono o WhatsApp), mismo criterio que tab Residentes/`/mis-datos`.

    `permitir_mover` (ticket 12): SOLO `True` desde Corregir destinatario --
    "mover" nunca se ofrece dentro de Recibir (el caller no pasa este
    parámetro en ese caso, así que queda `False` por defecto). Si el
    contacto ya es Ocupante activo no-principal de otra unidad, mueve a esa
    persona (con su identidad real) en vez de crear un registro nuevo -- el
    `nuevo_ocupante_nombre` tecleado se ignora en ese caso.

    Returns:
        `(nombre, telefono)` si se resolvió, o `(None, mensaje_de_error)`
        si no.
    """
    candidatos = candidatos_correccion(db, paquete)

    if candidato_idx == "nuevo":
        apto = None
        if paquete.snapshot_conjunto and paquete.snapshot_torre and paquete.snapshot_apartamento:
            apto = buscar_apartamento_por_terna(
                db, paquete.snapshot_conjunto, paquete.snapshot_torre, paquete.snapshot_apartamento
            )
        if apto is None:
            return None, "Este paquete no tiene apartamento resuelto en su snapshot."
        nombre_nuevo = (nuevo_ocupante_nombre or "").strip()
        if not nombre_nuevo:
            return None, "Escribí el nombre del nuevo ocupante."

        contacto_v = (nuevo_ocupante_contacto or "").strip()
        kwargs_contacto = {}
        if contacto_v:
            tipo_contacto = clasificar_contacto(contacto_v)
            if tipo_contacto == "telefono":
                kwargs_contacto["telefono"] = contacto_v
            elif tipo_contacto == "whatsapp":
                kwargs_contacto["whatsapp_usuario"] = contacto_v
            else:
                return None, (
                    "Ese contacto no parece un Teléfono ni un usuario de "
                    "WhatsApp válido -- revísalo, o déjalo vacío."
                )

        conflicto = (
            ocupante_activo_por_contacto(db, **kwargs_contacto)
            if permitir_mover and kwargs_contacto
            else None
        )
        moviendo = conflicto is not None and conflicto.apartamento_id != apto.id
        if moviendo and (conflicto.es_principal or not mover_de_otra_unidad):
            return None, mensaje_ya_ocupante_activo(db, conflicto)

        try:
            if moviendo:
                ocupante = mover_ocupante(db, conflicto, apto)
            else:
                ocupante = agregar_ocupante(db, apto, nombre_nuevo, **kwargs_contacto)
        except ValueError as exc:
            return None, str(exc)
        return ocupante.nombre, telefono_notificacion_ocupante(db, ocupante)

    if candidatos:
        try:
            idx = int(candidato_idx)
            candidato = candidatos[idx]
        except (TypeError, ValueError, IndexError):
            return None, "Seleccioná uno de los nombres de la lista."
        return candidato["nombre"], candidato["telefono"]

    return None, "No hay candidatos para elegir."


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
    nuevo_ocupante_contacto: str = Form(None),
    mover_de_otra_unidad: str = Form(None),
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

    if candidato_idx == "nuevo" or candidatos:
        # Sin campo que marcar en el error de selección (es un grupo de
        # candidatos, no un input_texto) -- sí se reabre el modal de este
        # paquete para que el toast aparezca con contexto visible.
        nombre, telefono = _resolver_desde_candidato(
            db, paquete, candidato_idx, nuevo_ocupante_nombre, nuevo_ocupante_contacto,
            permitir_mover=True, mover_de_otra_unidad=mover_de_otra_unidad,
        )
        if nombre is None:
            return _render_lista(
                request, db, staff, error=telefono, status_code=400,
                error_paquete_id=str(paquete.id),
            )
    else:
        nombre, telefono = recipient_name, recipient_phone

    try:
        corregir_destinatario(db, paquete, staff, nombre, telefono)
    except TransicionInvalida as exc:
        # Integridad transaccional (.scratch/ocupante-principal-escenarios,
        # ticket 09): si `_resolver_desde_candidato` ya creó un Ocupante
        # nuevo ("nuevo") antes de que ESTA carrera real ocurriera (el
        # paquete cambió de estado desde que se abrió la página), ese
        # Ocupante no debe quedar huérfano -- `get_db` comitearía igual sin
        # este rollback (commit al éxito / rollback SOLO si se lanza una
        # excepción hasta la capa de arriba).
        db.rollback()
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
