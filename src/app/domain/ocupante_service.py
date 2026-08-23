# -*- coding: utf-8 -*-
"""
Servicio de dominio de Ocupante — padrón de residentes de un Apartamento con
Persona propia opcional (Seam A, ADR-0006).

Invariante: un Apartamento con al menos un Ocupante ACTIVO **confirmado**
tiene SIEMPRE exactamente uno marcado `es_principal`, y ese principal
SIEMPRE tiene `persona_id` (una Persona real, con Teléfono o WhatsApp --
ADR-0007, `.scratch/announce-rapido` ticket 02). La base de datos lo
garantiza con un índice único parcial (`uq_ocupantes_principal_por_
apartamento`); este módulo garantiza que la aplicación nunca intente
violarlo (promover exige `persona_id`, y degrada al anterior en la misma
transacción antes de marcar el nuevo). Un Apartamento con solo Ocupantes
PENDING (sin confirmar todavía) puede legítimamente no tener ningún
principal — ver más abajo.

Invariante nueva (.scratch/mis-datos, ticket 02): una Persona (por Teléfono)
solo puede ser Ocupante ACTIVO de un Apartamento a la vez. Para unirse a
otro, primero debe darse de baja del actual (`dar_de_baja_ocupante`) — nunca
se borra la fila, queda de solo consulta (histórico).

Confirmación (`.scratch/apartamento-catalogo-confirmacion`, ticket 06): todo
Ocupante nuevo nace `pending` (`confirmado_en=None`), incluido el primero de
un Apartamento vacío -- ya no se auto-promueve a principal al crearse. Un
pending no pierde ninguna funcionalidad (puede anunciar/recibir igual que
uno confirmado, `apartamento_actual_id` se sincroniza igual) -- confirmar es
un sello administrativo, no un gate técnico. Solo el Ocupante principal ya
confirmado del mismo Apartamento, o cualquier `Usuario` de staff, puede
confirmar (`confirmar_ocupante`); si el Apartamento no tiene principal
todavía, confirmar promueve en el mismo acto (exige Teléfono, mismo
invariante que `promover_a_principal`). Rechazar un pending reutiliza
`dar_de_baja_ocupante` tal cual -- nunca llegó a confirmarse, así que
`confirmado_en` queda en `NULL` para siempre (se distingue de un confirmado
que luego se fue).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .apartamento import Apartamento
from .apartamento_service import buscar_apartamento_por_terna
from .contacto import clasificar_contacto
from .ocupante import Ocupante
from .paquete import Paquete
from .persona import Persona
from .persona_service import (
    buscar_persona_por_telefono,
    buscar_persona_por_whatsapp,
    get_or_create_persona,
    get_or_create_persona_por_whatsapp,
    update_datos_personales,
)
from .texto import normalizar_nombre
from .usuario import Usuario


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def listar_ocupantes(
    session: Session, apartamento: Apartamento, incluir_baja: bool = False
) -> list[Ocupante]:
    """Los Ocupantes de `apartamento`, principal primero.

    Por defecto solo trae los ACTIVOS (`desvinculado_en IS NULL`) — pasar
    `incluir_baja=True` para ver también el historial de dados de baja (de
    solo consulta, nunca se vuelven a actualizar)."""
    query = session.query(Ocupante).filter(Ocupante.apartamento_id == apartamento.id)
    if not incluir_baja:
        query = query.filter(Ocupante.desvinculado_en.is_(None))
    return query.order_by(Ocupante.es_principal.desc(), Ocupante.created_at.asc()).all()


def residentes_por_torre_apartamento(session: Session) -> dict[str, dict[str, list[str]]]:
    """`{torre: {apartamento: [nombre, ...]}}` -- SOLO las unidades con al
    menos un Ocupante ACTIVO, para todo el catálogo en una sola consulta
    (issue 85, .scratch/pendientes-cliente: buscador de "Asignar
    apartamento" -- antes de asociar un paquete sin unidad a una, el staff
    necesita saber si esa unidad está libre o ya tiene residentes, para no
    mezclar por error a alguien con la familia equivocada). Una unidad
    ausente está libre (sin ningún Ocupante activo); principal primero,
    mismo orden que `listar_ocupantes`. Forma anidada (no tupla como
    llave) a propósito -- se serializa tal cual a JSON para el buscador
    client-side, mismo criterio que `listar_catalogo_por_torre`."""
    filas = (
        session.query(Apartamento.torre, Apartamento.apartamento, Ocupante.nombre)
        .join(Ocupante, Ocupante.apartamento_id == Apartamento.id)
        .filter(Ocupante.desvinculado_en.is_(None))
        .order_by(Ocupante.es_principal.desc(), Ocupante.created_at.asc())
        .all()
    )
    resultado: dict[str, dict[str, list[str]]] = {}
    for torre, apartamento, nombre in filas:
        resultado.setdefault(torre, {}).setdefault(apartamento, []).append(nombre)
    return resultado


def cambios_recientes_de_apartamento(
    session: Session, persona_ids, dias: int = 30
) -> dict:
    """`{persona_id: {"torre": ..., "apartamento": ...}}` -- SOLO las
    Personas de `persona_ids` que dieron de baja un Ocupante (cambiaron o
    dejaron una unidad) en los últimos `dias` días (issue 165, .scratch/
    pendientes-cliente: un ícono en `/paquetes` para explicar por qué dos
    Paquetes de la misma Persona pueden traer direcciones distintas en su
    snapshot -- confuso si no se sabe que se mudó hace poco). La unidad
    devuelta es la que DEJÓ (la vieja), no la actual -- eso es lo que
    explica la diferencia. Con más de una baja reciente, la MÁS reciente
    gana. Una Persona ausente del dict no tuvo ninguna baja reciente."""
    ids = list(persona_ids)
    if not ids:
        return {}
    corte = datetime.now(timezone.utc) - timedelta(days=dias)
    filas = (
        session.query(
            Ocupante.persona_id, Apartamento.torre, Apartamento.apartamento, Ocupante.desvinculado_en
        )
        .join(Apartamento, Apartamento.id == Ocupante.apartamento_id)
        .filter(
            Ocupante.persona_id.in_(ids),
            Ocupante.desvinculado_en.isnot(None),
            Ocupante.desvinculado_en >= corte,
        )
        .order_by(Ocupante.desvinculado_en.desc())
        .all()
    )
    resultado: dict = {}
    for persona_id, torre, apartamento, _desvinculado_en in filas:
        resultado.setdefault(persona_id, {"torre": torre, "apartamento": apartamento})
    return resultado


def ocupante_de_persona(
    session: Session, apartamento: Apartamento, persona_id
) -> Ocupante | None:
    """El Ocupante ACTIVO de `apartamento` ligado a `persona_id`, o `None` si
    esa Persona no está enlistada ahí (todavía, o ya no — un Ocupante dado de
    baja no cuenta). Usado para que declarar el mismo Apartamento más de una
    vez (p.ej. reenviar `/mis-datos` sin cambios) no cree un Ocupante
    duplicado."""
    return (
        session.query(Ocupante)
        .filter(
            Ocupante.apartamento_id == apartamento.id,
            Ocupante.persona_id == persona_id,
            Ocupante.desvinculado_en.is_(None),
        )
        .one_or_none()
    )


def _persona_ya_es_ocupante_activo(session: Session, persona_id) -> bool:
    """El invariante real es "como máximo un Ocupante ACTIVO por Persona,
    en cualquier Apartamento" -- el mismo que asume `ocupante_activo_de_persona`
    (`.one_or_none()`, que revienta con `MultipleResultsFound` si se viola).
    Antes esta comprobación excluía el propio Apartamento (`apartamento_id !=
    apartamento_id`), lo que dejaba colar un SEGUNDO Ocupante activo con el
    mismo Teléfono en la MISMA unidad -- bug real, reproducido: `agregar_ocupante`
    dos veces con el mismo teléfono en el mismo Apartamento creaba 2 filas
    activas, y cualquier llamada posterior a `ocupante_activo_de_persona` para
    esa Persona (login, `/mis-datos`, `announce`, elegibilidad de OTP) crasheaba
    con 500."""
    return (
        session.query(Ocupante)
        .filter(
            Ocupante.persona_id == persona_id,
            Ocupante.desvinculado_en.is_(None),
        )
        .first()
        is not None
    )


def telefonos_activos_del_apartamento_de(session: Session, persona: Persona) -> list[str]:
    """Los Teléfonos de todos los Ocupantes ACTIVOS del Apartamento actual de
    `persona` -- base para ampliar `/mis-paquetes` a toda la unidad
    (`.scratch/mis-paquetes-vista-apartamento`, issue 01).

    Sin Apartamento asignado, devuelve `[persona.telefono]` (el propio, sin
    ampliar nada) -- comportamiento idéntico al que ya existía antes de este
    seam. Con Apartamento, reutiliza `listar_ocupantes` (ya filtra activos,
    principal primero) y colecta el Teléfono de cada Ocupante que tenga
    `persona_id` -- un Ocupante sin Teléfono no puede haber anunciado ni
    recibido nada bajo su propia identidad, así que no aporta nada a la
    lista."""
    if persona.apartamento_actual_id is None:
        return [persona.telefono]

    apartamento = session.get(Apartamento, persona.apartamento_actual_id)
    if apartamento is None:
        return [persona.telefono]

    telefonos = []
    for ocupante in listar_ocupantes(session, apartamento):
        if ocupante.persona_id is None:
            continue
        ocupante_persona = session.get(Persona, ocupante.persona_id)
        if ocupante_persona is not None:
            telefonos.append(ocupante_persona.telefono)
    return telefonos


def ocupante_activo_de_persona(session: Session, persona_id) -> Ocupante | None:
    """El Ocupante ACTIVO de `persona_id`, en cualquier Apartamento, o `None`
    si no es Ocupante activo de ninguno. Gracias a la invariante "un teléfono,
    un apartamento activo a la vez" (ticket 02), esta consulta nunca trae más
    de una fila — se usa para resolver el rol de quien entra a `/mis-datos`
    (principal vs Ocupante no-principal)."""
    return (
        session.query(Ocupante)
        .filter(
            Ocupante.persona_id == persona_id,
            Ocupante.desvinculado_en.is_(None),
        )
        .one_or_none()
    )


def ocupantes_activos_de_personas(session: Session, persona_ids) -> dict:
    """Versión batch de `ocupante_activo_de_persona` -- UN solo query para
    resolver el Ocupante activo de varias Personas a la vez (issue 68,
    badge de Principal/Secundario en `/residentes`), mismo patrón que
    `_apartamentos_por_id` en `customers_manage.py` (evita el N+1 de una
    consulta por fila dentro del loop de la plantilla).

    Devuelve `{persona_id: Ocupante}` -- una Persona sin Ocupante activo
    (nunca pasó por "declarar unidad"/agregar Residente) simplemente no
    aparece en el dict."""
    ids = list(persona_ids)
    if not ids:
        return {}
    ocupantes = (
        session.query(Ocupante)
        .filter(Ocupante.persona_id.in_(ids), Ocupante.desvinculado_en.is_(None))
        .all()
    )
    return {o.persona_id: o for o in ocupantes}


def _persona_de_ocupante_o_principal(session: Session, ocupante: Ocupante) -> Persona | None:
    """La Persona propia de `ocupante`, o si no tiene, la del Principal
    ACTIVO de su Apartamento EN ESE MOMENTO -- resolución compartida por
    `telefono_notificacion_ocupante` (contacto de notificación del
    Destinatario, solo Teléfono) y `anunciante_para_ocupante` (identidad del
    Anunciante cuando `/announce` resuelve un residente por id, ADR-0007,
    `.scratch/announce-rapido` ticket 05 -- Teléfono o WhatsApp)."""
    if ocupante.persona_id is not None:
        return session.get(Persona, ocupante.persona_id)

    principal = (
        session.query(Ocupante)
        .filter(
            Ocupante.apartamento_id == ocupante.apartamento_id,
            Ocupante.es_principal.is_(True),
            Ocupante.desvinculado_en.is_(None),
        )
        .one_or_none()
    )
    if principal is None or principal.persona_id is None:
        return None
    return session.get(Persona, principal.persona_id)


def telefono_notificacion_ocupante(session: Session, ocupante: Ocupante) -> str | None:
    """El teléfono al que le debe llegar un aviso a nombre de `ocupante`: el
    propio si TIENE TELÉFONO, o si no, el del principal ACTIVO de su
    Apartamento EN ESE MOMENTO (.scratch/mis-datos, ticket 08) -- lo usan
    tanto `paquete_service.announce` (se congela en el Paquete al anunciar,
    ADR-0001, nunca se re-resuelve después) como "Corregir destinatario"
    (ticket 09) al declarar un Ocupante nuevo.

    Issue 163 (`.scratch/pendientes-cliente`): "siempre debe haber un
    número... responsable" -- a propósito NO reusa `_persona_de_ocupante_o_
    principal` (que decide el fallback por si `ocupante` tiene Persona
    propia o no, sin mirar si esa Persona tiene Teléfono) -- acá el
    fallback al Principal aplica también cuando `ocupante` SÍ tiene Persona
    propia pero esa Persona es solo-WhatsApp (antes esta función se rendía
    ahí mismo, devolviendo `None` sin intentar el Principal).

    Estrictamente TELÉFONO, nunca WhatsApp, aunque `ocupante` (o su
    Principal) tenga solo `whatsapp_usuario` (ADR-0007, `.scratch/announce-
    rapido` ticket 03) -- devuelve `None` si NINGUNO de los dos (Ocupante ni
    Principal) tiene Teléfono, a propósito: el único consumidor de este
    valor es `Paquete.recipient_phone`, una columna que SMS/OTP ya leen
    como Teléfono real (`notificacion_service.resolver_destino_
    notificable`, `otp_service`). Meter un usuario de WhatsApp ahí rompería
    esos consumidores, no los ampliaría -- no existe hoy ningún canal de
    envío por WhatsApp que pudiera usar ese dato de todos modos. Para la
    identidad del ANUNCIANTE (que sí puede ser WhatsApp), ver
    `anunciante_para_ocupante` (esa función SÍ reusa `_persona_de_ocupante_
    o_principal` tal cual -- ahí un Principal solo-WhatsApp es una
    identidad completa y válida, a diferencia de acá)."""
    if ocupante.persona_id is not None:
        persona = session.get(Persona, ocupante.persona_id)
        if persona is not None and persona.telefono:
            return persona.telefono

    principal = (
        session.query(Ocupante)
        .filter(
            Ocupante.apartamento_id == ocupante.apartamento_id,
            Ocupante.es_principal.is_(True),
            Ocupante.desvinculado_en.is_(None),
        )
        .one_or_none()
    )
    if principal is None or principal.persona_id is None:
        return None
    persona_principal = session.get(Persona, principal.persona_id)
    return persona_principal.telefono if persona_principal is not None else None


def telefono_notificacion_de_persona(session: Session, persona: Persona) -> str | None:
    """Igual que `telefono_notificacion_ocupante`, pero partiendo de una
    Persona en vez de un Ocupante ya resuelto -- el teléfono propio si
    tiene, o si no, el del Principal ACTIVO de SU unidad actual EN ESE
    MOMENTO, si es Ocupante de alguna (issue 163, .scratch/pendientes-
    cliente: "siempre debe haber un número... responsable" -- antes solo el
    camino de Destinatario declarado-por-cliente con match de co-residente
    aplicaba este fallback; los demás caminos de `paquete_service.announce`
    se quedaban en `None` sin intentarlo si `persona` no tenía Teléfono
    propio, aunque su unidad sí tuviera un Principal alcanzable).

    Mismas reglas que la contraparte de Ocupante: estrictamente Teléfono,
    nunca WhatsApp (`Paquete.recipient_phone` es una columna que SMS/OTP ya
    leen como Teléfono real). `None` si `persona` no tiene Teléfono propio y
    tampoco es Ocupante activo de una unidad con Principal alcanzable por
    Teléfono."""
    if persona.telefono:
        return persona.telefono
    ocupante = ocupante_activo_de_persona(session, persona.id)
    if ocupante is None:
        return None
    return telefono_notificacion_ocupante(session, ocupante)


def anunciante_para_ocupante(session: Session, ocupante: Ocupante) -> Persona | None:
    """La Persona que debe figurar como Anunciante cuando `/announce`
    resuelve un Destinatario por Ocupante puntual (staff, elegido de una
    lista de residentes de una unidad -- ADR-0007, `.scratch/announce-
    rapido` ticket 05). Misma resolución que `telefono_notificacion_
    ocupante` (propia, o si no la del Principal), pero devuelve la Persona
    COMPLETA, no solo el Teléfono -- a diferencia del contacto de
    notificación del Destinatario (columna `recipient_phone`, estrictamente
    Teléfono), el Anunciante SÍ puede identificarse por WhatsApp.

    `None` si ni `ocupante` ni el Principal de su unidad tienen Persona
    propia todavía (ej. el primer residente de la unidad sigue `pending`,
    sin confirmar como Principal) -- en ese caso no hay ninguna identidad
    real con la cual anunciar; quien llama debe manejarlo (no se puede
    anunciar todavía para ese Ocupante)."""
    return _persona_de_ocupante_o_principal(session, ocupante)


MAX_OCUPANTES_ACTIVOS = 10

# Mismo mensaje tanto si lo atrapa el chequeo de aplicación
# (`_persona_ya_es_ocupante_activo`, el caso normal) como si lo atrapa el
# índice único de BD (`uq_ocupantes_persona_activo`, solo bajo una carrera
# real) -- quien llama nunca necesita distinguir cuál de los dos disparó.
MENSAJE_YA_OCUPANTE_ACTIVO = (
    "Este teléfono ya es Ocupante activo -- debe darse de baja antes de "
    "asociarse de nuevo."
)


def asociar_telefono_a_ocupante(
    session: Session, ocupante: Ocupante, telefono: str
) -> Ocupante:
    """Asocia `telefono` a un `ocupante` que hoy no tiene teléfono propio —
    resuelve/crea la Persona (`get_or_create_persona`) y liga `persona_id`.

    Raises:
        ValueError: si `ocupante` ya tiene teléfono propio, o si el teléfono
            ya es Ocupante activo (de este mismo Apartamento o de otro).
    """
    if ocupante.persona_id is not None:
        raise ValueError("Este Ocupante ya tiene un teléfono asociado.")

    persona = get_or_create_persona(session, telefono, ocupante.nombre)
    if _persona_ya_es_ocupante_activo(session, persona.id):
        raise ValueError(MENSAJE_YA_OCUPANTE_ACTIVO)

    ocupante.persona_id = persona.id
    # Mantiene apartamento_actual_id en sincronía (ver agregar_ocupante) --
    # ahora esta Persona SÍ puede loguearse y anunciar por sí misma, con el
    # Apartamento correcto resuelto en su snapshot.
    persona.apartamento_actual_id = ocupante.apartamento_id
    try:
        session.flush()
    except IntegrityError:
        # Carrera real: otra transacción activó este mismo teléfono como
        # Ocupante entre el chequeo de arriba y este flush -- el índice
        # único de BD (uq_ocupantes_persona_activo) la atrapó.
        session.rollback()
        raise ValueError(MENSAJE_YA_OCUPANTE_ACTIVO)
    return ocupante


def editar_telefono_ocupante(session: Session, ocupante: Ocupante, nuevo_telefono: str) -> Ocupante:
    """Cambia el teléfono de un `ocupante` no-principal que YA tiene uno,
    re-ligando `persona_id` a la Persona del nuevo teléfono
    (`get_or_create_persona`) -- sin tocar la Persona anterior, que sigue
    existiendo (pedido del cliente, `.scratch/pendientes-cliente/issues/35`).

    El teléfono del PRINCIPAL no se edita por acá -- ver
    `persona_service.cambiar_telefono_propio`, que además cierra sesión y
    exige re-verificación OTP (es su propia identidad de login, no un slot
    que gestiona sobre otra Persona).

    Raises:
        ValueError: si `ocupante` es el principal, si todavía no tiene
            teléfono (usar `asociar_telefono_a_ocupante`), o si el nuevo
            teléfono ya es Ocupante activo (de este mismo Apartamento o de otro).
    """
    if ocupante.es_principal:
        raise ValueError(
            "El teléfono del principal se edita desde 'Datos personales', no acá."
        )
    if ocupante.persona_id is None:
        raise ValueError("Este Ocupante todavía no tiene teléfono -- usa 'Asociar'.")

    persona = get_or_create_persona(session, nuevo_telefono, ocupante.nombre)
    if persona.id == ocupante.persona_id:
        return ocupante  # mismo teléfono, sin cambios reales

    if _persona_ya_es_ocupante_activo(session, persona.id):
        raise ValueError(MENSAJE_YA_OCUPANTE_ACTIVO)

    ocupante.persona_id = persona.id
    persona.apartamento_actual_id = ocupante.apartamento_id
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ValueError(MENSAJE_YA_OCUPANTE_ACTIVO)
    return ocupante


def desvincular_telefono_ocupante(session: Session, ocupante: Ocupante) -> Ocupante:
    """Quita el teléfono de `ocupante` — sigue existiendo como registro
    liviano (solo nombre), sin poder loguearse ni anunciar por sí mismo.

    Raises:
        ValueError: si `ocupante` es el principal (el principal SIEMPRE debe
            tener teléfono — promové a otro primero).
    """
    if ocupante.es_principal:
        raise ValueError(
            "El teléfono del principal no puede desvincularse directamente "
            "-- promové a otro Ocupante con teléfono primero."
        )

    if ocupante.persona_id is not None:
        persona = session.get(Persona, ocupante.persona_id)
        if persona is not None and persona.apartamento_actual_id == ocupante.apartamento_id:
            persona.apartamento_actual_id = None

    ocupante.persona_id = None
    session.flush()
    return ocupante


def asociar_whatsapp_a_ocupante(
    session: Session, ocupante: Ocupante, whatsapp_usuario: str
) -> Ocupante:
    """Asocia `whatsapp_usuario` a un `ocupante` que hoy no tiene contacto
    propio -- mismo patrón que `asociar_telefono_a_ocupante`, resuelto por
    WhatsApp en vez de Teléfono (`.scratch/ocupante-principal-escenarios`,
    ticket 06).

    Raises:
        ValueError: si `ocupante` ya tiene un contacto propio asociado (sea
            Teléfono o WhatsApp), o si el WhatsApp ya es Ocupante activo (de
            este mismo Apartamento o de otro).
    """
    if ocupante.persona_id is not None:
        raise ValueError("Este Ocupante ya tiene un contacto asociado.")

    persona = get_or_create_persona_por_whatsapp(session, whatsapp_usuario, ocupante.nombre)
    if _persona_ya_es_ocupante_activo(session, persona.id):
        raise ValueError(MENSAJE_YA_OCUPANTE_ACTIVO)

    ocupante.persona_id = persona.id
    persona.apartamento_actual_id = ocupante.apartamento_id
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ValueError(MENSAJE_YA_OCUPANTE_ACTIVO)
    return ocupante


def editar_whatsapp_ocupante(
    session: Session, ocupante: Ocupante, nuevo_whatsapp_usuario: str
) -> Ocupante:
    """Cambia el WhatsApp de un `ocupante` no-principal que YA tiene
    contacto propio por WhatsApp, re-ligando `persona_id` a la Persona del
    nuevo usuario (`get_or_create_persona_por_whatsapp`) -- mismo patrón
    que `editar_telefono_ocupante`.

    Raises:
        ValueError: si `ocupante` es el principal, si todavía no tiene
            contacto propio (usar `asociar_whatsapp_a_ocupante`), o si el
            nuevo WhatsApp ya es Ocupante activo (de este mismo Apartamento
            o de otro).
    """
    if ocupante.es_principal:
        raise ValueError(
            "El WhatsApp del principal se edita desde 'Datos personales', no acá."
        )
    if ocupante.persona_id is None:
        raise ValueError("Este Ocupante todavía no tiene contacto -- usa 'Asociar'.")

    persona = get_or_create_persona_por_whatsapp(session, nuevo_whatsapp_usuario, ocupante.nombre)
    if persona.id == ocupante.persona_id:
        return ocupante  # mismo WhatsApp, sin cambios reales

    if _persona_ya_es_ocupante_activo(session, persona.id):
        raise ValueError(MENSAJE_YA_OCUPANTE_ACTIVO)

    ocupante.persona_id = persona.id
    persona.apartamento_actual_id = ocupante.apartamento_id
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ValueError(MENSAJE_YA_OCUPANTE_ACTIVO)
    return ocupante


def desvincular_whatsapp_ocupante(session: Session, ocupante: Ocupante) -> Ocupante:
    """Quita el contacto de `ocupante` (asociado por WhatsApp) — sigue
    existiendo como registro liviano (solo nombre), sin poder loguearse ni
    anunciar por sí mismo. Mismo patrón que `desvincular_telefono_ocupante`.

    Raises:
        ValueError: si `ocupante` es el principal (el principal SIEMPRE debe
            tener contacto propio — promové a otro primero).
    """
    if ocupante.es_principal:
        raise ValueError(
            "El WhatsApp del principal no puede desvincularse directamente "
            "-- promové a otro Ocupante con contacto primero."
        )

    if ocupante.persona_id is not None:
        persona = session.get(Persona, ocupante.persona_id)
        if persona is not None and persona.apartamento_actual_id == ocupante.apartamento_id:
            persona.apartamento_actual_id = None

    ocupante.persona_id = None
    session.flush()
    return ocupante


def agregar_ocupante(
    session: Session,
    apartamento: Apartamento,
    nombre: str,
    telefono: str = None,
    whatsapp_usuario: str = None,
) -> Ocupante:
    """Agrega un Ocupante ACTIVO pero PENDING a `apartamento`
    (`.scratch/apartamento-catalogo-confirmacion`, ticket 06).

    Con `telefono` o `whatsapp_usuario` (ADR-0007, `.scratch/announce-rapido`
    ticket 02), reutiliza o crea la Persona correspondiente y liga
    `persona_id` -- si se pasan los dos, la Persona se resuelve por
    `telefono` (identidad más capaz: habilita login/notificaciones,
    `whatsapp_usuario` no) y el WhatsApp se agrega a esa misma Persona sin
    perderse (a menos que ya tuviera uno propio, que no se pisa). Sin
    ninguno de los dos, crea un registro liviano (solo nombre) que no puede
    loguearse ni anunciar por sí mismo.

    TODO Ocupante nuevo nace `pending` (`confirmado_en=None`) y sin
    `es_principal`, sin excepción -- incluido el primero de un Apartamento
    vacío. La promoción a principal ya no es automática: ocurre recién al
    confirmar (`confirmar_ocupante`), nunca al crear. Sí se mantiene la
    exigencia de que el primer Ocupante de un Apartamento vacío tenga
    Teléfono o WhatsApp (`ValueError` si no) -- sin eso, cuando llegue su
    confirmación no habría forma de que se vuelva principal (`confirmar_
    ocupante` exige una Persona real para promover).

    Con `telefono`/`whatsapp_usuario`, además, esa Persona NO puede ya ser
    Ocupante activo de OTRO Apartamento (una Persona, un apartamento activo
    a la vez) — debe darse de baja allá primero.

    Si el contacto YA es una Persona conocida (registrada antes de esta
    llamada, sea o no Ocupante activo en este momento), el `nombre` que
    queda guardado en el Ocupante es el YA REGISTRADO de esa Persona, no el
    `nombre` recibido acá (conversación 2026-08-16, pedido explícito) —
    evita que la misma Persona termine mostrando nombres distintos según
    qué Ocupante se mire. Para registrarla con un nombre distinto hay que
    desvincularla primero (`dar_de_baja_ocupante`) y darla de alta de nuevo.

    Args:
        session: sesión de SQLAlchemy activa.
        apartamento: el Apartamento al que se agrega.
        nombre: nombre del Ocupante -- SOLO se usa si `telefono`/
            `whatsapp_usuario` no resuelven a una Persona ya existente; si
            resuelven, se ignora a favor del nombre ya registrado (ver
            arriba).
        telefono: teléfono del Ocupante (cualquier formato), o ``None``.
        whatsapp_usuario: usuario de WhatsApp del Ocupante (con o sin `@`
            inicial), o ``None`` -- se ignora si `telefono` también viene.

    Returns:
        El Ocupante recién creado.

    Raises:
        ValueError: si es el primer Ocupante activo del Apartamento y no
            trae Teléfono ni WhatsApp; si esa Persona ya es Ocupante activo
            (de este mismo Apartamento o de otro); si el Apartamento ya
            tiene el máximo de `MAX_OCUPANTES_ACTIVOS` Ocupantes activos; o
            si `whatsapp_usuario` no cumple las reglas de username.
    """
    # `SELECT ... FOR UPDATE` sobre el Apartamento ANTES de contar (carrera
    # encontrada en auditoría, `.scratch/pendientes-cliente`): sin este lock,
    # dos `agregar_ocupante` concurrentes para el MISMO Apartamento pueden
    # leer el mismo conteo (ninguno ve la fila que el otro está por insertar
    # -- "phantom read" clásico) y ambos pasar el chequeo de
    # `MAX_OCUPANTES_ACTIVOS`, superando el límite. El lock serializa: la
    # segunda transacción espera a que la primera confirme (o revierta) antes
    # de contar, así que siempre cuenta sobre datos ya consistentes. No
    # afecta lecturas normales (`listar_ocupantes` sin este lock sigue
    # sirviendo GET /mis-datos, etc. sin bloquear nada).
    session.query(Apartamento).filter(Apartamento.id == apartamento.id).with_for_update().one()

    tiene_telefono = bool((telefono or "").strip())
    tiene_whatsapp = bool((whatsapp_usuario or "").strip())

    activos = listar_ocupantes(session, apartamento)
    es_el_primero = not activos
    if es_el_primero and not (tiene_telefono or tiene_whatsapp):
        raise ValueError(
            "El primer Ocupante de un Apartamento debe tener Teléfono o "
            "WhatsApp (lo necesitará para poder quedar como principal al "
            "confirmarse)."
        )
    if len(activos) >= MAX_OCUPANTES_ACTIVOS:
        raise ValueError(
            f"Este Apartamento ya tiene el máximo de {MAX_OCUPANTES_ACTIVOS} "
            "Ocupantes activos."
        )

    # Identidad ya registrada manda sobre el nombre recién tecleado
    # (conversación 2026-08-16, pedido explícito del cliente): si el
    # contacto YA es una Persona conocida (encontrada por lectura ANTES de
    # `get_or_create_persona*`, que también la buscaría pero además podría
    # crearla), el nombre que se guarda es el registrado (`persona.nombre`),
    # no el de este `nombre` -- mismo principio que ya aplicaba `mover_
    # ocupante` (ticket 11) para el caso de moverse de unidad, generalizado
    # acá a CUALQUIER alta que reutilice un contacto ya conocido, esté o no
    # actualmente activo en alguna unidad. Evita que la misma Persona
    # termine mostrando nombres distintos según qué Ocupante se mire (la
    # inconsistencia raíz investigada al inicio de esta conversación) --
    # para registrar ese contacto con un nombre distinto hay que desvincular
    # primero al Ocupante que ya lo tiene, nunca renombrar por la puerta de
    # atrás de un alta nueva.
    persona = None
    if tiene_telefono:
        ya_existia = buscar_persona_por_telefono(session, telefono) is not None
        persona = get_or_create_persona(session, telefono, nombre)
        if ya_existia:
            nombre = persona.nombre
        # Si además vino whatsapp_usuario, no se descarta -- se agrega a la
        # misma Persona (sin pisar uno que ya tuviera, mismo criterio de "no
        # tocar lo no enviado" que ya usa `update_datos_personales`).
        if tiene_whatsapp and not persona.whatsapp_usuario:
            update_datos_personales(session, persona, whatsapp_usuario=whatsapp_usuario)
    elif tiene_whatsapp:
        ya_existia = buscar_persona_por_whatsapp(session, whatsapp_usuario) is not None
        persona = get_or_create_persona_por_whatsapp(session, whatsapp_usuario, nombre)
        if ya_existia:
            nombre = persona.nombre

    if persona is not None:
        if _persona_ya_es_ocupante_activo(session, persona.id):
            raise ValueError(MENSAJE_YA_OCUPANTE_ACTIVO)
        # Mantiene `apartamento_actual_id` en sincronía con el roster de
        # Ocupantes -- otros consumidores (p.ej. `paquete_service.announce`,
        # que resuelve el snapshot del apartamento a partir de este campo)
        # dependen de él, no solo la vista de `/mis-datos`.
        persona.apartamento_actual_id = apartamento.id

    ocupante = Ocupante(
        apartamento_id=apartamento.id,
        persona_id=persona.id if persona is not None else None,
        nombre=normalizar_nombre(nombre),
        es_principal=False,
    )
    session.add(ocupante)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ValueError(MENSAJE_YA_OCUPANTE_ACTIVO)
    return ocupante


