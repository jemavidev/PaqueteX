# -*- coding: utf-8 -*-
"""
Servicio de dominio de Apartamento y membresía actual (Seam A).

Dos invariantes del dominio:

  1. **Catálogo cerrado** — un Apartamento se identifica por su terna
     normalizada `(conjunto, torre, apartamento)`, pero el catálogo completo
     (804 unidades) se siembra UNA sola vez por migración
     (`.scratch/apartamento-catalogo-confirmacion`, ticket 02).
     `resolver_apartamento` ya NO crea: resuelve la terna contra ese catálogo
     y falla si no existe -- "creable sobre la marcha" (spec original de
     `.scratch/data-model`) quedó reemplazado por el catálogo cerrado
     (ticket 03). El Conjunto ya no lo pasa el llamador: es un único valor
     global (`configuracion_conjunto_service`).
  2. **Membresía mutable** — la Persona tiene UN Apartamento actual opcional;
     asignarlo/mudarlo/desvincularlo siempre está disponible y resuelve la
     Persona por su Teléfono canónico (igual que `get_or_create_persona`).
"""

import re

from sqlalchemy.orm import Session

from .apartamento import Apartamento, _normalizar_componente, normalizar_terna
from .configuracion_conjunto_service import obtener_nombre_conjunto
from .persona import Persona
from .persona_service import get_or_create_persona
from .telefono import normalizar_telefono

_NUMERO_TORRE = re.compile(r"\d+")


def _buscar_por_terna(
    session: Session, conjunto: str, torre: str, apartamento: str
):
    return (
        session.query(Apartamento)
        .filter(
            Apartamento.conjunto == conjunto,
            Apartamento.torre == torre,
            Apartamento.apartamento == apartamento,
        )
        .one_or_none()
    )


def resolver_apartamento(session: Session, torre: str, apartamento: str) -> Apartamento:
    """Resuelve una unidad del catálogo cerrado por `(torre, apartamento)` --
    el Conjunto es único y global (`configuracion_conjunto_service`), ya no
    lo pasa el llamador.

    Ya NO crea: el catálogo completo se siembra una sola vez por migración
    (ticket 02). Resolver una terna que no está ahí es un error de uso (typo,
    integración rota), no un caso a tolerar en silencio creando una unidad
    fantasma.

    Args:
        session: sesión de SQLAlchemy activa.
        torre: identificador de la torre/bloque.
        apartamento: identificador del apartamento/unidad.

    Returns:
        El Apartamento del catálogo.

    Raises:
        ValueError: si `torre`/`apartamento` son `None`/vacíos, o si la terna
            no existe en el catálogo.
    """
    torre_c = _normalizar_componente(torre, "torre")
    apartamento_c = _normalizar_componente(apartamento, "apartamento")
    conjunto_c = obtener_nombre_conjunto(session)

    apto = _buscar_por_terna(session, conjunto_c, torre_c, apartamento_c)
    if apto is None:
        raise ValueError(
            f"La unidad {torre_c} {apartamento_c} no existe en el catálogo del conjunto."
        )
    return apto


def buscar_apartamento_por_terna(
    session: Session, conjunto: str, torre: str, apartamento: str
) -> Apartamento | None:
    """Como `resolver_apartamento`, pero de solo lectura — `None` si la terna
    normalizada no tiene Apartamento (nunca falla, nunca crea). A diferencia
    de `resolver_apartamento` sigue recibiendo `conjunto` explícito (no
    cambia con el catálogo cerrado, ticket 03) porque su único consumidor
    (Grupo 16, candidatos de "Corregir") resuelve a partir del snapshot
    congelado de un Paquete, que SÍ guarda su propio `conjunto` de cuando se
    anunció."""
    conjunto_c, torre_c, apartamento_c = normalizar_terna(conjunto, torre, apartamento)
    return _buscar_por_terna(session, conjunto_c, torre_c, apartamento_c)


