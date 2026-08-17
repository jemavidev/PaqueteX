# -*- coding: utf-8 -*-
"""
Candidatos de "Corregir" — Grupo 16 de la Ronda 2
(`ajustes-post-referencia-funcional/REQUERIMIENTOS.md`).

`corregir_destinatario` (`paquete_lifecycle.py`) sigue aceptando cualquier
nombre/teléfono por texto libre — ESTA rebanada es la de lectura: qué
Personas/Ocupantes ya conocidos puede ofrecer la UI como candidatos, para que
el staff **seleccione** en vez de tipear. La decisión de exigir la selección
(cuando hay candidatos) vive en la capa web, no aquí — este servicio solo
resuelve la lista.
"""

from sqlalchemy import tuple_
from sqlalchemy.orm import Session

from .apartamento import Apartamento, normalizar_terna
from .apartamento_service import buscar_apartamento_por_terna
from .ocupante import Ocupante
from .ocupante_service import listar_ocupantes
from .paquete import Paquete
from .persona import Persona


def _construir_candidatos(ocupantes: list[Ocupante], telefono_de, anunciante: Persona | None) -> list[dict]:
    """Arma la lista de candidatos (dedup por `(nombre, teléfono)`, en ese
    orden: Ocupantes primero, Anunciante al final) a partir de datos YA
    resueltos -- compartida por `candidatos_correccion` (resuelve uno por
    uno) y `candidatos_correccion_por_paquetes` (todo pre-cargado en
    batch), para no duplicar la regla de negocio del dedup entre las dos.
    `telefono_de(ocupante)` resuelve el teléfono de cada Ocupante -- una
    función porque cada caller lo obtiene de una fuente distinta (una
    consulta puntual vs. un dict ya armado)."""
    vistos = set()
    candidatos = []

    def _agregar(nombre: str, telefono: str | None):
        nombre = (nombre or "").strip()
        if not nombre:
            return
        clave = (nombre, telefono)
        if clave in vistos:
            return
        vistos.add(clave)
        candidatos.append({"nombre": nombre, "telefono": telefono})

    for ocupante in ocupantes:
        _agregar(ocupante.nombre, telefono_de(ocupante))

    if anunciante is not None:
        _agregar(anunciante.nombre, anunciante.telefono)

    return candidatos


def candidatos_correccion(session: Session, paquete: Paquete) -> list[dict]:
    """Candidatos para corregir el destinatario de `paquete`: los Ocupantes
    del Apartamento del snapshot (si el paquete tiene uno resuelto) más el
    Anunciante mismo — únicos por `(nombre, teléfono)`, en ese orden.

    Cada candidato es `{"nombre": str, "telefono": str | None}`. Sin
    Apartamento en el snapshot, la lista trae solo al Anunciante (o queda
    vacía si ni siquiera eso resuelve, lo que no debería pasar en la
    práctica — `announced_by_persona_id` siempre existe)."""
    ocupantes = []
    if paquete.snapshot_conjunto and paquete.snapshot_torre and paquete.snapshot_apartamento:
        apto = buscar_apartamento_por_terna(
            session,
            paquete.snapshot_conjunto,
            paquete.snapshot_torre,
            paquete.snapshot_apartamento,
        )
        if apto is not None:
            ocupantes = listar_ocupantes(session, apto)

    def _telefono_de(ocupante: Ocupante) -> str | None:
        if ocupante.persona_id is None:
            return None
        persona = session.get(Persona, ocupante.persona_id)
        return persona.telefono if persona else None

    anunciante = session.get(Persona, paquete.announced_by_persona_id)
    return _construir_candidatos(ocupantes, _telefono_de, anunciante)