def dar_de_baja_ocupante(session: Session, ocupante: Ocupante) -> Ocupante:
    """Da de baja a `ocupante`: marca `desvinculado_en` (ahora), NUNCA borra
    la fila — sus datos quedan de solo consulta (histórico), igual que
    `anonimizar_persona`/ADR-0001 nunca reescribe ni borra historia real.

    Si `ocupante` es el principal de su Apartamento, exige que sea el ÚNICO
    Ocupante ACTIVO restante — si hay otros Ocupantes activos, primero hay
    que promover a alguno de ellos (`promover_a_principal`) o darles de baja
    a todos, antes de que el principal pueda darse de baja él mismo.

    Idempotente: si ya estaba dado de baja, no hace nada (no reescribe la
    fecha original de baja).

    Raises:
        ValueError: si es principal y quedan otros Ocupantes activos en el
            mismo Apartamento.
    """
    if ocupante.desvinculado_en is not None:
        return ocupante

    if ocupante.es_principal:
        otro_activo = (
            session.query(Ocupante)
            .filter(
                Ocupante.apartamento_id == ocupante.apartamento_id,
                Ocupante.id != ocupante.id,
                Ocupante.desvinculado_en.is_(None),
            )
            .first()
        )
        if otro_activo is not None:
            raise ValueError(
                "El principal no puede darse de baja mientras existan otros "
                "Ocupantes activos -- promové a alguno primero, o dales de "
                "baja a todos antes de darte de baja tú."
            )

    ocupante.desvinculado_en = _utcnow()
    if ocupante.persona_id is not None:
        # Sincroniza apartamento_actual_id (ver agregar_ocupante) -- ya no
        # vive ahí, así que un anuncio nuevo tampoco debe resolver ese
        # Apartamento como su contexto de entrega.
        persona = session.get(Persona, ocupante.persona_id)
        if persona is not None and persona.apartamento_actual_id == ocupante.apartamento_id:
            persona.apartamento_actual_id = None
    session.flush()
    return ocupante


