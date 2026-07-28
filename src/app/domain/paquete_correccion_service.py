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

from sqlalchemy.orm import Session

from .apartamento_service import buscar_apartamento_por_terna
from .ocupante_service import listar_ocupantes
from .paquete import Paquete
from .persona import Persona


def candidatos_correccion(session: Session, paquete: Paquete) -> list[dict]:
    """Candidatos para corregir el destinatario de `paquete`: los Ocupantes
    del Apartamento del snapshot (si el paquete tiene uno resuelto) más el
    Anunciante mismo — únicos por `(nombre, teléfono)`, en ese orden.

    Cada candidato es `{"nombre": str, "telefono": str | None}`. Sin
    Apartamento en el snapshot, la lista trae solo al Anunciante (o queda
    vacía si ni siquiera eso resuelve, lo que no debería pasar en la
    práctica — `announced_by_persona_id` siempre existe)."""
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

    if paquete.snapshot_conjunto and paquete.snapshot_torre and paquete.snapshot_apartamento:
        apto = buscar_apartamento_por_terna(
            session,
            paquete.snapshot_conjunto,
            paquete.snapshot_torre,
            paquete.snapshot_apartamento,
        )
        if apto is not None:
            for ocupante in listar_ocupantes(session, apto):
                telefono = None
                if ocupante.persona_id is not None:
                    persona = session.get(Persona, ocupante.persona_id)
                    telefono = persona.telefono if persona else None
                _agregar(ocupante.nombre, telefono)

    anunciante = session.get(Persona, paquete.announced_by_persona_id)
    if anunciante is not None:
        _agregar(anunciante.nombre, anunciante.telefono)

    return candidatos