def candidatos_correccion_por_paquetes(
    session: Session, paquetes: list[Paquete]
) -> dict:
    """Versión batch de `candidatos_correccion` -- MISMA lógica (comparten
    `_construir_candidatos`), pero para una lista completa de Paquetes a la
    vez (auditoría de rendimiento 2026-08-10, `.scratch/pendientes-cliente`):
    antes, `/paquetes` llamaba a `candidatos_correccion` una vez POR paquete
    de la página, y cada llamada disparaba sus propias consultas de
    Apartamento/Ocupante/Persona -- un N+1 clásico que, bajo varias
    pestañas/usuarios navegando a la vez, agotaba el pool de conexiones de
    la BD. Acá se resuelven TODOS los Apartamentos/Ocupantes/Personas que
    la página entera podría necesitar en un puñado fijo de consultas, y el
    resto es trabajo en memoria.

    Pensada para paquetes en `paquete_lifecycle.ESTADOS_CORREGIBLES`
    (`ANUNCIADO`/`RECIBIDO`) -- el caller filtra (mismo criterio que ya
    tenía `packages.py::_listar`); pasar paquetes de otro estado
    (`ENTREGADO`/`CANCELADO`) no falla, solo no tiene sentido de negocio
    (no se pueden corregir).

    Returns:
        `{paquete.id: candidatos}`, mismo formato de `candidatos_correccion`
        para cada Paquete de `paquetes`.
    """
    # `normalizar_terna` -- mismo paso defensivo que ya hace
    # `buscar_apartamento_por_terna` en el camino de a uno: el snapshot HOY
    # siempre se copia ya normalizado desde el Apartamento resuelto
    # (`paquete_service.announce`/`paquete_lifecycle`), pero sin este paso
    # un futuro camino de escritura que no lo garantice dejaría estos
    # candidatos silenciosamente vacíos en vez de fallar ruidoso.
    ternas = {
        normalizar_terna(p.snapshot_conjunto, p.snapshot_torre, p.snapshot_apartamento)
        for p in paquetes
        if p.snapshot_conjunto and p.snapshot_torre and p.snapshot_apartamento
    }
    apartamentos_por_terna = {}
    if ternas:
        encontrados = (
            session.query(Apartamento)
            .filter(
                tuple_(
                    Apartamento.conjunto, Apartamento.torre, Apartamento.apartamento
                ).in_(ternas)
            )
            .all()
        )
        apartamentos_por_terna = {
            (a.conjunto, a.torre, a.apartamento): a for a in encontrados
        }

    apto_ids = {a.id for a in apartamentos_por_terna.values()}
    ocupantes_por_apto = {}
    if apto_ids:
        todos_ocupantes = (
            session.query(Ocupante)
            .filter(
                Ocupante.apartamento_id.in_(apto_ids),
                Ocupante.desvinculado_en.is_(None),
            )
            .order_by(Ocupante.es_principal.desc(), Ocupante.created_at.asc())
            .all()
        )
        for o in todos_ocupantes:
            ocupantes_por_apto.setdefault(o.apartamento_id, []).append(o)

    persona_ids = {
        o.persona_id for lst in ocupantes_por_apto.values() for o in lst if o.persona_id
    } | {p.announced_by_persona_id for p in paquetes if p.announced_by_persona_id}
    personas_por_id = {}
    if persona_ids:
        personas_por_id = {
            p.id: p
            for p in session.query(Persona).filter(Persona.id.in_(persona_ids)).all()
        }

    def _telefono_de(ocupante: Ocupante) -> str | None:
        if ocupante.persona_id is None:
            return None
        persona = personas_por_id.get(ocupante.persona_id)
        return persona.telefono if persona else None

    resultado = {}
    for paquete in paquetes:
        terna = normalizar_terna(
            paquete.snapshot_conjunto, paquete.snapshot_torre, paquete.snapshot_apartamento
        ) if (paquete.snapshot_conjunto and paquete.snapshot_torre and paquete.snapshot_apartamento) else None
        apto = apartamentos_por_terna.get(terna) if terna else None
        ocupantes = ocupantes_por_apto.get(apto.id, []) if apto is not None else []
        anunciante = personas_por_id.get(paquete.announced_by_persona_id)
        resultado[paquete.id] = _construir_candidatos(ocupantes, _telefono_de, anunciante)

    return resultado