def _puede_confirmar(session: Session, ocupante: Ocupante, actor) -> bool:
    if isinstance(actor, Usuario):
        # Staff -- ADMIN u OPERADOR, sin distinción (mismo patrón que el
        # resto de gestión de Ocupantes).
        return True
    if isinstance(actor, Persona):
        mi_ocupante = ocupante_activo_de_persona(session, actor.id)
        return (
            mi_ocupante is not None
            and mi_ocupante.es_principal
            and mi_ocupante.apartamento_id == ocupante.apartamento_id
        )
    return False


def confirmar_ocupante(session: Session, ocupante: Ocupante, actor) -> Ocupante:
    """Confirma un Ocupante `pending`: marca `confirmado_en` y, si su
    Apartamento no tiene ningún principal todavía, lo promueve en la misma
    operación (reemplaza la auto-promoción que antes pasaba en
    `agregar_ocupante` -- ahora ocurre al confirmar, no al crear).

    Solo puede confirmar el Ocupante principal YA CONFIRMADO del mismo
    Apartamento (una `Persona`), o cualquier `Usuario` de staff.

    Args:
        session: sesión de SQLAlchemy activa.
        ocupante: el Ocupante `pending` a confirmar.
        actor: quién confirma -- una `Persona` (debe ser el principal del
            mismo Apartamento) o un `Usuario` (staff, cualquier rol).

    Returns:
        El Ocupante confirmado.

    Raises:
        PermissionError: si `actor` no tiene permiso para confirmar este
            Ocupante.
        ValueError: si `ocupante` ya estaba confirmado, o si es el primero
            de su Apartamento pero no tiene Persona propia (ni Teléfono ni
            WhatsApp, ADR-0007 -- no puede quedar como principal).
    """
    if not _puede_confirmar(session, ocupante, actor):
        raise PermissionError("No tienes permiso para confirmar este Ocupante.")
    if ocupante.confirmado_en is not None:
        raise ValueError("Este Ocupante ya está confirmado.")

    # Issue 166 (.scratch/pendientes-cliente): bug real -- sin filtrar
    # `desvinculado_en IS NULL`, esta consulta encontraba a un Principal
    # VIEJO (ya dado de baja hace tiempo, la fila queda de solo consulta
    # pero conserva `es_principal=True` -- nunca se limpia al irse) y creía
    # que la unidad "ya tenía Principal" para siempre, aunque llevara meses
    # completamente vacía. Un Ocupante nuevo quedaba confirmado pero nunca
    # promovido, sin ningún error visible que lo explicara.
    hay_principal = (
        session.query(Ocupante)
        .filter(
            Ocupante.apartamento_id == ocupante.apartamento_id,
            Ocupante.es_principal.is_(True),
            Ocupante.desvinculado_en.is_(None),
        )
        .first()
        is not None
    )
    if not hay_principal:
        if ocupante.persona_id is None:
            raise ValueError(
                "El primer Ocupante confirmado de un Apartamento debe tener "
                "Teléfono o WhatsApp (queda como principal automáticamente)."
            )
        ocupante.es_principal = True

    ocupante.confirmado_en = _utcnow()
    session.flush()
    return ocupante


