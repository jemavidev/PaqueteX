# -*- coding: utf-8 -*-
"""
Servicio de dominio `announce` — anunciar un Paquete congelando su contexto de
entrega (Seam A, ADR-0001).

Distingue **Anunciante** (siempre una Persona real, creada/reutilizada por su
Teléfono) de **Destinatario**, que puede ser:

  - el propio Anunciante          → `Destinatario.yo_mismo()`
  - otra Persona ya registrada     → `Destinatario.persona_registrada(telefono)`
  - solo un nombre sin teléfono    → `Destinatario.solo_nombre(nombre)`
    (queda bajo el teléfono del Anunciante, sin crear una Persona sin llave).
  - el nombre que declaró el cliente al anunciar → `Destinatario.declarado_por_cliente(nombre)`
    (usado por la vista simplificada `/anunciar`: el cliente no elige "a nombre
    de quién", solo escribe un nombre; puede o no coincidir con el nombre YA
    registrado del Anunciante — el staff resuelve cualquier discrepancia
    después, ver `REFERENCIA_FUNCIONAL_APLICATIVO.md` y el Grupo 1 de
    `ajustes-post-referencia-funcional/REQUERIMIENTOS.md`).

Al anunciar se CONGELA el snapshot: teléfono del anunciante, nombre/teléfono del
destinatario y la terna del apartamento resuelto EN EL INSTANTE del anuncio
(copiada como texto — nunca un FK, ADR-0001). El Paquete nace en `ANUNCIADO`.
"""

import enum
import secrets

from sqlalchemy.orm import Session

from .apartamento import Apartamento
from .paquete import EstadoPaquete, Paquete
from .persona import Persona
from .persona_service import get_or_create_persona
from .telefono import normalizar_telefono


class _TipoDestinatario(enum.Enum):
    YO_MISMO = "YO_MISMO"
    PERSONA_REGISTRADA = "PERSONA_REGISTRADA"
    SOLO_NOMBRE = "SOLO_NOMBRE"
    DECLARADO_POR_CLIENTE = "DECLARADO_POR_CLIENTE"


class Destinatario:
    """A nombre de quién llega el Paquete.

    No se instancia directamente: se construye con uno de los cuatro
    constructores (`yo_mismo`, `persona_registrada`, `solo_nombre`,
    `declarado_por_cliente`), que hacen explícito el caso del "a nombre de".
    """

    __slots__ = ("_tipo", "_telefono", "_nombre")

    def __init__(self, tipo: _TipoDestinatario, telefono: str = None, nombre: str = None):
        self._tipo = tipo
        self._telefono = telefono
        self._nombre = nombre

    @classmethod
    def yo_mismo(cls) -> "Destinatario":
        """El Destinatario ES el Anunciante (usa el nombre YA REGISTRADO de la Persona)."""
        return cls(_TipoDestinatario.YO_MISMO)

    @classmethod
    def persona_registrada(cls, telefono: str) -> "Destinatario":
        """A nombre de otra Persona ya registrada (por su Teléfono propio)."""
        return cls(_TipoDestinatario.PERSONA_REGISTRADA, telefono=telefono)

    @classmethod
    def solo_nombre(cls, nombre: str) -> "Destinatario":
        """Solo un nombre, sin teléfono: queda bajo el tel del Anunciante."""
        return cls(_TipoDestinatario.SOLO_NOMBRE, nombre=nombre)

    @classmethod
    def declarado_por_cliente(cls, nombre: str) -> "Destinatario":
        """El nombre que el cliente escribió al anunciar (vista simplificada
        `/anunciar`), bajo el mismo teléfono del Anunciante. A diferencia de
        `yo_mismo()`, usa el nombre TAL CUAL lo escribió (puede no coincidir
        con el nombre ya registrado de la Persona — el staff resuelve
        cualquier discrepancia después)."""
        return cls(_TipoDestinatario.DECLARADO_POR_CLIENTE, nombre=nombre)


def _persona_por_telefono(session: Session, telefono_canonico: str):
    return (
        session.query(Persona)
        .filter(Persona.telefono == telefono_canonico)
        .one_or_none()
    )


def _terna_snapshot(session: Session, apartamento_id):
    """La terna del apartamento indicado, o (None, None, None) si no hay."""
    if apartamento_id is None:
        return (None, None, None)
    apto = session.get(Apartamento, apartamento_id)
    if apto is None:
        return (None, None, None)
    return (apto.conjunto, apto.torre, apto.apartamento)


