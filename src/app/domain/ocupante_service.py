# -*- coding: utf-8 -*-
"""
Servicio de dominio de Ocupante — padrón de residentes de un Apartamento con
Teléfono opcional (Seam A, ADR-0006).

Invariante: un Apartamento con al menos un Ocupante ACTIVO tiene SIEMPRE
exactamente uno marcado `es_principal`, y ese principal SIEMPRE tiene
`persona_id` (un Teléfono real). La base de datos lo garantiza con un índice
único parcial (`uq_ocupantes_principal_por_apartamento`); este módulo
garantiza que la aplicación nunca intente violarlo (promover exige
`persona_id`, y degrada al anterior en la misma transacción antes de marcar
el nuevo).

Invariante nueva (.scratch/mis-datos, ticket 02): una Persona (por Teléfono)
solo puede ser Ocupante ACTIVO de un Apartamento a la vez. Para unirse a
otro, primero debe darse de baja del actual (`dar_de_baja_ocupante`) — nunca
se borra la fila, queda de solo consulta (histórico).
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .apartamento import Apartamento
from .ocupante import Ocupante
from .persona import Persona
from .persona_service import get_or_create_persona
from .texto import normalizar_nombre


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


def _persona_activa_en_otro_apartamento(
    session: Session, persona_id, apartamento_id
) -> bool:
    return (
        session.query(Ocupante)
        .filter(
            Ocupante.persona_id == persona_id,
            Ocupante.apartamento_id != apartamento_id,
            Ocupante.desvinculado_en.is_(None),
        )
        .first()
        is not None
    )


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


def telefono_notificacion_ocupante(session: Session, ocupante: Ocupante) -> str | None:
    """El teléfono al que le debe llegar un aviso a nombre de `ocupante`: el
    propio si tiene, o si no, el del principal ACTIVO de su Apartamento EN
    ESE MOMENTO (.scratch/mis-datos, ticket 08) -- lo usan tanto
    `paquete_service.announce` (se congela en el Paquete al anunciar,
    ADR-0001, nunca se re-resuelve después) como "Corregir destinatario"
    (ticket 09) al declarar un Ocupante nuevo."""
    if ocupante.persona_id is not None:
        persona = session.get(Persona, ocupante.persona_id)
        return persona.telefono if persona is not None else None

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


MAX_OCUPANTES_ACTIVOS = 5


def asociar_telefono_a_ocupante(
    session: Session, ocupante: Ocupante, telefono: str
) -> Ocupante:
    """Asocia `telefono` a un `ocupante` que hoy no tiene teléfono propio —
    resuelve/crea la Persona (`get_or_create_persona`) y liga `persona_id`.

    Raises:
        ValueError: si `ocupante` ya tiene teléfono propio, o si el teléfono
            ya es Ocupante activo de OTRO Apartamento.
    """
    if ocupante.persona_id is not None:
        raise ValueError("Este Ocupante ya tiene un teléfono asociado.")

    persona = get_or_create_persona(session, telefono, ocupante.nombre)
    if _persona_activa_en_otro_apartamento(session, persona.id, ocupante.apartamento_id):
        raise ValueError(
            "Este teléfono ya es Ocupante activo de otro Apartamento -- debe "
            "darse de baja allá antes de asociarse a uno nuevo."
        )

    ocupante.persona_id = persona.id
    # Mantiene apartamento_actual_id en sincronía (ver agregar_ocupante) --
    # ahora esta Persona SÍ puede loguearse y anunciar por sí misma, con el
    # Apartamento correcto resuelto en su snapshot.
    persona.apartamento_actual_id = ocupante.apartamento_id
    session.flush()
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
            teléfono ya es Ocupante activo de OTRO Apartamento.
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

    if _persona_activa_en_otro_apartamento(session, persona.id, ocupante.apartamento_id):
        raise ValueError(
            "Este teléfono ya es Ocupante activo de otro Apartamento -- debe "
            "darse de baja allá antes de asociarse a uno nuevo."
        )

    ocupante.persona_id = persona.id
    persona.apartamento_actual_id = ocupante.apartamento_id
    session.flush()
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


def agregar_ocupante(
    session: Session, apartamento: Apartamento, nombre: str, telefono: str = None
) -> Ocupante:
    """Agrega un Ocupante ACTIVO a `apartamento`.

    Con `telefono`, reutiliza o crea la Persona correspondiente
    (`get_or_create_persona`) y liga `persona_id`. Sin `telefono`, crea un
    registro liviano (solo nombre) que no puede loguearse ni anunciar por sí
    mismo.

    Si `apartamento` no tiene NINGÚN Ocupante activo todavía, este primer
    Ocupante DEBE tener teléfono (`ValueError` si no) y queda marcado
    `es_principal` automáticamente — un Apartamento con Ocupantes activos
    siempre tiene un principal. Ocupantes agregados después nunca se
    auto-promueven (usar `promover_a_principal` explícitamente).

    Con `telefono`, además, esa Persona NO puede ya ser Ocupante activo de
    OTRO Apartamento (un teléfono, un apartamento activo a la vez) — debe
    darse de baja allá primero.

    Args:
        session: sesión de SQLAlchemy activa.
        apartamento: el Apartamento al que se agrega.
        nombre: nombre del Ocupante.
        telefono: teléfono del Ocupante (cualquier formato), o ``None``.

    Returns:
        El Ocupante recién creado.

    Raises:
        ValueError: si es el primer Ocupante activo del Apartamento y no
            trae teléfono; si el teléfono ya es Ocupante activo de otro
            Apartamento; o si el Apartamento ya tiene el máximo de
            `MAX_OCUPANTES_ACTIVOS` Ocupantes activos.
    """
    activos = listar_ocupantes(session, apartamento)
    es_el_primero = not activos
    if es_el_primero and not (telefono or "").strip():
        raise ValueError(
            "El primer Ocupante de un Apartamento debe tener Teléfono "
            "(queda como principal automáticamente)."
        )
    if len(activos) >= MAX_OCUPANTES_ACTIVOS:
        raise ValueError(
            f"Este Apartamento ya tiene el máximo de {MAX_OCUPANTES_ACTIVOS} "
            "Ocupantes activos."
        )

    persona = None
    if (telefono or "").strip():
        persona = get_or_create_persona(session, telefono, nombre)
        if _persona_activa_en_otro_apartamento(session, persona.id, apartamento.id):
            raise ValueError(
                "Esta Persona ya es Ocupante activo de otro Apartamento -- "
                "debe darse de baja allá antes de asociarse a uno nuevo."
            )
        # Mantiene `apartamento_actual_id` en sincronía con el roster de
        # Ocupantes -- otros consumidores (p.ej. `paquete_service.announce`,
        # que resuelve el snapshot del apartamento a partir de este campo)
        # dependen de él, no solo la vista de `/mis-datos`.
        persona.apartamento_actual_id = apartamento.id

    ocupante = Ocupante(
        apartamento_id=apartamento.id,
        persona_id=persona.id if persona is not None else None,
        nombre=normalizar_nombre(nombre),
        es_principal=es_el_primero,
    )
    session.add(ocupante)
    session.flush()
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


def promover_a_principal(session: Session, ocupante: Ocupante) -> Ocupante:
    """Promueve `ocupante` a principal de su Apartamento, degradando al
    principal anterior (si había uno) en la misma transacción.

    Raises:
        ValueError: si `ocupante` no tiene `persona_id` (sin Teléfono, no puede
            ser principal), o si ya está dado de baja.
    """
    if ocupante.persona_id is None:
        raise ValueError(
            "Un Ocupante sin Teléfono no puede promoverse a principal."
        )
    if ocupante.desvinculado_en is not None:
        raise ValueError(
            "Un Ocupante dado de baja no puede promoverse a principal."
        )

    anterior = (
        session.query(Ocupante)
        .filter(
            Ocupante.apartamento_id == ocupante.apartamento_id,
            Ocupante.es_principal.is_(True),
            Ocupante.id != ocupante.id,
        )
        .one_or_none()
    )
    if anterior is not None:
        anterior.es_principal = False
        session.flush()  # libera el índice único parcial antes de marcar el nuevo

    ocupante.es_principal = True
    session.flush()
    return ocupante