def promover_a_principal(session: Session, ocupante: Ocupante) -> Ocupante:
    """Promueve `ocupante` a principal de su Apartamento, degradando al
    principal anterior (si había uno) en la misma transacción.

    Si `ocupante` todavía estaba `pending`, queda confirmado en el mismo
    acto (`.scratch/ocupante-principal-escenarios`, ticket 03) -- promover
    (por cualquier vía: el botón explícito, o la promoción automática al
    recibir un paquete) nunca debe dejar a alguien `es_principal=True` sin
    `confirmado_en`. Si ya estaba confirmado, esa fecha original no se toca.

    Raises:
        ValueError: si `ocupante` no tiene `persona_id` (sin Teléfono ni
            WhatsApp propios, ADR-0007, no puede ser principal), o si ya
            está dado de baja.
    """
    if ocupante.persona_id is None:
        raise ValueError(
            "Un Ocupante sin Teléfono ni WhatsApp propios no puede "
            "promoverse a principal."
        )
    if ocupante.desvinculado_en is not None:
        raise ValueError(
            "Un Ocupante dado de baja no puede promoverse a principal."
        )

    # Issue 167 (.scratch/pendientes-cliente): mismo bug de [[166]] --
    # sin `desvinculado_en IS NULL`, esta consulta podía encontrar MÁS DE
    # UN Principal histórico (uno activo + uno viejo ya desvinculado, cuya
    # fila conserva `es_principal=True` como registro) y reventaba con
    # `MultipleResultsFound` en vez de encontrar "ninguno o uno". Antes del
    # fix del índice único en [[166]], la propia base de datos hacía
    # IMPOSIBLE que existiera más de una fila `es_principal=True` por
    # unidad (activa o no) -- así que este bug quedaba tapado por
    # accidente; con el índice ya corregido para permitir historial, hace
    # falta este mismo filtro acá también.
    anterior = (
        session.query(Ocupante)
        .filter(
            Ocupante.apartamento_id == ocupante.apartamento_id,
            Ocupante.es_principal.is_(True),
            Ocupante.desvinculado_en.is_(None),
            Ocupante.id != ocupante.id,
        )
        .one_or_none()
    )
    if anterior is not None:
        anterior.es_principal = False
        session.flush()  # libera el índice único parcial antes de marcar el nuevo

    ocupante.es_principal = True
    if ocupante.confirmado_en is None:
        ocupante.confirmado_en = _utcnow()
    session.flush()
    return ocupante