def listar_catalogo_por_torre(session: Session) -> dict[str, list[str]]:
    """El catálogo cerrado agrupado por Torre, para los pickers de Torre +
    Apartamento de `/mis-datos` (ticket 04) y `/announce-new` (ticket 05) --
    Torres en orden numérico (`TORRE 1`..`TORRE 10`, no alfabético: `TORRE
    10` alfabéticamente cae entre `TORRE 1` y `TORRE 2`), Apartamentos de
    cada Torre en orden numérico ascendente.
    """
    filas = session.query(Apartamento.torre, Apartamento.apartamento).all()
    agrupado: dict[str, list[str]] = {}
    for torre, apartamento in filas:
        agrupado.setdefault(torre, []).append(apartamento)

    def _clave_torre(torre: str) -> tuple:
        numero = _NUMERO_TORRE.search(torre)
        return (int(numero.group()), torre) if numero else (float("inf"), torre)

    return {
        torre: sorted(agrupado[torre], key=int)
        for torre in sorted(agrupado, key=_clave_torre)
    }


def set_apartamento_actual(
    session: Session, telefono: str, apartamento: Apartamento | None
) -> Persona:
    """Asigna (o desvincula) el Apartamento actual de una Persona.

    Resuelve la Persona por su Teléfono canónico (igual que
    `get_or_create_persona`). NO crea la Persona: este servicio no recibe nombre,
    de modo que la Persona debe existir previamente.

    Args:
        session: sesión de SQLAlchemy activa.
        telefono: teléfono de la Persona, en cualquier formato.
        apartamento: el Apartamento a asignar, o ``None`` para desvincular.

    Returns:
        La Persona con su `apartamento_actual_id` actualizado.

    Raises:
        LookupError: si no existe una Persona con ese Teléfono.
        ValueError: si el teléfono es ``None`` o no contiene dígitos.
    """
    telefono_canonico = normalizar_telefono(telefono)

    persona = (
        session.query(Persona)
        .filter(Persona.telefono == telefono_canonico)
        .one_or_none()
    )
    if persona is None:
        raise LookupError(
            f"No existe una Persona con el teléfono {telefono_canonico!r}; "
            "regístrela primero (get_or_create_persona)."
        )

    persona.apartamento_actual_id = apartamento.id if apartamento is not None else None
    session.flush()
    return persona


def move_resident(
    session: Session, telefono: str, apartamento: Apartamento | None
) -> Persona:
    """Muda —o desvincula si ``apartamento`` es ``None``— a una Persona.

    Es la cara MUTABLE de la membresía (glosario: mudarse / desvincularse) y el
    mecanismo que hace CORREGIBLE una herencia errónea (spec §Herencia). Mudar o
    desvincular NUNCA reescribe el snapshot de paquetes ya anunciados (ADR-0001):
    el snapshot es texto copiado, no un FK que siga a la Persona. Equivale a
    `set_apartamento_actual`, expuesto con el verbo de dominio de la mudanza.
    """
    return set_apartamento_actual(session, telefono, apartamento)


def declare_unit(
    session: Session, apartamento: Apartamento, miembros
) -> list[Persona]:
    """Declara una unidad a propósito: asigna `apartamento` como Apartamento
    actual a TODOS los miembros declarados a la vez — eso ES la herencia.

    `miembros` es un iterable de tuplas ``(telefono, nombre)``: cada uno se
    registra (get_or_create_persona) si no existía y hereda el Apartamento. El
    "grupo misma unidad" no es una entidad persistente: es justo este conjunto de
    Personas compartiendo `apartamento_actual` (ver CONTEXT.md). La herencia es
    CORREGIBLE después con `move_resident` sobre cualquier teléfono, sin afectar a
    los demás. Un "a nombre de" casual en `announce` NO pasa por aquí y por tanto
    NO agrupa a nadie.

    Args:
        session: sesión de SQLAlchemy activa.
        apartamento: el Apartamento de la unidad (resuélvelo del catálogo
            cerrado con `resolver_apartamento` antes de llamar).
        miembros: iterable de tuplas ``(telefono, nombre)``.

    Returns:
        La lista de Personas del grupo, con su `apartamento_actual` asignado.
    """
    personas: list[Persona] = []
    for telefono, nombre in miembros:
        persona = get_or_create_persona(session, telefono, nombre)
        persona.apartamento_actual_id = apartamento.id
        personas.append(persona)
    session.flush()
    return personas
