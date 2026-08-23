# -*- coding: utf-8 -*-
"""
Vista de staff `/paquetes` — lista + acciones del ciclo de vida.

Protegida por `current_staff`: el `Usuario` de la sesión es el **actor** de cada
transición (recibir/entregar/cancelar), nunca un id enviado por el cliente. Las
acciones exitosas redirigen a `/paquetes` (PRG); las transiciones inválidas
re-muestran la lista con un aviso, sin efecto.
"""

import re
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

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
from sqlalchemy import func, or_, tuple_
from sqlalchemy.exc import IntegrityError
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
    cambios_recientes_de_apartamento,
    identificar_contacto_para_unidad,
    listar_ocupantes,
    mensaje_ya_ocupante_activo,
    mover_ocupante,
    ocupante_activo_por_contacto,
    promover_a_principal,
    residentes_por_torre_apartamento,
    telefono_notificacion_ocupante,
)
from app.domain.paquete import (
    CondicionPaquete,
    EstadoPaquete,
    MotivoCancelacion,
    Paquete,
    TipoPaquete,
    torre_sin_prefijo,
)
from app.domain.paquete_correccion_service import candidatos_correccion, candidatos_correccion_por_paquetes
from app.domain.paquete_lifecycle import (
    ESTADOS_CORREGIBLES,
    TransicionInvalida,
    cancel,
    corregir_apartamento,
    corregir_destinatario,
    deliver,
    receive,
)
from app.domain.paquete_timeline_service import timelines_de_paquetes
from app.domain.persona import Persona
from app.domain.persona_service import (
    url_llamada,
    url_whatsapp,
)
from app.domain.usuario import Usuario

from ..db import get_db, get_session_factory
from ..fotos import get_foto_storage, subir_fotos_diferido
from ..notifications import enviar_en_segundo_plano, get_notification_sender
from ..security import current_staff, require_admin
from ..templating import templates

router = APIRouter()

# De 20 a 10 el 2026-08-13 (skill `prototype`, ganador "Grid denso"); de vuelta
# a 20 el 2026-08-20, pedido explícito -- ver .scratch/pendientes-cliente.
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


def _personas_por_telefono(db: Session, telefonos: set) -> dict:
    """`{telefono: Persona}` -- para enriquecer el link de WhatsApp del
    DESTINATARIO con su username real (conversación 2026-08-17, pedido
    explícito), sin tocar el snapshot congelado del Paquete (ADR-0001):
    `recipient_phone` no tiene FK a una Persona (a propósito, ver
    `paquete_lifecycle.py`), así que la única forma de saber si detrás
    hay alguien con `whatsapp_usuario` es buscar por teléfono -- la llave
    universal de la Persona (ADR-0003). Mismo criterio batch que
    `_personas_por_id`, `telefono` en vez de `id`."""
    telefonos = {t for t in telefonos if t}
    if not telefonos:
        return {}
    return {p.telefono: p for p in db.query(Persona).filter(Persona.telefono.in_(telefonos)).all()}


def _personas_por_nombre(db: Session, nombres: set) -> dict:
    """`{nombre: Persona}` -- fallback de `_personas_por_telefono` para
    cuando el destinatario NO TIENE NINGÚN teléfono en el snapshot
    (`recipient_phone IS NULL`) -- caso real (bug reportado en vivo,
    conversación 2026-08-17, ejemplo "CAMILA OSPINA"): una Persona
    solo-WhatsApp (ADR-0007, sin Teléfono propio) como destinatario deja
    `recipient_phone` vacío a propósito (`telefono_notificacion_ocupante`
    NUNCA mete un username de WhatsApp ahí -- esa columna la leen SMS/OTP
    como Teléfono real) -- sin teléfono que buscar, no hay forma de
    recuperar a esa Persona salvo por nombre.

    Confiable en la práctica en ESTE dominio (no en general): `agregar_
    ocupante` (issue 97) fuerza que el nombre de cualquier Ocupante
    coincida con el de su Persona real cuando el contacto ya existe, y
    "Corregir destinatario" copia el nombre EXACTO del candidato elegido
    -- así que `recipient_name` == `Persona.nombre` es el caso normal, no
    la excepción. Riesgo aceptado y no resuelto acá: dos Personas
    distintas con el mismo nombre completo registrado resolverían a la
    última que devuelva la consulta -- caso borde, no la norma (nombres
    completos, no apodos)."""
    nombres = {n for n in nombres if n}
    if not nombres:
        return {}
    return {p.nombre: p for p in db.query(Persona).filter(Persona.nombre.in_(nombres)).all()}