def resolver_ocupante_de_paquete(session: Session, paquete: Paquete) -> Ocupante | None:
    """El Ocupante que corresponde al destinatario de `paquete` -- `Paquete`
    nunca guarda una FK a `Ocupante` (ADR-0001, solo el snapshot congelado),
    así que hay que resolverlo desde los datos que sí quedaron.

    Dos intentos, en orden: (1) el roster de la unidad del snapshot
    (`snapshot_conjunto/torre/apartamento`), buscando un Ocupante cuyo
    nombre coincida con `recipient_name` -- mismo patrón que ya usa
    `paquete_service._resolver_ocupante_por_nombre` para un caso análogo;
    (2) si no hay snapshot de apartamento (o ningún nombre calza ahí),
    `recipient_phone` -> la Persona dueña de ese Teléfono -> su Ocupante
    activo. `None` si ninguno de los dos resuelve.

    Nombre PRIMERO, no teléfono -- a propósito (`.scratch/ocupante-
    principal-escenarios`, ticket 10): desde que `recipient_phone` puede
    ser el Teléfono de quien ANUNCIÓ (no del destinatario, cuando este
    último no tiene contacto propio), resolver por teléfono primero
    identificaría erróneamente al Anunciante como si fuera el destinatario.
    `recipient_name` siempre identifica a quien el paquete realmente nombra,
    sin importar de quién sea el contacto de notificación."""
    if (
        paquete.snapshot_conjunto
        and paquete.snapshot_torre
        and paquete.snapshot_apartamento
        and paquete.recipient_name
    ):
        apartamento = buscar_apartamento_por_terna(
            session, paquete.snapshot_conjunto, paquete.snapshot_torre, paquete.snapshot_apartamento
        )
        if apartamento is not None:
            nombre_normalizado = normalizar_nombre(paquete.recipient_name)
            for ocupante in listar_ocupantes(session, apartamento):
                if ocupante.nombre == nombre_normalizado:
                    return ocupante

    if paquete.recipient_phone:
        persona = buscar_persona_por_telefono(session, paquete.recipient_phone)
        if persona is not None:
            ocupante = ocupante_activo_de_persona(session, persona.id)
            if ocupante is not None:
                return ocupante

    return None