_ALFABETO_ACCESS_CODE = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # sin 0,1,O,I,L
_LONGITUD_ACCESS_CODE = 4
_MAX_INTENTOS_ACCESS_CODE = 50


def _generar_candidato_access_code() -> str:
    return "".join(
        secrets.choice(_ALFABETO_ACCESS_CODE) for _ in range(_LONGITUD_ACCESS_CODE)
    )


def _generar_access_code(session: Session) -> str:
    """4 caracteres sin ambigüedad visual (excluye `0`,`1`,`O`,`I`,`L`), nunca con
    la secuencia `666`, y único contra los ya existentes en `paquetes`."""
    for _ in range(_MAX_INTENTOS_ACCESS_CODE):
        candidato = _generar_candidato_access_code()
        if "666" in candidato:
            continue
        existe = (
            session.query(Paquete)
            .filter(Paquete.access_code == candidato)
            .first()
        )
        if existe is None:
            return candidato
    raise RuntimeError("No se pudo generar un access_code único tras varios intentos.")


def announce(
    session: Session,
    anunciante_telefono: str,
    anunciante_nombre: str,
    destinatario: Destinatario,
    apartamento: Apartamento = None,
) -> Paquete:
    """Anuncia un Paquete: congela su contexto de entrega y lo deja en `ANUNCIADO`.

    Args:
        session: sesión de SQLAlchemy activa.
        anunciante_telefono: teléfono de quien anuncia (cualquier formato).
        anunciante_nombre: nombre de quien anuncia (solo se usa si es Persona nueva).
        destinatario: a nombre de quién llega (uno de los tres `Destinatario.*`).
        apartamento: override explícito del apartamento de entrega; si es ``None``,
            se usa el `apartamento_actual` de la Persona relevante.

    Returns:
        El Paquete recién anunciado, con su snapshot congelado.

    Raises:
        LookupError: si el Destinatario es `persona_registrada` y no existe.
        ValueError: si un teléfono es ``None`` o no contiene dígitos.
    """
    anunciante = get_or_create_persona(session, anunciante_telefono, anunciante_nombre)

    # --- Resolver el Destinatario ------------------------------------------- #
    if destinatario._tipo is _TipoDestinatario.YO_MISMO:
        persona_destino = anunciante
        recipient_name = anunciante.nombre
        recipient_phone = anunciante.telefono
    elif destinatario._tipo is _TipoDestinatario.PERSONA_REGISTRADA:
        telefono_canonico = normalizar_telefono(destinatario._telefono)
        persona_destino = _persona_por_telefono(session, telefono_canonico)
        if persona_destino is None:
            raise LookupError(
                f"No existe una Persona registrada con el teléfono "
                f"{telefono_canonico!r}; use Destinatario.solo_nombre(...) para un "
                "nombre sin teléfono."
            )
        recipient_name = persona_destino.nombre
        recipient_phone = persona_destino.telefono
    elif destinatario._tipo is _TipoDestinatario.SOLO_NOMBRE:
        # Un nombre bajo el teléfono del Anunciante, sin Persona.
        persona_destino = None
        recipient_name = destinatario._nombre
        recipient_phone = None
    else:  # DECLARADO_POR_CLIENTE — nombre tal cual lo escribió, mismo tel del Anunciante.
        persona_destino = anunciante
        recipient_name = destinatario._nombre
        recipient_phone = anunciante.telefono

    # --- Congelar el snapshot del apartamento (texto, EN EL INSTANTE) -------- #
    if apartamento is not None:
        snap_conjunto = apartamento.conjunto
        snap_torre = apartamento.torre
        snap_apartamento = apartamento.apartamento
    else:
        persona_para_apto = persona_destino if persona_destino is not None else anunciante
        snap_conjunto, snap_torre, snap_apartamento = _terna_snapshot(
            session, persona_para_apto.apartamento_actual_id
        )

    paquete = Paquete(
        access_code=_generar_access_code(session),
        announced_by_persona_id=anunciante.id,
        announced_by_phone=anunciante.telefono,
        recipient_name=recipient_name,
        recipient_phone=recipient_phone,
        snapshot_conjunto=snap_conjunto,
        snapshot_torre=snap_torre,
        snapshot_apartamento=snap_apartamento,
        estado=EstadoPaquete.ANUNCIADO,
    )
    session.add(paquete)
    session.flush()
    return paquete
