# -*- coding: utf-8 -*-
"""
Servicio de dominio de registro de Persona (Seam A).

Concentra el invariante "el Teléfono es la llave universal": normaliza el
teléfono ANTES de persistir y garantiza que un mismo número —en cualquier
formato— resuelva a UNA sola Persona (registro implícito, sin duplicados).
"""

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .persona import Persona
from .telefono import normalizar_telefono
from .texto import normalizar_nombre

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_ANONIMIZADO_PREFIJO = "DEL-"  # nunca colisiona con un teléfono real (+57…)
_NOMBRE_ANONIMIZADO = "Cliente eliminado"


def _buscar_por_telefono(session: Session, telefono_canonico: str):
    return (
        session.query(Persona)
        .filter(Persona.telefono == telefono_canonico)
        .one_or_none()
    )


def get_or_create_persona(session: Session, telefono: str, nombre: str) -> Persona:
    """Reutiliza la Persona del teléfono dado, o la crea si no existe.

    Normaliza el teléfono a su forma canónica antes de buscar/persistir, de modo
    que dos formatos del mismo número resuelvan a la misma Persona.

    Args:
        session: sesión de SQLAlchemy activa.
        telefono: teléfono en cualquier formato.
        nombre: nombre del residente (solo se usa al CREAR; si la Persona ya
            existe no se sobreescribe su nombre).

    Returns:
        La Persona existente o recién creada.
    """
    telefono_canonico = normalizar_telefono(telefono)

    existente = _buscar_por_telefono(session, telefono_canonico)
    if existente is not None:
        return existente

    persona = Persona(telefono=telefono_canonico, nombre=normalizar_nombre(nombre))
    session.add(persona)
    try:
        session.flush()
    except IntegrityError:
        # Carrera: otra transacción creó la misma Persona; la constraint única
        # del Teléfono nos protege. Reintentar la lectura.
        session.rollback()
        encontrada = _buscar_por_telefono(session, telefono_canonico)
        if encontrada is None:
            raise
        return encontrada

    return persona


def update_datos_personales(
    session: Session,
    persona: Persona,
    *,
    nombre: str = None,
    email: str = None,
    segundo_contacto: str = None,
    whatsapp_usuario: str = None,
) -> Persona:
    """Actualiza PARCIALMENTE los datos ampliables de una Persona.

    Los argumentos en ``None`` NO tocan el valor existente — permite guardar
    parcialmente sin borrar lo que no se envió en esta llamada.

    `documento`/`tipo_documento` NO se aceptan aquí a propósito (Grupo 12,
    Ronda 2 de `ajustes-post-referencia-funcional`): el usuario pidió sacar
    ese dato de todo flujo del sistema. Las columnas siguen existiendo en
    `Persona` (dato histórico neutral, sin migración destructiva), pero
    ningún camino de código las escribe ya.

    `whatsapp_usuario` (pedido del cliente, .scratch/pendientes-cliente):
    solo lo escribe `/residentes/{id}` (staff) hoy -- `/mis-datos` (el
    propio cliente) simplemente no pasa este argumento, así que queda
    intacto para ese caller sin necesitar ninguna rama nueva.

    Valida la forma básica ANTES de mutar nada (atómico): si `email` viene con
    forma inválida, lanza `ValueError` y la Persona queda intacta (ningún otro
    campo de esta llamada se aplica tampoco).

    Raises:
        ValueError: si `email` viene y no tiene forma de email.
    """
    if email is not None and not _EMAIL_RE.match(email):
        raise ValueError(f"El email {email!r} no tiene un formato válido.")

    if nombre is not None:
        persona.nombre = normalizar_nombre(nombre)
    if email is not None:
        persona.email = email
    if segundo_contacto is not None:
        persona.segundo_contacto = segundo_contacto
    if whatsapp_usuario is not None:
        persona.whatsapp_usuario = whatsapp_usuario

    session.flush()
    return persona


def anonimizar_persona(session: Session, persona: Persona) -> Persona:
    """Anonimiza una Persona (ADR-0005): limpia sus datos personales y
    reemplaza su Teléfono por un valor sintético no reutilizable — sin borrar
    la fila (la FK real `fk_paquetes_anunciante` desde `paquetes` nunca se
    rompe). Idempotente: si ya estaba anonimizada, no hace nada.

    Desvincula del Apartamento asignando `apartamento_actual_id = None`
    directamente (no a través de `move_resident`, que la re-buscaría por
    teléfono de forma redundante ya que aquí se tiene la instancia en mano; la
    garantía de que esto no reescribe el snapshot de paquetes ya anunciados
    vive en el esquema — columnas de texto copiadas, no un FK — no en la
    función que se use para desvincular).
    """
    if persona.eliminado_en is not None:
        return persona

    persona.apartamento_actual_id = None
    persona.nombre = _NOMBRE_ANONIMIZADO
    persona.email = None
    persona.documento = None
    persona.tipo_documento = None
    persona.segundo_contacto = None
    persona.telefono = _ANONIMIZADO_PREFIJO + uuid.uuid4().hex[:16]
    persona.eliminado_en = datetime.now(timezone.utc)

    session.flush()
    return persona


def set_notificaciones_activas(session: Session, persona: Persona, activas: bool) -> Persona:
    """Activa o desactiva las notificaciones de evento (Recibido/Entregado/
    Cancelado) de una Persona. NUNCA afecta el envío del OTP — es el mecanismo
    de login, no una notificación opcional."""
    persona.notificaciones_activas = activas
    session.flush()
    return persona


def cambiar_telefono_propio(session: Session, persona: Persona, nuevo_telefono: str) -> Persona:
    """Cambia el teléfono de `persona` a `nuevo_telefono` -- edición del
    PROPIO número desde `/mis-datos` (pedido del cliente,
    `.scratch/pendientes-cliente/issues/35`). A diferencia de re-ligar un
    Ocupante gestionado a otra Persona, esto RENOMBRA la fila existente
    (mismo id, mismo historial de paquetes vía snapshot) -- no crea ni
    reutiliza otra Persona.

    El caller es responsable de cerrar la sesión tras un cambio exitoso y
    exigir una nueva verificación OTP al número nuevo -- confirma que quien
    pidió el cambio de verdad controla ese teléfono antes de dejarlo seguir
    operando con la identidad nueva.

    Raises:
        ValueError: si `nuevo_telefono` no tiene forma válida, o si ya
            pertenece a otra Persona.
    """
    canonico = normalizar_telefono(nuevo_telefono)
    if canonico == persona.telefono:
        return persona

    en_uso = (
        session.query(Persona)
        .filter(Persona.telefono == canonico, Persona.id != persona.id)
        .first()
    )
    if en_uso is not None:
        raise ValueError("Ese teléfono ya está en uso por otra cuenta.")

    persona.telefono = canonico
    session.flush()
    return persona


def set_autoriza_recepcion_automatica(session: Session, persona: Persona, autoriza: bool) -> Persona:
    """Activa o desactiva la autorización automática de recepción
    (.scratch/mis-datos, ticket 12) — puramente informativo para el staff,
    no cambia nada de lo que el staff puede hacer hoy."""
    persona.autoriza_recepcion_automatica = autoriza
    session.flush()
    return persona