def promover_al_recibir(session: Session, paquete: Paquete) -> Ocupante | None:
    """Dispara la promoción automática a principal (`.scratch/ocupante-
    principal-escenarios`, ticket 04): si se puede resolver el Ocupante
    destinatario de `paquete` (`resolver_ocupante_de_paquete`), su unidad
    todavía no tiene principal, y ese Ocupante tiene Persona propia -- se
    promueve en el momento. Llamada por `paquete_lifecycle.receive()`
    después de una transición exitosa; nunca falla ni bloquea el recibo en
    sí -- si no se puede resolver nada, o el resuelto no tiene contacto
    propio, la unidad simplemente se queda sin principal hasta que alguien
    con contacto reciba algo.

    Returns:
        El Ocupante promovido, o `None` si no se disparó ninguna promoción.
    """
    ocupante = resolver_ocupante_de_paquete(session, paquete)
    if ocupante is None or ocupante.persona_id is None:
        return None

    # Issue 166 (.scratch/pendientes-cliente): mismo bug que `confirmar_
    # ocupante` -- sin `desvinculado_en IS NULL`, un Principal viejo (ya
    # dado de baja) bloqueaba esta promoción para siempre, aunque la unidad
    # llevara meses vacía.
    hay_principal = (
        session.query(Ocupante)
        .filter(
            Ocupante.apartamento_id == ocupante.apartamento_id,
            Ocupante.es_principal.is_(True),
            Ocupante.desvinculado_en.is_(None),
        )
        .first()
        is not None
    )
    if hay_principal:
        return None

    return promover_a_principal(session, ocupante)


