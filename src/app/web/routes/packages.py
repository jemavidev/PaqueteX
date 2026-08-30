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
from app.domain.preferencia_notificacion import CanalNotificacion
from app.domain.preferencia_notificacion_service import preferencias_activas_por_persona
from app.domain.usuario import Usuario

from ..config import public_base_url_relaxed
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
    resultado = preparar_notificacion(db, paquete, evento, public_base_url_relaxed())
    if resultado is not None:
        background_tasks.add_task(enviar_en_segundo_plano, sender, *resultado)


def _personas_por_id(db: Session, ids: set) -> dict:
    """`{persona_id: Persona}` para todos los `ids` no nulos, en UNA sola
    consulta -- helper batch compartido por `p.persona_anunciante`/
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


def _mensaje_whatsapp(paquete: Paquete) -> str:
    """Texto pre-cargado (`?text=`) del botón de WhatsApp de Acciones (issue
    222, .scratch/pendientes-cliente, plantilla exacta pedida por el
    cliente) -- con negrilla nativa de WhatsApp (`*texto*`), a diferencia
    del cuerpo compartido de `notificacion_service.PLANTILLAS_DEFAULT`
    (SMS/Email/WhatsApp automáticos), que se quedó en texto plano porque esa
    sintaxis no significa nada fuera de WhatsApp."""
    estado_texto = paquete.estado.value.capitalize()
    link = f"{public_base_url_relaxed() or ''}/consultar?q={paquete.access_code}"
    return (
        f"Hola *{paquete.recipient_name}*, tu paquete con código "
        f"*{paquete.access_code}* está *{estado_texto}*. "
        f"Consulta más detalles aquí: {link}"
    )


def _whatsapp_notificacion_permitida(
    preferencias_whatsapp: dict, persona: Persona | None, evento
) -> bool:
    """¿El botón de WhatsApp de Acciones debe estar HABILITADO (issue 222,
    .scratch/pendientes-cliente)? Refleja la preferencia real de `persona`
    para WhatsApp × `evento` (matriz Canal × Evento, la misma que gestiona
    `/mis-datos`) -- ya no basta con que exista un canal de contacto,
    también tiene que estar permitido.

    `preferencias_whatsapp` es el batch precomputado por
    `preferencias_activas_por_persona` (una query para TODA la página, no
    una por fila -- evita el N+1 que atrapa
    `test_lista_no_dispara_una_query_de_persona_o_usuario_por_paquete`).

    Sin `persona` resuelta (`_persona_para_notificar` no encontró a nadie
    con identidad propia, cae al teléfono crudo del snapshot o al
    Anunciante sin Persona) no hay ninguna preferencia que consultar --
    se deja habilitado (mismo criterio histórico: siempre debe haber a
    quién notificar) en vez de bloquear un caso que ya era un fallback."""
    if persona is None:
        return True
    return preferencias_whatsapp.get((str(persona.id), evento.value), True)


def _persona_para_notificar(
    persona_identidad: Persona | None, persona_prestada: Persona | None
) -> Persona | None:
    """A quién debe apuntar el ícono de WhatsApp del destinatario (issue
    101, .scratch/pendientes-cliente, pedido explícito: "la finalidad de
    esto es que se tenga siempre un lugar donde se pueda notificar, ya sea
    whatsapp/teléfono propio o del residente principal") -- SIEMPRE debe
    haber alguien a quien notificar, en este orden:

    1. El canal propio del destinatario YA IDENTIFICADO (`persona_
       identidad` -- WhatsApp o teléfono, cualquiera de los dos que tenga).
    2. Si no tiene ninguno de los dos (o no se identificó a nadie), el
       contacto prestado que ya resolvió el snapshot al anunciar (issue
       163 -- el Principal de su unidad, o el Anunciante): `persona_
       prestada`.

    Antes del fix de issue 101, el ícono usaba SIEMPRE `persona_prestada`
    -- issue 163 llena `recipient_phone` con el teléfono del Principal
    cuando el destinatario no tiene teléfono propio (a propósito, esa
    columna es SOLO teléfono, nunca WhatsApp -- la leen SMS/OTP como
    número real), así que un destinatario CON WhatsApp propio pero SIN
    teléfono terminaba notificando a su Principal en vez de a él mismo."""
    if persona_identidad is not None and (
        persona_identidad.whatsapp_usuario or persona_identidad.telefono
    ):
        return persona_identidad
    return persona_prestada


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


def _destinatario_coincide_con_candidato_real(paquete: Paquete, candidatos: list[dict]) -> bool:
    """True si `recipient_name` coincide con un candidato real de
    `candidatos_correccion` -- extraído (issue 189, ronda 4) de
    `_destinatario_sin_confirmar` para reusar el MISMO criterio como
    bloqueo real en `receive_action` (`_destinatario_sin_confirmar` es una
    advertencia -- se puede ignorar; esta función respalda una decisión de
    bloquear una acción, así que ambas comparten exactamente la misma regla
    a propósito, nunca dos versiones que puedan divergir).

    Regla estricta (coincidir con un Ocupante REAL, `estado_ocupante`
    puesto -- ver `_construir_candidatos` -- no solo con el Anunciante) SOLO
    aplica cuando hay Apartamento resuelto Y esa unidad YA tiene al menos un
    Ocupante real (`hay_ocupantes_reales`) -- ahí sí hay alguien real con
    quien el destinatario podría estar confundiéndose (caso real "FANTASMA
    4"/Angélica). Sin eso (sin Apartamento, o con Apartamento pero unidad
    genuinamente vacía -- nadie vivió ahí todavía, nada con qué confundirse)
    el Anunciante sigue bastando por sí solo -- mismo comportamiento de
    siempre, exigir "+ Nuevo residente" para el primer paquete de una unidad
    nueva sería fricción sin ningún problema real que evitar."""
    nombre = (paquete.recipient_name or "").strip().lower()
    if not nombre or not candidatos:
        return False
    tiene_apartamento = bool(
        paquete.snapshot_conjunto and paquete.snapshot_torre and paquete.snapshot_apartamento
    )
    hay_ocupantes_reales = any(c.get("estado_ocupante") for c in candidatos)
    if not tiene_apartamento or not hay_ocupantes_reales:
        return any(c["nombre"].strip().lower() == nombre for c in candidatos)
    return any(
        c["nombre"].strip().lower() == nombre and c.get("estado_ocupante")
        for c in candidatos
    )


def _destinatario_sin_confirmar(
    paquete: Paquete, candidatos: list[dict], persona_anunciante: Persona | None
) -> bool:
    """True si el destinatario actual todavía no está confirmado -- ground
    truth recalculado en cada lectura, no se guarda.

    Issue 189 (.scratch/pendientes-cliente): reemplaza a la vieja
    `_nombre_no_coincide`, que apagaba esta advertencia con
    `paquete.corrected_at is not None` -- ese campo es COMPARTIDO con
    `corregir_apartamento` (`paquete_lifecycle.py`, ver ADR-0001: "el
    esquema no distingue cuál de las dos correcciones ocurrió"). Bug real
    reportado en vivo con varios paquetes de prueba (FANTASMA 1/2/3, ESTE ES
    UN CLIENTE FANTASMA): asignar SOLO la unidad (sin resolver a nadie real)
    ya ponía `corrected_at`, apagando esta advertencia PARA SIEMPRE aunque
    el destinatario nunca se hubiera resuelto a nadie -- sin ninguna otra
    pista de que ese paso seguía pendiente.

    Mientras el paquete siga en `ESTADOS_CORREGIBLES` (algo TODAVÍA
    accionable): se recalcula contra la realidad actual, sin importar qué
    haya tocado `corrected_at`.

    SIN Apartamento resuelto todavía en el snapshot: `candidatos` trae solo
    al Anunciante -- mismo comportamiento de siempre (avisa si el nombre
    anunciado no coincide con el propio registrado).

    CON Apartamento YA resuelto (ronda 2 de issue 189, pedido explícito
    tras confirmar con el cliente -- ejemplo real FANTASMA 2: se anuncia
    "para mí mismo" SIN unidad, y después se le asigna Torre 2 · 302, una
    unidad real donde esa persona NO es residente; como `recipient_name`
    seguía coincidiendo con su propio nombre de Anunciante, quedaba
    "confirmado" igual, aunque nunca se hubiera verificado que de verdad
    vive ahí): "para mí mismo" YA NO alcanza por sí solo una vez que hay
    unidad -- el Anunciante sigue ofreciéndose como candidato en
    `candidatos_correccion` (comodín para no bloquear Recibir/Asignar sin
    romper ningún flujo), pero acá debe coincidir específicamente con un
    Ocupante REAL de esa unidad. `estado_ocupante` (`_construir_candidatos`,
    paquete_correccion_service.py) solo viene poblado para candidatos que sí
    son Ocupantes -- `None` para el Anunciante cuando NO es también Ocupante
    de esta unidad, la señal exacta que hace falta acá.

    Ya en un estado terminal (ENTREGADO/CANCELADO): nada de esto es
    accionable (ni el ícono es clickeable ahí, ver `_resultados.html`) --
    `candidatos` ni se calcula para esos estados (`corregibles` en el
    caller, evita el costo para historial que ya no se puede tocar), así
    que acá se conserva el criterio ORIGINAL, más simple, como aviso
    puramente histórico: compara contra el nombre YA REGISTRADO del
    Anunciante, y si `corrected_at` está puesto lo toma como que sí hubo una
    corrección real -- válido en este tramo porque las dos correcciones
    (`corregir_apartamento`/`corregir_destinatario`) solo pueden ocurrir
    MIENTRAS el paquete todavía estaba en `ESTADOS_CORREGIBLES`, antes de
    llegar acá."""
    if paquete.estado in ESTADOS_CORREGIBLES:
        return not _destinatario_coincide_con_candidato_real(paquete, candidatos)
    if paquete.corrected_at is not None:
        return False
    if persona_anunciante is None or not persona_anunciante.nombre:
        return False
    nombre_anunciante = persona_anunciante.nombre.strip().lower()
    return nombre_anunciante != (paquete.recipient_name or "").strip().lower()


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
    # ejemplo "CAMILA OSPINA"): originalmente solo para paquetes sin ningún
    # teléfono en el snapshot. Ampliado (issue 101, .scratch/pendientes-
    # cliente) a TODOS los `recipient_name` -- ahora también lo usa la
    # identidad del link de abajo cuando el teléfono SÍ resuelve, pero a
    # OTRA Persona -- ver docstring de `_personas_por_nombre`.
    personas_por_nombre_destinatario = _personas_por_nombre(
        db, {p.recipient_name for p in paquetes}
    )
    # Preferencias de WhatsApp del botón de Acciones (issue 222, .scratch/
    # pendientes-cliente) -- batch por TODAS las Personas candidatas de la
    # página (mismo criterio "un puñado fijo de queries" de arriba), no una
    # consulta por fila dentro del loop principal de más abajo.
    preferencias_whatsapp = preferencias_activas_por_persona(
        db,
        {p.id for p in personas_por_telefono_destinatario.values()}
        | {p.id for p in personas_por_nombre_destinatario.values()},
        CanalNotificacion.WHATSAPP,
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
        # `candidatos_correccion` ANTES de `advertencia_nombre` -- issue 189,
        # la nueva `_destinatario_sin_confirmar` la necesita ya resuelta.
        p.candidatos_correccion = candidatos_por_paquete.get(p.id, [])
        p.advertencia_nombre = _destinatario_sin_confirmar(
            p, p.candidatos_correccion, personas.get(p.announced_by_persona_id)
        )
        p.actor_ultima_accion = _actor_ultima_accion(p, usuarios, personas)
        p.fecha_ultima_accion = _fecha_ultima_accion(p)
        p.duracion_transcurrida = _duracion_transcurrida(p)
        p.direccion_corta = _direccion_corta(p)
        p.timeline = timelines.get(p.id, [])
        p.persona_anunciante = personas.get(p.announced_by_persona_id)
        # Contacto "prestado" -- lo que `recipient_phone` trae congelado tal
        # cual, sin importar de quién sea: issue 163 lo llena a propósito
        # con el teléfono del Principal de la unidad (o del Anunciante)
        # cuando el destinatario no tiene teléfono propio, para que SIEMPRE
        # haya a quién contactar. Base del fallback de más abajo, nunca el
        # resultado final si el destinatario SÍ tiene su propio canal.
        persona_destino_contacto = personas_por_telefono_destinatario.get(p.recipient_phone)
        if persona_destino_contacto is None and not p.recipient_phone:
            persona_destino_contacto = personas_por_nombre_destinatario.get(p.recipient_name)
        # Identidad para el título del modal "Ver" y el link de Torre/Apto
        # (conversación 2026-08-21 / issue 100, .scratch/pendientes-cliente):
        # a diferencia del contacto de arriba, acá SÍ importa que sea
        # realmente la Persona del destinatario -- bug real reportado en
        # vivo (issue 101, .scratch/pendientes-cliente, ejemplo "JESUS
        # VILLALOBOS"/"J2PY"): el fallback de issue 163 deja `recipient_
        # phone` con el teléfono de OTRA Persona real (el Principal de la
        # unidad en ese momento), así que confiar en el match por teléfono
        # acá enlazaba a la ficha equivocada -- un residente sin teléfono
        # propio parecía "vivir" en la unidad de quien prestó su teléfono
        # para la notificación. Se confía en el match por teléfono SOLO si
        # el nombre de esa Persona coincide con `recipient_name`; si no
        # coincide (o no hubo match), se intenta por nombre -- mismo
        # mecanismo que ya usaba el camino "sin teléfono" de arriba, ahora
        # también cubre "con teléfono, pero prestado". Sin ningún match,
        # `None` (ej. `declarado_por_cliente` sin ningún co-residente que
        # coincida) -- el nombre se queda como texto plano, no hay a dónde
        # enlazarlo (más seguro que enlazar a la persona equivocada).
        persona_destino = persona_destino_contacto
        if persona_destino is None or persona_destino.nombre != p.recipient_name:
            persona_destino = personas_por_nombre_destinatario.get(p.recipient_name)
        p.persona_destino_id = persona_destino.id if persona_destino else None
        # WhatsApp del ícono de Acciones -- ver `_persona_para_notificar`
        # para la prioridad completa (issue 101, .scratch/pendientes-
        # cliente, pedido explícito): propio primero, prestado (issue 163)
        # como garantía de que SIEMPRE haya a quién notificar.
        persona_para_whatsapp = _persona_para_notificar(persona_destino, persona_destino_contacto)
        _base_whatsapp = _whatsapp_url_destinatario(p, persona_para_whatsapp)
        # Mensaje pre-cargado + gate por preferencia (issue 222, .scratch/
        # pendientes-cliente): el link SIEMPRE se calcula (falta de éste es
        # "sin teléfono registrado"), pero el botón solo queda HABILITADO si
        # la preferencia WhatsApp × estado actual de `persona_para_whatsapp`
        # lo permite -- ver `_whatsapp_notificacion_permitida`.
        p.whatsapp_url_destinatario = (
            f"{_base_whatsapp}?text={quote(_mensaje_whatsapp(p), safe='')}"
            if _base_whatsapp
            else None
        )
        p.whatsapp_notificacion_permitida = _whatsapp_notificacion_permitida(
            preferencias_whatsapp, persona_para_whatsapp, p.estado
        )
        apto = apartamentos_por_terna.get(
            (p.snapshot_conjunto, p.snapshot_torre, p.snapshot_apartamento)
        )
        # "Residentes de la unidad" (tercer seguimiento el mismo día de
        # issue 101, .scratch/pendientes-cliente, pedido explícito del
        # cliente, ejemplo real UKT7): la dirección RELEVANTE para esta
        # sección es la del destinatario, no la del snapshot congelado --
        # si el destinatario identificado ya se mudó, se sigue SU domicilio
        # ACTUAL (`apartamento_actual_id`), para mostrar a sus residentes
        # reales de HOY (ej. Angélica + Daniela, que sí viven juntas ahora)
        # en vez de a quien vive en la dirección VIEJA (que ya no tiene
        # ninguna relación ni con el destinatario ni con este paquete).
        # `apartamento_actual_id is not None` es a propósito -- `None` NO
        # significa "se mudó de acá", significa "nunca fue Ocupante
        # registrado de NINGUNA unidad" (ej. un Anunciante con `apartamento=`
        # explícito en `announce()`, sin padrón propio) -- ahí se usa la
        # unidad del snapshot como siempre (bug real encontrado por un test
        # ya existente, `test_modal_ver_muestra_residentes_de_la_unidad`,
        # al implementar la primera versión de este fix). Sin destinatario
        # identificado, también cae al snapshot (comportamiento original,
        # sin cambios).
        p._apartamento_id_residentes = (
            persona_destino.apartamento_actual_id
            if persona_destino is not None and persona_destino.apartamento_actual_id is not None
            else (apto.id if apto else None)
        )

    # Ícono "cambio reciente de apartamento" (issue 165, .scratch/pendientes-
    # cliente) -- SEGUNDO loop porque `persona_destino_id` recién se resuelve
    # arriba, dentro del loop principal (no se conoce de antemano el set
    # completo hasta que termina). Batch, no una consulta por fila.
    cambios_recientes = cambios_recientes_de_apartamento(
        db, {p.persona_destino_id for p in paquetes if p.persona_destino_id}
    )
    # "Residentes de la unidad" (ver comentario en el loop principal, arriba)
    # -- `p._apartamento_id_residentes` puede apuntar a un apartamento que
    # NUNCA fue snapshot de ningún paquete de la página (la unidad ACTUAL de
    # un destinatario mudado, ej. Torre 2 · 302 de Angélica para el paquete
    # UKT7, snapshot en Torre 1 · 302) -- ese id no está en `ocupantes_por_
    # apartamento` todavía. Un solo batch adicional para los ids que falten
    # (normalmente ninguno o muy pocos: solo dispara con destinatarios
    # mudados), reusando `_ocupantes_por_apartamento_id`.
    ids_faltantes = {
        p._apartamento_id_residentes
        for p in paquetes
        if p._apartamento_id_residentes is not None
        and p._apartamento_id_residentes not in ocupantes_por_apartamento
    }
    if ids_faltantes:
        ocupantes_nuevos = _ocupantes_por_apartamento_id(db, ids_faltantes)
        ocupantes_por_apartamento.update(ocupantes_nuevos)
        # Bug real reportado en vivo (conversación 2026-08-23, ejemplo
        # "LAIS HERNANDEZ"/"RAFAEL TORRES"): `personas` (para armar
        # `r.persona` de cada fila -- el WhatsApp/teléfono/email de la
        # plantilla) ya se había resuelto ANTES de este batch, con los
        # `persona_id` de los Ocupantes de `ocupantes_por_apartamento`
        # ORIGINAL (solo unidades que son snapshot de algún paquete) -- los
        # Ocupantes de una unidad NUEVA (como la de arriba) traían
        # `persona_id`s que `personas` nunca había visto, así que
        # `personas.get(o.persona_id)` daba `None` para ellos: `r.persona`
        # quedaba vacío y la plantilla (`{%- if r.persona and r.persona.
        # whatsapp_usuario %}`) no mostraba su WhatsApp -- aunque SÍ lo
        # tuvieran -- ni su teléfono ni su email. Mismo batch de siempre,
        # solo con los ids que falten.
        persona_ids_faltantes = {
            o.persona_id
            for ocupantes in ocupantes_nuevos.values()
            for o in ocupantes
            if o.persona_id and o.persona_id not in personas
        }
        if persona_ids_faltantes:
            personas.update(_personas_por_id(db, persona_ids_faltantes))

    for p in paquetes:
        p.cambio_reciente_apartamento = (
            cambios_recientes.get(p.persona_destino_id) if p.persona_destino_id else None
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
            for o in ocupantes_por_apartamento.get(p._apartamento_id_residentes, [])
        ] if p._apartamento_id_residentes else []
        del p._apartamento_id_residentes  # transitorio, no lo necesita la plantilla

    return paquetes, pagina, total_paginas


# Issue 188 (.scratch/pendientes-cliente): bug real reportado en vivo, 3
# casos seguidos (RAFA T, ESTE ES UN CLIENTE FANTASMA, FANTASMA 1) -- [[186]]/
# [[187]] SÍ reabren "Corregir destinatario" con candidatos reales cuando
# "Asignar apartamento"/"Recibir" dejan una unidad sin residente vinculado,
# pero un modal que se reabre SOLO no es una señal lo bastante fuerte --
# el staff no notaba que había un paso pendiente y seguía de largo. Texto
# fijo (no un mensaje libre por query param, evita cualquier duda de
# inyección) -- se identifica en la URL con `aviso=residente_pendiente`
# (`packages_list`), nunca se arma a mano en el redirect.
_AVISO_RESIDENTE_PENDIENTE = (
    "Se asignó la unidad, pero todavía no hay ningún residente vinculado a "
    "ella -- elige uno de la lista o registra uno nuevo en el modal que se "
    "abrió para completar la asociación."
)

# Issue 189 (ronda 4, .scratch/pendientes-cliente): esconder el problema
# (íconos/enlaces que dejan de mostrarse cuando el destinatario no está
# confirmado, rondas 1-3) no lo soluciona -- el hueco real era que "Recibir"
# dejaba completar la recepción física sin resolver a nadie, cuando la
# unidad recién declarada YA tiene residentes reales (bug real reportado en
# vivo, "FANTASMA 4"). Ahora ese caso NO completa la recepción -- ver el
# bloqueo en `receive_action`, antes de `receive()`.
_AVISO_RECEPCION_PENDIENTE = (
    "La unidad quedó asignada, pero la recepción todavía no se completó -- "
    "elegí quién recibe (o registra uno nuevo) en el modal que se abrió "
    "para terminar."
)


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
    aviso=None,
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
            "aviso": aviso,
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
    aviso: str = None,
):
    # Issue 188 (ronda 4: +"recepcion_pendiente"): `aviso` en la URL es solo
    # un CÓDIGO whitelisteado, nunca texto libre -- el mensaje real siempre
    # sale de una constante fija server-side, no de lo que venga en el
    # query string.
    _AVISOS = {
        "residente_pendiente": _AVISO_RESIDENTE_PENDIENTE,
        "recepcion_pendiente": _AVISO_RECEPCION_PENDIENTE,
    }
    aviso_texto = _AVISOS.get(aviso)
    return _render_lista(
        request, db, staff, estado=estado, q=q, pagina=pagina,
        ver_paquete_id=ver, corregir_paquete_id=corregir, recibir_paquete_id=recibir,
        entregar_paquete_id=entregar, recontactar_valor=recontactar,
        aviso=aviso_texto,
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
    # Issue 187 (.scratch/pendientes-cliente): mismo bug que [[186]]
    # (`assign_apartment_action`), en este otro punto de entrada -- el paso
    # de declarar unidad DENTRO de Recibir tiene el mismo hueco: si el
    # staff elige Torre+Apartamento acá pero no toca "Nuevo residente"
    # (ninguno de los 2 sub-pasos es obligatorio, pueden dejarse vacíos por
    # diseño), el paquete queda RECIBIDO mostrando una unidad sin que
    # exista ningún Ocupante real vinculado -- capturado ANTES de llamar a
    # `corregir_apartamento` porque ese cambia `paquete.snapshot_apartamento`.
    asigno_apartamento_ahora = bool(
        torre_v and apartamento_v and paquete.snapshot_apartamento is None
    )
    if asigno_apartamento_ahora:
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

    hay_resolucion_residente = bool(candidato_idx or (nuevo_ocupante_nombre or "").strip())
    # Issue 189 (ronda 5, pedido explícito): "para mí mismo" sin resolución
    # explícita se autocompleta con la identidad YA conocida del Anunciante
    # -- ver `_autocompletar_nuevo_residente_yo_mismo`.
    if not hay_resolucion_residente:
        auto_nombre, auto_contacto = _autocompletar_nuevo_residente_yo_mismo(db, paquete)
        if auto_nombre:
            candidato_idx = "nuevo"
            nuevo_ocupante_nombre = auto_nombre
            nuevo_ocupante_contacto = auto_contacto
            hay_resolucion_residente = True
    if hay_resolucion_residente:
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

    # Issue 189 (ronda 4, pedido explícito -- "esconder el problema no lo
    # soluciona"): rondas 1-3 aseguraban que la UI nunca MINTIERA sobre un
    # destinatario sin confirmar (ícono persistente, caja/links ocultos),
    # pero eso solo decora el problema -- nada impedía que existiera. Bug
    # real reportado en vivo ("FANTASMA 4"): declarar Torre 2 · 302 (unidad
    # real con Angélica de Principal) sin elegir a nadie dejaba el paquete
    # en RECIBIDO igual, con una unidad asignada que ningún Ocupante real
    # respaldaba. Ahora, si a esta altura el paquete tiene una unidad
    # resuelta (recién declarada arriba en este mismo envío, o ya la tenía
    # de antes) y esa unidad YA tiene residentes reales pero el destinatario
    # no coincide con ninguno (`_destinatario_coincide_con_candidato_real`,
    # misma regla que ya usa el ícono persistente -- nunca dos criterios que
    # puedan divergir), la recepción física NO se completa acá -- se
    # bloquea ANTES de `receive()`, en vez de recibir igual y confiar en un
    # aviso que el staff podía pasar por alto. La unidad SÍ queda asignada
    # (el commit de `corregir_apartamento` de arriba ya corrió -- información
    # real y útil, no se descarta), y se reabre este mismo modal "Recibir" --
    # ahora con `sin_apartamento=False` (el snapshot ya quedó puesto), así
    # que `candidatos_correccion` trae a los residentes reales de esa unidad
    # de inmediato, sin tener que adivinar ni pasar por un segundo modal
    # separado. Guía/tipo/condición/fotos de este intento se descartan a
    # propósito -- recibir de verdad no ocurrió todavía, no hay nada que
    # preservar.
    tiene_apartamento_ahora = bool(
        paquete.snapshot_conjunto and paquete.snapshot_torre and paquete.snapshot_apartamento
    )
    if tiene_apartamento_ahora:
        candidatos_actuales = candidatos_correccion(db, paquete)
        if not _destinatario_coincide_con_candidato_real(paquete, candidatos_actuales):
            db.commit()
            return RedirectResponse(
                f"/paquetes?recibir={paquete.id}&aviso=recepcion_pendiente",
                status_code=status.HTTP_303_SEE_OTHER,
            )

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
    # Issue 189 (ronda 4): si llegamos hasta acá, `receive()` YA corrió --
    # el bloqueo de arriba garantiza que eso solo pasa con el destinatario
    # confirmado (o sin ninguna unidad real con la que pudiera confundirse),
    # así que no hace falta ningún redirect especial más -- issue 187's
    # redirect a "Corregir" quedó retirado, ya no hay ningún caso real que
    # cubrir (sería redundante: reabriría un modal a confirmar algo que ya
    # está confirmado).
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

    Issue 186 (.scratch/pendientes-cliente): bug real reportado en vivo --
    con `nuevo_ocupante_nombre` vacío (el staff dejó "+ Nuevo residente"
    colapsado, solo asignó la unidad), el paquete quedaba mostrando esa
    unidad en la columna Dirección SIN que existiera ningún Ocupante real
    vinculado -- ni la Persona quedaba con `apartamento_actual_id`, ni
    aparecía en `/residentes` para esa unidad, ni en "Agrupar por
    apartamento". Nada avisaba que ese paso seguía pendiente. Redirige con
    `?corregir=<id>` (mismo query param que ya usa `packages_list` para
    reabrir "Corregir destinatario", ver `corregir_paquete_id`) en vez de
    a la lista sola -- con la unidad YA resuelta, `candidatos_correccion`
    encuentra a los Ocupantes reales de esa unidad, así que el staff puede
    elegir a uno con un clic o registrar a alguien nuevo ahí mismo, sin
    tener que saber que ese segundo paso hacía falta. Solo cuando NO se
    llenó "+ Nuevo residente" -- si sí se llenó, la asociación real ya
    quedó completa acá mismo, no hace falta un segundo paso.
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
    contacto_nuevo_v = nuevo_ocupante_contacto
    # Issue 189 (ronda 5, pedido explícito): "para mí mismo" sin "+ Nuevo
    # residente" explícito se autocompleta con la identidad YA conocida del
    # Anunciante -- ver `_autocompletar_nuevo_residente_yo_mismo`.
    if not nombre_nuevo_v:
        auto_nombre, auto_contacto = _autocompletar_nuevo_residente_yo_mismo(db, paquete)
        if auto_nombre:
            nombre_nuevo_v = auto_nombre
            contacto_nuevo_v = auto_contacto
    if nombre_nuevo_v:
        nombre, telefono = _resolver_desde_candidato(
            db, paquete, "nuevo", nombre_nuevo_v, contacto_nuevo_v,
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
    if nombre_nuevo_v:
        return RedirectResponse("/paquetes", status_code=status.HTTP_303_SEE_OTHER)
    # Issue 189 (ronda 4): mismo criterio de confirmación que ahora bloquea
    # `receive_action` -- si la unidad recién asignada es genuinamente
    # vacía (o el destinatario ya coincide con el Anunciante, sin ningún
    # residente real con quien pudiera confundirse), reabrir "Corregir" acá
    # sería redundante (nada está realmente pendiente). Solo se reabre
    # cuando de verdad hace falta -- misma señal que ya usa el ícono
    # persistente, nunca dos criterios que puedan divergir.
    if _destinatario_coincide_con_candidato_real(paquete, candidatos_correccion(db, paquete)):
        return RedirectResponse("/paquetes", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(
        f"/paquetes?corregir={paquete.id}&aviso=residente_pendiente",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _autocompletar_nuevo_residente_yo_mismo(db: Session, paquete: Paquete) -> tuple[str, str] | tuple[None, None]:
    """`(nombre, contacto)` del propio Anunciante para autocompletar "+ Nuevo
    residente" -- o `(None, None)` si no aplica.

    Issue 189 (ronda 5, pedido explícito -- flujo /announce "anunciar +
    recibir" en un solo paso, con o sin residentes ya en la unidad): si el
    destinatario es "para mí mismo" (`recipient_name` coincide con el
    nombre YA registrado del Anunciante) y esa Persona todavía NO es
    Ocupante real de la unidad ya resuelta del paquete, no tiene sentido
    bloquear pidiendo que el staff re-teclee un nombre y teléfono que YA se
    capturaron al anunciar -- se resuelve con esos mismos datos, como si
    "+ Nuevo residente" se hubiera llenado a mano. Reusado por
    `receive_action` y `assign_apartment_action` -- ambos tratan el
    resultado exactamente igual que la entrada manual, incluidas sus
    protecciones (`_resolver_desde_candidato`, `permitir_mover=True`): si
    esa Persona ya es Ocupante activo de OTRA unidad, este camino
    automático nunca marca `mover_de_otra_unidad`, así que sigue
    rechazando en vez de mudarla en silencio -- cae al flujo manual de
    siempre en ese caso puntual.

    `None` en cualquiera de estos casos: sin Apartamento resuelto todavía
    (nada que registrar todavía), sin Anunciante resoluble, el destinatario
    no es "para mí mismo", sin ningún contacto propio, o ya es Ocupante
    real de esta unidad (nada que hacer, ya está vinculado)."""
    if not paquete.snapshot_apartamento:
        return None, None
    anunciante = db.get(Persona, paquete.announced_by_persona_id)
    if anunciante is None or not anunciante.nombre:
        return None, None
    nombre_anunciante = anunciante.nombre.strip().lower()
    if nombre_anunciante != (paquete.recipient_name or "").strip().lower():
        return None, None
    contacto = anunciante.telefono or anunciante.whatsapp_usuario
    if not contacto:
        return None, None
    candidatos_ahora = candidatos_correccion(db, paquete)
    ya_es_ocupante_real_aqui = any(
        c.get("estado_ocupante") and c["nombre"].strip().lower() == nombre_anunciante
        for c in candidatos_ahora
    )
    if ya_es_ocupante_real_aqui:
        return None, None
    return anunciante.nombre, contacto


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