def _whatsapp_url_destinatario(paquete: Paquete, persona: Persona | None) -> str | None:
    """URL de WhatsApp para el destinatario de `paquete` (conversación
    2026-08-17, pedido explícito: "el ícono de WhatsApp... debería estar
    enfocado al nombre de usuario de whatsapp antes que el número de
    teléfono, en caso que no tenga usuario de whatsapp, entonces se
    debería usar el número") -- mismo criterio de prioridad que
    `persona_service.url_whatsapp` (username > teléfono), reusada acá
    cuando SÍ se resuelve una Persona real detrás del teléfono del
    snapshot. Sin Persona resuelta (ej. `declarado_por_cliente` sin match
    de ningún co-residente, nunca llegó a tener una Persona propia), cae
    al teléfono crudo del snapshot.

    Bug real reportado en vivo (conversación 2026-08-17, ejemplo "6Y5U"):
    un destinatario SOLO-NOMBRE (`Destinatario.solo_nombre`) sin Persona
    resuelta Y sin `recipient_phone` (ADR-0007) dejaba esto en `None` --
    ícono siempre apagado, aunque el Anunciante SÍ tenga cómo contactarlo
    (`announce()` exige que tenga teléfono o WhatsApp). Último fallback:
    el WhatsApp del Anunciante (mismo criterio que ya usa el ícono de
    Email de `_acciones.html`, que SIEMPRE cae a `persona_anunciante` por
    no tener campo propio de destinatario)."""
    if persona is not None:
        return url_whatsapp(persona)
    if paquete.recipient_phone:
        return f"https://wa.me/{paquete.recipient_phone.lstrip('+')}"
    # `persona_anunciante` es transitorio (asignado en `_listar`, no una
    # relación real del modelo) -- `getattr` con default evita un
    # `AttributeError` si algún día se llama esto sobre un `Paquete` que
    # no pasó por ese enriquecimiento.
    anunciante = getattr(paquete, "persona_anunciante", None)
    if anunciante and (anunciante.whatsapp_usuario or anunciante.telefono):
        return url_whatsapp(anunciante)
    return None


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