def hay_otro_ocupante_activo(session: Session, apartamento_id, ocupante_id) -> bool:
    """¿Queda algún OTRO Ocupante activo (`ocupante_id` aparte) en
    `apartamento_id`? (issue 69) -- misma condición que ya evaluaba en
    silencio el guard de reasignación de `/residentes/{id}/apartamento`
    (`customers_manage_asignar_apartamento`); expuesta acá para poder
    avisarle al staff ANTES de que intente guardar y le rebote el error,
    no solo después."""
    return (
        session.query(Ocupante)
        .filter(
            Ocupante.apartamento_id == apartamento_id,
            Ocupante.id != ocupante_id,
            Ocupante.desvinculado_en.is_(None),
        )
        .first()
        is not None
    )


def ocupante_activo_por_contacto(
    session: Session, telefono: str = None, whatsapp_usuario: str = None
) -> Ocupante | None:
    """El Ocupante activo (si hay) de la Persona identificada por
    `telefono` o `whatsapp_usuario` -- para cuando `agregar_ocupante`
    rechaza por "ya es Ocupante activo" y hace falta saber de qué unidad
    es y si es principal ahí, para ofrecer "Mover acá" en vez de solo
    bloquear (`.scratch/ocupante-principal-escenarios`, ticket 12)."""
    persona = None
    if telefono:
        persona = buscar_persona_por_telefono(session, telefono)
    elif whatsapp_usuario:
        persona = buscar_persona_por_whatsapp(session, whatsapp_usuario)
    if persona is None:
        return None
    return ocupante_activo_de_persona(session, persona.id)


def mensaje_ya_ocupante_activo(session: Session, conflicto: Ocupante) -> str:
    """Mensaje enriquecido para cuando un contacto ya es Ocupante activo de
    otra unidad -- incluye Torre+Apartamento (`.scratch/ocupante-principal-
    escenarios`, ticket 12) en vez de solo bloquear con el mensaje genérico
    de `MENSAJE_YA_OCUPANTE_ACTIVO`.

    Issue 159 (`.scratch/pendientes-cliente`): ya no distingue principal de
    no-principal -- `mover_ocupante` degrada automáticamente si hace falta
    (ver su docstring), así que "Mover acá" alcanza para los dos casos."""
    apto = session.get(Apartamento, conflicto.apartamento_id)
    unidad = f"{apto.torre} Apto {apto.apartamento}" if apto is not None else "otra unidad"
    if conflicto.es_principal:
        return (
            f'Ya es Ocupante PRINCIPAL de {unidad} -- marcá "Mover acá" para reubicarlo '
            "(se degrada automáticamente a otro Residente de esa unidad, o queda vacía "
            "si está solo)."
        )
    return f'Ya es Ocupante de {unidad} (no principal) -- marcá "Mover acá" para reubicarlo.'


def identificar_contacto_para_unidad(
    session: Session, contacto: str, apto_actual: Apartamento | None
) -> dict:
    """Vista previa en vivo de un `contacto` (Teléfono o WhatsApp) tecleado
    para "+ Nuevo residente"/"Agregar residente" -- issue 154
    (.scratch/pendientes-cliente): extraído de `nuevo_residente_identificar`
    (`packages.py`, único caller hasta ahora) para que `/residentes` tab
    Residentes reuse la MISMA lógica en vez de reimplementarla -- dos
    callers casi idénticos ya son un seam real, no uno hipotético.

    `apto_actual` es la unidad de referencia para decidir si un conflicto
    encontrado es "otra unidad" o la misma en la que se está agregando --
    cada caller la resuelve a su manera (`/paquetes`: snapshot del Paquete;
    `/residentes`: `Persona.apartamento_actual_id` de la ficha)-- por eso
    entra ya resuelta, esta función no sabe nada de Paquete ni de Persona
    "actual".

    Returns:
        `{"encontrado": False}` si `contacto` no corresponde a nadie
        registrado (vacío, a medio teclear, o simplemente nuevo).
        `{"encontrado": True, "nombre": "...", "conflicto": None}` si
        corresponde a una Persona conocida sin conflicto de unidad.
        `{"encontrado": True, "nombre": "...", "conflicto": {"es_principal":
        bool, "torre": "...", "apartamento": "...", "persona_id": "..."}}`
        si además ya es Ocupante activo de otra unidad."""
    tipo = clasificar_contacto(contacto)
    persona = None
    if tipo == "telefono":
        persona = buscar_persona_por_telefono(session, contacto)
    elif tipo == "whatsapp":
        persona = buscar_persona_por_whatsapp(session, contacto)
    if persona is None:
        return {"encontrado": False}

    conflicto_ocupante = ocupante_activo_por_contacto(
        session,
        telefono=contacto if tipo == "telefono" else None,
        whatsapp_usuario=contacto if tipo == "whatsapp" else None,
    )
    conflicto = None
    if conflicto_ocupante is not None and (
        apto_actual is None or conflicto_ocupante.apartamento_id != apto_actual.id
    ):
        conflicto_apto = session.get(Apartamento, conflicto_ocupante.apartamento_id)
        conflicto = {
            "es_principal": conflicto_ocupante.es_principal,
            "torre": conflicto_apto.torre if conflicto_apto else None,
            "apartamento": conflicto_apto.apartamento if conflicto_apto else None,
            "persona_id": str(persona.id),
        }

    return {"encontrado": True, "nombre": persona.nombre, "conflicto": conflicto}


def mover_ocupante(
    session: Session, ocupante: Ocupante, apartamento_destino: Apartamento
) -> Ocupante:
    """Mueve a `ocupante` (activo) de su unidad actual a
    `apartamento_destino` en un solo paso -- da de baja en la anterior y
    agrega en la nueva, sin el paso manual de "dar de baja" primero que
    hoy exige `agregar_ocupante` (`.scratch/ocupante-principal-
    escenarios`, ticket 11). Acción exclusiva de staff -- las 4 vistas que
    la exponen (tab Dirección, tab Residentes, `/announce` Torre+Apto
    nueva persona, Corregir destinatario) son todas de staff, nunca
    autoservicio.

    Issue 159 (`.scratch/pendientes-cliente`, revierte la restricción
    original de este mismo ticket 11): si `ocupante` es PRINCIPAL, staff
    con control total -- ya no hace falta promover a otro a mano primero.
    - Con otros Ocupantes activos en su unidad actual que tengan Teléfono o
      WhatsApp propio: se promueve automáticamente al más antiguo de ellos
      (`created_at` ascendente, mismo orden que `listar_ocupantes`) ANTES
      de mover a `ocupante` -- que llega a `apartamento_destino` como
      Residente normal, no Principal. La unidad vieja nunca queda sin
      Principal.
    - Solo en su unidad (sin otros Ocupantes activos): se mueve directo,
      la unidad vieja queda vacía -- no hay a quién degradar/promover.
    - Con otros Ocupantes activos pero NINGUNO con contacto propio: no hay
      a quién promover -- se rechaza (ver Raises). Degradar a alguien SIN
      Teléfono/WhatsApp violaría el invariante de que todo Principal
      necesita contacto propio (mismo invariante que ya exige
      `promover_a_principal`).

    Conserva el contacto que `ocupante` ya tenía (Teléfono y/o WhatsApp) --
    reutiliza la misma Persona (`agregar_ocupante` la resuelve por su
    contacto), nunca crea una nueva.

    Args:
        session: sesión de SQLAlchemy activa.
        ocupante: el Ocupante activo a mover.
        apartamento_destino: la unidad a la que se mueve.

    Returns:
        El nuevo Ocupante (fila nueva -- la anterior queda dada de baja,
        de solo consulta, mismo criterio que `dar_de_baja_ocupante`).

    Raises:
        ValueError: si `ocupante` ya está dado de baja; si es Principal y
            tiene compañeros de unidad activos pero ninguno con Teléfono ni
            WhatsApp propio para sucederlo; o si `apartamento_destino` ya
            tiene el máximo de Ocupantes activos (`agregar_ocupante` lo
            exige igual).
    """
    if ocupante.desvinculado_en is not None:
        raise ValueError("Este Ocupante ya está dado de baja.")

    if ocupante.es_principal:
        candidato = (
            session.query(Ocupante)
            .filter(
                Ocupante.apartamento_id == ocupante.apartamento_id,
                Ocupante.id != ocupante.id,
                Ocupante.desvinculado_en.is_(None),
                Ocupante.persona_id.isnot(None),
            )
            .order_by(Ocupante.created_at.asc())
            .first()
        )
        if candidato is not None:
            promover_a_principal(session, candidato)  # degrada a `ocupante` en el acto
        elif hay_otro_ocupante_activo(session, ocupante.apartamento_id, ocupante.id):
            raise ValueError(
                "Es Principal y ninguno de los otros Residentes de su unidad actual "
                "tiene Teléfono ni WhatsApp propio para sucederlo -- agregale contacto "
                "a alguno desde tab Residentes antes de moverlo."
            )
        # Si no hay ningún otro Ocupante activo, sigue de largo: se mueve
        # directo, sin nadie a quien degradar.

    nombre = ocupante.nombre
    telefono = None
    whatsapp_usuario = None
    if ocupante.persona_id is not None:
        persona = session.get(Persona, ocupante.persona_id)
        if persona is not None:
            telefono = persona.telefono
            whatsapp_usuario = persona.whatsapp_usuario

    dar_de_baja_ocupante(session, ocupante)
    return agregar_ocupante(
        session, apartamento_destino, nombre, telefono=telefono, whatsapp_usuario=whatsapp_usuario
    )


def reasignar_apartamento(
    session: Session, persona: Persona, apartamento: Apartamento | None, actor: Usuario
) -> Ocupante | None:
    """La cara de staff de "mudar" o "desvincular" a `persona` de un
    Apartamento (tab "Dirección" de `/residentes/{id}`, `.scratch/announce-
    residente-correcto` ticket 01) -- a diferencia de `apartamento_service.
    move_resident` (que solo tocaba `Persona.apartamento_actual_id` directo),
    ESTA función pasa siempre por el padrón de `Ocupante`, para que ese campo
    quede SIEMPRE derivado de él -- nunca un residente "fantasma", invisible
    para `listar_ocupantes` (y por lo tanto para el camino Torre+Apartamento
    de `/announce`).

    - `apartamento` es la unidad donde `persona` YA es Ocupante activo ->
      no-op, devuelve ese Ocupante tal cual (mismo comportamiento que el
      guard de "reasignar al mismo apartamento" que ya tenía la ruta).
    - `apartamento` es distinta (o `persona` no es Ocupante de ninguna
      unidad todavía) -> da de alta un Ocupante nuevo (`agregar_ocupante`,
      reusando la Persona por su Teléfono/WhatsApp). Issue 161 (`.scratch/
      pendientes-cliente`): si la unidad estaba VACÍA, se confirma en el
      mismo acto (`confirmar_ocupante`, `actor` = el `Usuario` de staff que
      hace la asignación) -- promueve a principal automáticamente, mismo
      comportamiento ya existente de `confirmar_ocupante`. Si la unidad YA
      tenía otro Ocupante activo, en cambio, el nuevo queda PENDING -- staff
      puede asignar la unidad, pero no salta el paso de confirmación (que
      el Principal de esa unidad, o cualquier staff, hace después) -- mismo
      criterio que ya usa `agregar_ocupante` desde tab Residentes.
    - `apartamento` es `None` -> desvincula: da de baja el Ocupante activo
      de `persona` si tiene uno (`dar_de_baja_ocupante`, que ya limpia
      `apartamento_actual_id` como efecto secundario); si no tiene Ocupante
      pero `apartamento_actual_id` seguía puesto (dato huérfano de antes de
      este ticket), lo limpia directo como respaldo.

    Args:
        session: sesión de SQLAlchemy activa.
        persona: la Persona a mudar o desvincular.
        apartamento: la unidad destino, o `None` para desvincular.
        actor: el `Usuario` de staff que hace la asignación (confirma el
            Ocupante nuevo en su nombre).

    Returns:
        El Ocupante resultante, o `None` si se desvinculó.

    Raises:
        ValueError: si `persona` ya es Ocupante activo de OTRA unidad
            (debe darse de baja allá primero -- mismo guard que ya aplica
            `agregar_ocupante`), o si la unidad destino ya tiene el máximo
            de Ocupantes activos.
    """
    mi_ocupante = ocupante_activo_de_persona(session, persona.id)

    if apartamento is None:
        if mi_ocupante is not None:
            dar_de_baja_ocupante(session, mi_ocupante)
        elif persona.apartamento_actual_id is not None:
            persona.apartamento_actual_id = None
            session.flush()
        return None

    if mi_ocupante is not None and mi_ocupante.apartamento_id == apartamento.id:
        return mi_ocupante

    # Issue 161 (.scratch/pendientes-cliente): confirmar automáticamente
    # (y por lo tanto promover a principal, `confirmar_ocupante`) SOLO si la
    # unidad estaba vacía -- si ya tenía a alguien, el nuevo Ocupante queda
    # pending, esperando que el Principal (o cualquier staff) lo confirme a
    # mano. Se evalúa ANTES de agregar (si no, el propio `nuevo` ya cuenta
    # como "otro Ocupante" y esto siempre daría `True`).
    unidad_ya_ocupada = hay_otro_ocupante_activo(session, apartamento.id, None)

    nuevo = agregar_ocupante(
        session, apartamento, persona.nombre,
        telefono=persona.telefono, whatsapp_usuario=persona.whatsapp_usuario,
    )
    if unidad_ya_ocupada:
        return nuevo
    return confirmar_ocupante(session, nuevo, actor)