def _duracion_transcurrida(paquete: Paquete) -> str | None:
    """Duración real (días + horas) entre que el paquete se recibió y se
    entregó o canceló -- conversación 2026-08-17, pedido explícito inicial:
    "al lado del estado actual... la cantidad de dias que duro el paquete
    desde el dia que se recibio hasta que se entrego o se cancelo, en su
    defecto si no se ha entregado deberia ir contando cada dia desde que
    se recibio"; refinado el mismo día: "quiero que tambien incluya las
    horas, por ejemplo '3 dias y 4 horas' o '16 horas'".

    Duración REAL en segundos, no días de calendario -- la primera versión
    truncaba a `hora_local(...).date()` (cruzar medianoche contaba como 1
    día aunque hubieran pasado pocas horas), pero con horas en el texto esa
    aproximación deja de ser coherente: "3 días y 4 horas" solo tiene
    sentido si son exactamente 3*24+4 horas, no 3 cruces de medianoche.

    `None` si el paquete nunca se recibió (`received_at` vacío) -- ANUNCIADO
    sin recibir, o CANCELADO directo desde ahí sin pasar por RECIBIDO: no
    hay "momento en que se recibió" del que contar, la plantilla omite el
    chip."""
    if paquete.received_at is None:
        return None
    fin = paquete.delivered_at or paquete.cancelled_at or datetime.now(timezone.utc)
    total_horas = int((fin - paquete.received_at).total_seconds() // 3600)
    dias, horas = divmod(total_horas, 24)
    partes = []
    if dias:
        partes.append(f"{dias} día" + ("" if dias == 1 else "s"))
    if horas or not dias:
        partes.append(f"{horas} hora" + ("" if horas == 1 else "s"))
    return " y ".join(partes)


def _direccion_corta(paquete: Paquete) -> str | None:
    """Formato compacto para la columna "Dirección" (issue 79): "Torre 10 ·
    Apt 101". Ver `torre_sin_prefijo` (domain/paquete.py) para el porqué del
    saneo del prefijo "TORRE" redundante."""
    if not paquete.snapshot_apartamento:
        return None
    torre = torre_sin_prefijo(paquete.snapshot_torre)
    return (
        f"Torre {torre} · Apt {paquete.snapshot_apartamento}"
        if torre
        else f"Apt {paquete.snapshot_apartamento}"
    )


def _nombre_no_coincide(persona: Persona | None, paquete: Paquete) -> bool:
    """True si el nombre anunciado difiere del nombre YA REGISTRADO del
    Anunciante Y el paquete todavía no pasó por "Corregir destinatario"
    (`corrected_at`) -- calculado al leer (no se guarda).

    Ampliado (conversación 2026-08-17, pedido explícito -- "Opción A"):
    antes, la advertencia se apagaba SOLO si el nombre corregido pasaba a
    coincidir exactamente con el Anunciante -- confuso para el staff, que
    corregía a propósito a una persona DISTINTA (un co-residente, alguien
    nuevo) y veía el ícono seguir ahí como si nada hubiera pasado. Ahora
    "Corregir destinatario" (cualquiera de sus 3 entradas: advertencia,
    "Modificar", o el botón del modal "Ver") apaga la advertencia para
    SIEMPRE, sin importar a quién se haya corregido -- para volver a
    corregir después, "Modificar" en Acciones sigue disponible sin
    condición (no depende de que la advertencia esté prendida).

    Nota (`corrected_at` es compartido con `corregir_apartamento`, ver
    ADR-0001 -- "el esquema no distingue cuál de las dos correcciones
    ocurrió"): si un paquete sin Apartamento Y con nombre desajustado se
    corrige SOLO de Apartamento (vía "Asignar apartamento", sin tocar el
    destinatario), la advertencia de nombre también se apaga como efecto
    colateral -- caso borde, no reportado, se documenta acá para el
    próximo que lo encuentre.

    Recibe la Persona YA resuelta (`_personas_por_id`, batch por página) en
    vez de buscarla ella misma -- ver docstring de `_personas_por_id`."""
    if paquete.corrected_at is not None:
        return False
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
    coincidencia PARCIAL: código de acceso, guía, nombre del destinatario,
    nombre/email/usuario de WhatsApp del Anunciante (requiere join a Persona),
    teléfono (anunciante o destinatario, también parcial -- ver más abajo),
    Torre y Apartamento del snapshot -- un solo campo para "cualquier dato que
    el staff recuerde" en vez de cajas separadas por criterio
    (.scratch/paquetes-busqueda-viva).

    Teléfono parcial (pedido 2026-08-20): antes exigía el número COMPLETO y
    válido (`normalizar_telefono(q)` sin excepción); ahora, si `q` contiene
    al menos 4 dígitos, esos dígitos se buscan como substring dentro del
    teléfono guardado (`+573001234567`) -- alcanza con "los últimos 4
    dígitos", con o sin formato (espacios/guiones/+ se ignoran, se comparan
    solo los dígitos). El piso de 4 evita que un texto como "torre 5" (un
    solo dígito) dispare falsos positivos contra prácticamente cualquier
    teléfono."""
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
            Persona.email.ilike(patron),
            Persona.whatsapp_usuario.ilike(patron),
            Paquete.snapshot_torre.ilike(patron),
            Paquete.snapshot_apartamento.ilike(patron),
        ]
        digitos = re.sub(r"\D", "", q)
        if len(digitos) >= 4:
            patron_telefono = f"%{digitos}%"
            condiciones.append(Paquete.announced_by_phone.ilike(patron_telefono))
            condiciones.append(Paquete.recipient_phone.ilike(patron_telefono))
        query = query.filter(or_(*condiciones))

    total = query.count()
    total_paginas = max(1, -(-total // _POR_PAGINA))  # ceil sin importar float
    pagina = max(1, min(pagina, total_paginas))

    # Orden por ÚLTIMO cambio de estado, no por fecha de anuncio
    # (conversación 2026-08-17, pedido explícito) -- mismo orden de
    # prioridad que `_fecha_ultima_accion` (cancelado > entregado >
    # recibido > anunciado), resuelto acá en SQL (no en Python) para que
    # el OFFSET/LIMIT de la paginación, ya a nivel de consulta, corte en
    # el lugar correcto.
    ultimo_cambio = func.coalesce(
        Paquete.cancelled_at, Paquete.delivered_at, Paquete.received_at, Paquete.announced_at
    )
    paquetes = (
        query.order_by(ultimo_cambio.desc())
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
    personas_por_telefono_destinatario = _personas_por_telefono(
        db, {p.recipient_phone for p in paquetes}
    )
    # Fallback por nombre (conversación 2026-08-17, bug reportado en vivo,
    # ejemplo "CAMILA OSPINA"): SOLO para paquetes sin ningún teléfono en
    # el snapshot -- ver docstring de `_personas_por_nombre`.
    personas_por_nombre_destinatario = _personas_por_nombre(
        db, {p.recipient_name for p in paquetes if not p.recipient_phone}
    )

    # `ESTADOS_CORREGIBLES` (paquete_lifecycle.py) es la misma lista que usa
    # el guard real de `corregir_destinatario` -- se reusa acá para no
    # precargar candidatos que el modal no podría guardar de todos modos
    # (ej. CANCELADO).
    corregibles = [p for p in paquetes if p.estado in ESTADOS_CORREGIBLES]
    candidatos_por_paquete = (
        candidatos_correccion_por_paquetes(db, corregibles) if corregibles else {}
    )
    timelines = timelines_de_paquetes(db, paquetes)

    for p in paquetes:
        # Atributos transitorios (no persistidos), solo para la plantilla.
        p.advertencia_nombre = _nombre_no_coincide(personas.get(p.announced_by_persona_id), p)
        p.actor_ultima_accion = _actor_ultima_accion(p, usuarios, personas)
        p.candidatos_correccion = candidatos_por_paquete.get(p.id, [])
        p.fecha_ultima_accion = _fecha_ultima_accion(p)
        p.duracion_transcurrida = _duracion_transcurrida(p)
        p.direccion_corta = _direccion_corta(p)
        p.timeline = timelines.get(p.id, [])
        p.persona_anunciante = personas.get(p.announced_by_persona_id)
        persona_destino = personas_por_telefono_destinatario.get(p.recipient_phone)
        if persona_destino is None and not p.recipient_phone:
            persona_destino = personas_por_nombre_destinatario.get(p.recipient_name)
        p.whatsapp_url_destinatario = _whatsapp_url_destinatario(p, persona_destino)
        # Título del modal "Ver" (conversación 2026-08-21, pedido explícito):
        # el nombre enlaza a su ficha de /residentes cuando SÍ se resolvió
        # una Persona real detrás del destinatario (mismo `persona_destino`
        # ya resuelto arriba para el WhatsApp -- ninguna consulta nueva).
        # `None` cuando no hay match (ej. `declarado_por_cliente` sin
        # ningún co-residente que coincida) -- ahí el nombre se queda como
        # texto plano, no hay a dónde enlazarlo.
        p.persona_destino_id = persona_destino.id if persona_destino else None
        apto = apartamentos_por_terna.get(
            (p.snapshot_conjunto, p.snapshot_torre, p.snapshot_apartamento)
        )
        p.residentes_unidad = [
            {
                "nombre": o.nombre,
                "es_principal": o.es_principal,
                # Persona completa (no solo `telefono` suelto) para que la
                # plantilla pueda usar `url_llamada`/`url_whatsapp` -- mismo
                # criterio que `p.persona_anunciante` (issue 79).
                "persona": personas.get(o.persona_id) if o.persona_id else None,
            }
            for o in ocupantes_por_apartamento.get(apto.id, [])
        ] if apto else []

    # Ícono "cambio reciente de apartamento" (issue 165, .scratch/pendientes-
    # cliente) -- SEGUNDO loop porque `persona_destino_id` recién se resuelve
    # arriba, dentro del loop principal (no se conoce de antemano el set
    # completo hasta que termina). Batch, no una consulta por fila.
    cambios_recientes = cambios_recientes_de_apartamento(
        db, {p.persona_destino_id for p in paquetes if p.persona_destino_id}
    )
    for p in paquetes:
        p.cambio_reciente_apartamento = (
            cambios_recientes.get(p.persona_destino_id) if p.persona_destino_id else None
        )

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


def _conteos_pendientes(db: Session) -> dict:
    """Total de paquetes en ANUNCIADO y en RECIBIDO -- GLOBAL, sin filtrar
    por la búsqueda/estado activo (retroalimentación en vivo 2026-08-18,
    issue 126): indicador operativo de trabajo pendiente para los badges
    de `filtro_estado()`, no un recuento de lo que se ve en pantalla. Una
    sola consulta agrupada, no dos `count()` sueltos."""
    filas = (
        db.query(Paquete.estado, func.count(Paquete.id))
        .filter(Paquete.estado.in_([EstadoPaquete.ANUNCIADO, EstadoPaquete.RECIBIDO]))
        .group_by(Paquete.estado)
        .all()
    )
    por_estado = {estado.value: total for estado, total in filas}
    return {"ANUNCIADO": por_estado.get("ANUNCIADO", 0), "RECIBIDO": por_estado.get("RECIBIDO", 0)}


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
    ver_paquete_id=None,
    corregir_paquete_id=None,
    recibir_paquete_id=None,
    entregar_paquete_id=None,
    recontactar_valor=None,
):
    paquetes, pagina_actual, total_paginas = _listar(db, estado=estado, q=q, pagina=pagina)
    en_vivo = _peticion_en_vivo(request)
    plantilla = "packages/_resultados.html" if en_vivo else "packages/list.html"
    # La barra de filtros (con los badges) vive FUERA de `_resultados.html`
    # -- no hace falta recalcular esto en cada fetch de búsqueda en vivo,
    # que solo reemplaza el fragmento de resultados.
    conteos_estado = _conteos_pendientes(db) if not en_vivo else None
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
            # Badges de conteo (Anunciado/Recibido) sobre los íconos de
            # filtro (issue 126) -- `None` en peticiones de búsqueda en
            # vivo, ver `conteos_estado` más arriba.
            "conteos_estado": conteos_estado,
            # Catálogo de Torre+Apartamento para el paso nuevo de Recibir
            # (.scratch/ocupante-principal-escenarios, ticket 05) -- declarar
            # unidad cuando el destinatario todavía no tiene una.
            "catalogo_torres": listar_catalogo_por_torre(db),
            # Residentes ACTIVOS por unidad, para el buscador de "Asignar
            # apartamento" (issue 85) -- antes de asociar, el staff ve si
            # la unidad está libre o ya tiene residentes (y cuáles), para
            # no mezclar por error a alguien con la familia equivocada.
            "residentes_por_unidad": residentes_por_torre_apartamento(db),
            # Identifica CUÁL paquete/modal tenía el error, para reabrirlo
            # y marcar su campo específico (retroalimentación en vivo
            # 2026-08-02) -- solo aplica hoy al modal "Corregir" (el único
            # con inputs de texto reales; los demás usan chips sin estado
            # de error propio, o no tienen ningún input de texto).
            "error_paquete_id": error_paquete_id,
            "error_campo": error_campo,
            # Reabre el modal "Ver" tras el redirect de una corrección
            # exitosa disparada desde SU PROPIO botón "Corregir destinatario"
            # (conversación 2026-08-16, pedido explícito) -- mismo patrón que
            # `error_paquete_id`, pero para el camino de éxito en vez de
            # error, y para el modal "Ver" en vez de "Corregir".
            "ver_paquete_id": ver_paquete_id,
            # Reabre el modal "Corregir destinatario" (no "Ver") tras
            # promover a otro Residente como principal desde "+ Nuevo
            # residente" (conversación 2026-08-17, pedido explícito) --
            # mismo patrón que `ver_paquete_id`, apuntando al modal del que
            # salió el staff. `recontactar_valor`, si viene, es el contacto
            # que el staff ya había tecleado antes de promover -- el JS lo
            # vuelve a escribir y dispara la vista previa sola, así el
            # "Mudar residente" (ya no bloqueado, el conflicto era ser
            # principal) aparece sin que el staff tenga que retipear nada.
            "corregir_paquete_id": corregir_paquete_id,
            # Mismo patrón que `corregir_paquete_id`, apuntando al modal
            # "Recibir" en vez de "Corregir destinatario" (conversación
            # 2026-08-17, pedido explícito: portar la misma vista previa
            # de "+ Nuevo residente" -- con su propio "Degradarlo" -- a
            # Recibir también).
            "recibir_paquete_id": recibir_paquete_id,
            # Reabre el modal "Entregar" (issue 164, .scratch/pendientes-
            # cliente) -- mismo patrón que `recibir_paquete_id`, para el
            # botón "Entregar" que ahora también aparece en /announce al
            # identificar a un residente con paquetes RECIBIDO en curso.
            "entregar_paquete_id": entregar_paquete_id,
            "recontactar_valor": recontactar_valor,
            # Links tel:/wa.me para el modal "Ver" (issue 79 -- Teléfono/
            # WhatsApp de la Persona Anunciante clicables). Mismo patrón que
            # `customers_manage.py` (que ya expone estas 2 funciones así, no
            # como globals de Jinja).
            "url_whatsapp": url_whatsapp,
            "url_llamada": url_llamada,
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
    ver: str = None,
    corregir: str = None,
    recibir: str = None,
    entregar: str = None,
    recontactar: str = None,
):
    return _render_lista(
        request, db, staff, estado=estado, q=q, pagina=pagina,
        ver_paquete_id=ver, corregir_paquete_id=corregir, recibir_paquete_id=recibir,
        entregar_paquete_id=entregar, recontactar_valor=recontactar,
    )


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
    mover_de_otra_unidad: str = Form(None),
    origen: str = Form(None),
    q: str = Form(None),
):
    paquete = _get_paquete_o_404(db, paquete_id)
    guia = (guide_number or "").strip() or None
    tipo = TipoPaquete(package_type) if package_type else None
    condicion = CondicionPaquete(package_condition) if package_condition else None
    # `origen="consultar"` (issue 171, mismo mecanismo que ya tiene
    # `deliver_action`): el botón "Recibir" de /consultar reusa este mismo
    # endpoint -- vuelve a esa vista (con la misma búsqueda) en vez de al
    # listado de staff, tanto si funciona como si no.
    destino = f"/consultar?q={quote(q)}" if origen == "consultar" and q else "/paquetes"

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
            if destino != "/paquetes":
                return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)
            return _render_lista(
                request, db, staff, error=str(exc), status_code=400,
                error_paquete_id=str(paquete.id),
            )

    if candidato_idx or (nuevo_ocupante_nombre or "").strip():
        # `permitir_mover=True` (conversación 2026-08-17, pedido explícito):
        # antes Recibir bloqueaba en seco con el mensaje genérico de
        # `agregar_ocupante` ("debe darse de baja antes de asociarse de
        # nuevo") -- mismo mecanismo que ya tenía Corregir destinatario
        # (ticket 12), nada nuevo, solo conectado acá también.
        nombre, telefono = _resolver_desde_candidato(
            db, paquete, candidato_idx, nuevo_ocupante_nombre, nuevo_ocupante_contacto,
            permitir_mover=True, mover_de_otra_unidad=mover_de_otra_unidad,
        )
        if nombre is None:
            if destino != "/paquetes":
                return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)
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
            if destino != "/paquetes":
                return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)
            return _render_lista(request, db, staff, error=str(exc), status_code=400)

    try:
        receive(db, paquete, staff, guia, package_type=tipo, package_condition=condicion)
    except TransicionInvalida as exc:
        if destino != "/paquetes":
            return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)
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
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/paquetes/{paquete_id}/entregar")
def deliver_action(
    paquete_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    sender: NotificationSender = Depends(get_notification_sender),
    origen: str = Form(None),
    q: str = Form(None),
):
    paquete = _get_paquete_o_404(db, paquete_id)
    # `origen="consultar"` (issue 124): el botón "Entregar" de /consultar
    # reusa este mismo endpoint -- vuelve a esa vista (con la misma
    # búsqueda) en vez de al listado de staff, tanto si funciona como si
    # no (la vista simplemente refleja el estado real del paquete).
    destino = f"/consultar?q={quote(q)}" if origen == "consultar" and q else "/paquetes"
    try:
        deliver(db, paquete, staff)
    except TransicionInvalida as exc:
        if destino != "/paquetes":
            return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)
        return _render_lista(request, db, staff, error=str(exc), status_code=400)
    _notificar_diferido(background_tasks, db, paquete, EstadoPaquete.ENTREGADO, sender)
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/paquetes/{paquete_id}/cancelar")
def cancel_action(
    paquete_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    sender: NotificationSender = Depends(get_notification_sender),
    motivo: str = Form(None),
    motivo_otro: str = Form(None),
):
    paquete = _get_paquete_o_404(db, paquete_id)
    # "Otro" + texto libre (conversación 2026-08-17, pedido explícito): la
    # causa REAL tecleada queda en `cancel_reason`, no el literal "OTRO" --
    # si se marcó Otro pero se dejó vacío, sigue cancelando con "OTRO" (no
    # bloquea la cancelación por faltar el detalle).
    if motivo == "OTRO" and motivo_otro and motivo_otro.strip():
        motivo = motivo_otro.strip()
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


@router.post("/paquetes/{paquete_id}/asignar-apartamento")
def assign_apartment_action(
    paquete_id: str,
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    torre: str = Form(None),
    apartamento: str = Form(None),
    nuevo_ocupante_nombre: str = Form(None),
    nuevo_ocupante_contacto: str = Form(None),
    mover_de_otra_unidad: str = Form(None),
):
    """Asigna Torre+Apartamento a un Paquete sin unidad, en ANUNCIADO o
    RECIBIDO (columna "Dirección" de /paquetes, conversación 2026-08-14;
    ampliado a RECIBIDO 2026-08-19, pedido explícito) -- reusa
    `corregir_apartamento` (ya existente, excepción acotada a ADR-0001, ver
    su docstring: pensada originalmente para "Paquete huérfano" cuyo
    Teléfono se vincula a una unidad después de anunciado). Antes solo era
    alcanzable como paso OPCIONAL dentro de Recibir -- este ícono/modal es
    una entrada independiente para hacerlo sin recibir el paquete a la vez.

    `nuevo_ocupante_nombre`/`nuevo_ocupante_contacto` (issue 149, mismo caso
    real que issue 148 en Recibir): asignar SOLO la unidad acá nunca crea
    ningún Ocupante -- es una corrección del snapshot del Paquete, no del
    padrón de residentes (`corregir_apartamento` nunca tocó `Ocupante`, a
    propósito, ver su docstring). Antes de esto, registrar a alguien como
    residente de la unidad recién asignada exigía una segunda visita a
    "Corregir destinatario". Opcional: campos vacíos dejan esta acción
    igual que siempre (solo la dirección). Sin `candidato_idx` expuesto en
    el HTML -- a diferencia de Recibir, este modal nunca mostró candidatos
    numerados pre-declaración, así que no hay ningún índice que pueda
    desalinearse; "nuevo" se pasa fijo server-side en cuanto el nombre
    viene lleno.

    Mismo guard server-side que el resto del archivo: si el paquete ya no
    está en `ESTADOS_CORREGIBLES` (carrera real, o ya Entregado/Cancelado)
    o la terna no existe en el catálogo, rechaza sin efecto en vez de dejar
    el paquete a medio corregir.
    """
    paquete = _get_paquete_o_404(db, paquete_id)
    torre_v = (torre or "").strip()
    apartamento_v = (apartamento or "").strip()
    if not torre_v or not apartamento_v:
        return _render_lista(
            request, db, staff, error="Torre y Apartamento son obligatorios.", status_code=400,
        )
    try:
        apto = resolver_apartamento(db, torre_v, apartamento_v)
        corregir_apartamento(db, paquete, staff, apto)
    except (ValueError, TransicionInvalida) as exc:
        return _render_lista(request, db, staff, error=str(exc), status_code=400)

    nombre_nuevo_v = (nuevo_ocupante_nombre or "").strip()
    if nombre_nuevo_v:
        nombre, telefono = _resolver_desde_candidato(
            db, paquete, "nuevo", nuevo_ocupante_nombre, nuevo_ocupante_contacto,
            permitir_mover=True, mover_de_otra_unidad=mover_de_otra_unidad,
        )
        if nombre is None:
            return _render_lista(request, db, staff, error=telefono, status_code=400)
        try:
            corregir_destinatario(db, paquete, staff, nombre, telefono)
        except TransicionInvalida as exc:
            # Mismo criterio que ticket 09 (.scratch/ocupante-principal-
            # escenarios): si `_resolver_desde_candidato` ya creó un
            # Ocupante nuevo antes de que ESTE paso fallara por una carrera
            # real, ese Ocupante no debe quedar huérfano.
            db.rollback()
            return _render_lista(request, db, staff, error=str(exc), status_code=400)

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

    `permitir_mover` (ticket 12): `True` desde los 3 callers actuales
    (Recibir, Asignar apartamento, Corregir destinatario -- issues 148/149
    lo extendieron a los 2 primeros, que originalmente no lo tenían). Si el
    contacto ya es Ocupante activo de otra unidad, mueve a esa persona (con
    su identidad real) en vez de crear un registro nuevo -- el
    `nuevo_ocupante_nombre` tecleado se ignora en ese caso. Issue 159
    (.scratch/pendientes-cliente): incluye Principal -- `mover_ocupante`
    degrada automáticamente si hace falta.

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
        # Issue 159 (.scratch/pendientes-cliente): un Principal ya no
        # bloquea acá -- `mover_ocupante` degrada automáticamente si hace
        # falta (ver su docstring).
        if moviendo and not mover_de_otra_unidad:
            return None, mensaje_ya_ocupante_activo(db, conflicto)

        try:
            if moviendo:
                ocupante = mover_ocupante(db, conflicto, apto)
            else:
                ocupante = agregar_ocupante(db, apto, nombre_nuevo, **kwargs_contacto)
        except ValueError as exc:
            # Integridad transaccional (mismo criterio que ticket 09,
            # .scratch/ocupante-principal-escenarios): si `mover_ocupante`
            # ya promovió/degradó o dio de baja algo antes de fallar en un
            # paso posterior (ej. destino lleno), ninguno de los 3 callers
            # de esta función hace rollback por su cuenta -- sin este acá,
            # ese cambio parcial quedaría comiteado igual al cerrar el
            # request (`get_db` comitea salvo excepción sin capturar).
            db.rollback()
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


@router.get("/paquetes/{paquete_id}/nuevo-residente/identificar")
def nuevo_residente_identificar(
    paquete_id: str,
    contacto: str = "",
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    """Lookup en vivo para el campo "Teléfono o WhatsApp" del sub-form
    "+ Nuevo residente" de "Corregir destinatario" (conversación 2026-08-16,
    pedido explícito) -- mientras el staff escribe, avisa si el contacto YA
    es una Persona registrada, para que no pueda renombrarla sin querer
    (`agregar_ocupante` ya lo impide server-side de todos modos; esto es
    la vista previa en vivo, no el enforcement real).

    Escopado a `paquete_id` (conversación 2026-08-17, pedido explícito):
    además de si la Persona ya existe, ahora informa si ya es Ocupante
    ACTIVO de OTRA unidad (`conflicto`, `None` si no aplica) -- distinta de
    la de este paquete -- y si ahí es principal, para que el JS decida
    cuál de las 3 UI mostrar: nada (sin conflicto), "Mudar residente a
    <esta unidad>" (conflicto no-principal), o el aviso + link a
    `/residentes` (conflicto principal, `mover_ocupante` nunca lo mueve
    directo). Mismos criterios que el POST real de este mismo form
    (`_resolver_desde_candidato`) -- vista previa, no una regla nueva.

    No devuelve HTML (a diferencia de `/announce/identificar`): lo único
    que el JS necesita es texto/atributos para setear en inputs ya
    existentes -- JSON es más simple acá que parsear un fragmento.

    Delgado a propósito (issue 154, .scratch/pendientes-cliente): la lógica
    real vive en `ocupante_service.identificar_contacto_para_unidad`,
    compartida con el "+ Agregar residente" de `/residentes` tab
    Residentes -- acá solo se resuelve `apto_actual` a partir del snapshot
    de ESTE Paquete (`/residentes` lo resuelve distinto, desde
    `Persona.apartamento_actual_id`). Ver esa función para el contrato
    exacto del dict devuelto."""
    paquete = _get_paquete_o_404(db, paquete_id)
    apto_actual = None
    if paquete.snapshot_conjunto and paquete.snapshot_torre and paquete.snapshot_apartamento:
        apto_actual = buscar_apartamento_por_terna(
            db, paquete.snapshot_conjunto, paquete.snapshot_torre, paquete.snapshot_apartamento
        )
    return identificar_contacto_para_unidad(db, contacto, apto_actual)


@router.get("/paquetes/promover-candidatos")
def promover_candidatos(
    torre: str = "",
    apartamento: str = "",
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
):
    """Lista para elegir a quién promover como principal, sin salir del
    modal "Corregir destinatario" (conversación 2026-08-17, pedido
    explícito -- versión reducida de la tab "Residentes" de `/residentes`:
    un clic sobre alguien YA activo en esa unidad, listo, sin el resto de
    la ficha del cliente).

    Excluye al principal actual (`es_principal` ya es su estado, no tiene
    sentido "promoverlo" a lo que ya es) -- promover a cualquiera de los
    demás degrada al actual automáticamente (`promover_a_principal`).

    Returns:
        `{"unidad": "TORRE X · Apto Y", "candidatos": [{"ocupante_id":
        "...", "nombre": "..."}]}`, o `{"unidad": None, "candidatos": []}`
        si `torre`/`apartamento` no resuelven a una unidad real."""
    try:
        apto = resolver_apartamento(db, torre, apartamento)
    except ValueError:
        return {"unidad": None, "candidatos": []}
    ocupantes = listar_ocupantes(db, apto)
    candidatos = [
        {"ocupante_id": str(o.id), "nombre": o.nombre} for o in ocupantes if not o.es_principal
    ]
    return {"unidad": f"{apto.torre} · Apto {apto.apartamento}", "candidatos": candidatos}


@router.post("/paquetes/promover-principal")
def promover_principal_action(
    request: Request,
    db: Session = Depends(get_db),
    staff: Usuario = Depends(current_staff),
    ocupante_id: str = Form(...),
    paquete_id: str = Form(None),
    contacto: str = Form(None),
    origen: str = Form("corregir"),
):
    """Promueve a `ocupante_id` como principal de su unidad -- la versión
    reducida de "Promover a otro residente", disparada desde el aviso de
    Principal de "+ Nuevo residente" (conversación 2026-08-17, pedido
    explícito: hacerlo sin salir del modal de origen ni perder el lugar
    donde estaba el staff). UN solo modal "Promover" por paquete, sirve
    tanto a "Corregir destinatario" como a "Recibir" (mismo pedido,
    ampliado el mismo día: "continua con el punto 2" -- portar la misma
    vista previa a Recibir).

    `paquete_id`/`contacto` (opcionales, ocultos en el form -- puestos por
    JS al abrir el modal "Promover"): si vienen, el redirect de éxito
    vuelve a reabrir el modal de origen de ESE paquete Y retipea el
    contacto solo, para que la vista previa se dispare de nuevo (ahora sin
    el bloqueo de Principal, listo para "Mudar residente") sin que el
    staff tenga que escribirlo de nuevo. `origen` ("corregir" | "recibir",
    puesto por el JS de cada "Degradarlo" antes de abrir este modal)
    decide CUÁL de los dos reabre -- default "corregir" por compatibilidad
    con cualquier form viejo en caché que no lo mande."""
    try:
        oc_uuid = uuid.UUID(ocupante_id)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocupante no encontrado")
    ocupante = db.get(Ocupante, oc_uuid)
    if ocupante is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocupante no encontrado")

    try:
        promover_a_principal(db, ocupante)
    except ValueError as exc:
        return _render_lista(request, db, staff, error=str(exc), status_code=400)
    except IntegrityError:
        # Carrera real (dos promociones a la vez sobre el mismo
        # Apartamento) -- mismo criterio que `customers_manage.py`.
        db.rollback()
        return _render_lista(
            request, db, staff,
            error="Alguien más ya hizo un cambio en este apartamento -- actualiza la página e intenta de nuevo.",
            status_code=400,
        )

    destino = "/paquetes"
    if paquete_id:
        parametro = "recibir" if origen == "recibir" else "corregir"
        destino = f"/paquetes?{parametro}={paquete_id}"
        if contacto:
            destino += f"&recontactar={quote(contacto)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


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
    origen: str = Form(None),
):
    """Corrige destinatario de un Paquete en `ESTADOS_CORREGIBLES`
    (`ANUNCIADO`/`RECIBIDO`) — excepción acotada a ADR-0001 (ver
    `paquete_lifecycle.corregir_destinatario`).

    `origen == "ver"` (conversación 2026-08-16, pedido explícito): cuando el
    modal "Corregir" se abrió desde SU PROPIO botón dentro del modal "Ver"
    (campo oculto puesto por ese botón, ver `_resultados.html`), el éxito
    redirige a `/paquetes?ver=<id>` para reabrir Ver en vez de dejar al
    staff en la lista sola. Las otras dos entradas a este mismo modal
    (advertencia de la columna Cliente, "Modificar" de Acciones) ponen este
    campo en `""` al abrir, así que conservan el redirect de siempre.

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
    destino = f"/paquetes?ver={paquete.id}" if origen == "ver" else "/paquetes"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)
