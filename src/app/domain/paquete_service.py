# -*- coding: utf-8 -*-
"""
Servicio de dominio `announce` — anunciar un Paquete congelando su contexto de
entrega (Seam A, ADR-0001).

Distingue **Anunciante** (siempre una Persona real, identificada por Teléfono
O usuario de WhatsApp -- ADR-0007, exactamente uno de los dos) de
**Destinatario**, que puede ser:

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
  - un Ocupante YA IDENTIFICADO por id → `Destinatario.ocupante(ocupante_id)`
    (staff, `/announce` -- ADR-0007, `.scratch/announce-rapido` ticket 03;
    generaliza la resolución por nombre de `declarado_por_cliente` al caso
    donde el Ocupante ya se conoce directamente, ej. elegido de una lista).

Al anunciar se CONGELA el snapshot: teléfono del anunciante (`NULL` si es
solo-WhatsApp), nombre/teléfono del destinatario y la terna del apartamento
resuelto EN EL INSTANTE del anuncio (copiada como texto — nunca un FK,
ADR-0001). El Paquete nace en `ANUNCIADO`.
"""

import enum
import secrets

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .apartamento import Apartamento
from .ocupante import Ocupante
from .ocupante_service import (
    listar_ocupantes,
    ocupante_activo_de_persona,
    telefono_notificacion_ocupante,
)
from .paquete import EstadoPaquete, Paquete
from .persona import Persona
from .persona_service import get_or_create_persona, get_or_create_persona_por_whatsapp
from .telefono import normalizar_telefono
from .texto import normalizar_nombre
from .usuario import Usuario


class _TipoDestinatario(enum.Enum):
    YO_MISMO = "YO_MISMO"
    PERSONA_REGISTRADA = "PERSONA_REGISTRADA"
    SOLO_NOMBRE = "SOLO_NOMBRE"
    DECLARADO_POR_CLIENTE = "DECLARADO_POR_CLIENTE"
    OCUPANTE = "OCUPANTE"


class Destinatario:
    """A nombre de quién llega el Paquete.

    No se instancia directamente: se construye con uno de los cinco
    constructores (`yo_mismo`, `persona_registrada`, `solo_nombre`,
    `declarado_por_cliente`, `ocupante`), que hacen explícito el caso del "a
    nombre de".
    """

    __slots__ = ("_tipo", "_telefono", "_nombre", "_ocupante_id")

    def __init__(
        self,
        tipo: _TipoDestinatario,
        telefono: str = None,
        nombre: str = None,
        ocupante_id=None,
    ):
        self._tipo = tipo
        self._telefono = telefono
        self._nombre = nombre
        self._ocupante_id = ocupante_id

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

    @classmethod
    def ocupante(cls, ocupante_id) -> "Destinatario":
        """A nombre de un Ocupante puntual, YA IDENTIFICADO por id (staff,
        `/announce` -- ADR-0007, `.scratch/announce-rapido` ticket 03).
        Generaliza la resolución que `declarado_por_cliente` hace por MATCH
        de nombre (`_resolver_ocupante_por_nombre`) al caso donde el Ocupante
        ya se conoce directamente (ej. el staff lo eligió de una lista de
        residentes de una unidad), sin tener que adivinar por texto."""
        return cls(_TipoDestinatario.OCUPANTE, ocupante_id=ocupante_id)


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


def _resolver_ocupante_por_nombre(
    session: Session, anunciante: Persona, nombre_declarado: str
) -> Ocupante | None:
    """¿El nombre declarado en `/anunciar` coincide con un Ocupante YA
    CONOCIDO del apartamento del anunciante? (.scratch/mis-datos, ticket 08)

    Compara contra TODO el roster ACTIVO de esa unidad (el propio anunciante
    u otro Ocupante) -- así el principal puede anunciar para sí mismo o para
    cualquier segundo contacto ya conocido de su unidad, sin fricción. La
    resolución es enteramente privada/servidor: `/anunciar` (vista pública)
    nunca expone esta lista, solo recibe teléfono + nombre en texto libre.

    Sin apartamento resuelto para el anunciante (o sin coincidencia), `None`
    -- cae al comportamiento de siempre (`declarado_por_cliente` tal cual)."""
    mi_ocupante = ocupante_activo_de_persona(session, anunciante.id)
    if mi_ocupante is None:
        return None
    apartamento = session.get(Apartamento, mi_ocupante.apartamento_id)
    if apartamento is None:
        return None
    nombre_normalizado = normalizar_nombre(nombre_declarado)
    for ocupante in listar_ocupantes(session, apartamento):
        if ocupante.nombre == nombre_normalizado:
            return ocupante
    return None


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
    anunciante_telefono: str = None,
    anunciante_nombre: str = None,
    destinatario: Destinatario = None,
    apartamento: Apartamento = None,
    staff_actor: Usuario = None,
    anunciante_whatsapp: str = None,
) -> Paquete:
    """Anuncia un Paquete: congela su contexto de entrega y lo deja en `ANUNCIADO`.

    Args:
        session: sesión de SQLAlchemy activa.
        anunciante_telefono: teléfono de quien anuncia (cualquier formato) --
            alterna a `anunciante_whatsapp`, exactamente uno de los dos.
        anunciante_nombre: nombre de quien anuncia (solo se usa si es Persona nueva).
        destinatario: a nombre de quién llega (uno de los `Destinatario.*`).
        apartamento: override explícito del apartamento de entrega; si es ``None``,
            se usa el `apartamento_actual` de la Persona relevante (o, para
            `Destinatario.ocupante(...)`, el Apartamento propio del Ocupante).
        staff_actor: si el anuncio lo hace el staff (vía `/announce`, no el
            cliente en `/anunciar`), el `Usuario` que lo hizo — se registra en
            `announced_by_usuario_id` para la auditoría de actor (Grupo 11,
            Ronda 2). `None` (default) para el flujo público de cliente.
        anunciante_whatsapp: usuario de WhatsApp de quien anuncia (ADR-0007,
            `.scratch/announce-rapido` ticket 03) -- alterna a
            `anunciante_telefono`, exactamente uno de los dos.

    Returns:
        El Paquete recién anunciado, con su snapshot congelado.

    Raises:
        LookupError: si el Destinatario es `persona_registrada` y no existe,
            o si es `ocupante` y ese id no existe.
        ValueError: si `destinatario` no se pasa; si no se pasa exactamente
            uno de `anunciante_telefono`/`anunciante_whatsapp`; si el que se
            pasó no contiene dígitos/no tiene forma válida.
    """
    if destinatario is None:
        raise ValueError("destinatario es obligatorio -- usa uno de los Destinatario.*.")

    tiene_telefono = bool((anunciante_telefono or "").strip())
    tiene_whatsapp = bool((anunciante_whatsapp or "").strip())
    if tiene_telefono == tiene_whatsapp:  # los dos, o ninguno
        raise ValueError(
            "Pasa exactamente uno de anunciante_telefono o anunciante_whatsapp, "
            "nunca los dos ni ninguno."
        )
    if tiene_telefono:
        anunciante = get_or_create_persona(session, anunciante_telefono, anunciante_nombre)
    else:
        anunciante = get_or_create_persona_por_whatsapp(
            session, anunciante_whatsapp, anunciante_nombre
        )

    # --- Resolver el Destinatario ------------------------------------------- #
    ocupante_resuelto = None
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
    elif destinatario._tipo is _TipoDestinatario.OCUPANTE:
        # Ocupante YA IDENTIFICADO por id (staff, `/announce` -- ticket 03):
        # mismo contrato de contacto de notificación que `telefono_notificacion_
        # ocupante` ya resuelve para el caso de match por nombre (propio si
        # tiene, si no el del Principal activo de su unidad) -- `recipient_phone`
        # es una columna de Teléfono real (SMS/OTP la consumen así), así que
        # queda NULL si el único contacto disponible en esa cadena es WhatsApp.
        persona_destino = None
        ocupante_resuelto = session.get(Ocupante, destinatario._ocupante_id)
        if ocupante_resuelto is None:
            raise LookupError(f"No existe un Ocupante con id {destinatario._ocupante_id!r}.")
        recipient_name = ocupante_resuelto.nombre
        recipient_phone = telefono_notificacion_ocupante(session, ocupante_resuelto)
    else:  # DECLARADO_POR_CLIENTE — nombre tal cual lo escribió, mismo tel del Anunciante.
        persona_destino = anunciante
        recipient_name = destinatario._nombre
        recipient_phone = anunciante.telefono
        # Auto-match contra el roster de Ocupantes del apartamento del
        # anunciante (.scratch/mis-datos, ticket 08) -- si el nombre
        # coincide con uno YA CONOCIDO (él mismo u otro Ocupante de su misma
        # unidad), se resuelve a ESE Ocupante en vez de al "nombre tal cual"
        # de siempre. Sin coincidencia, cae al comportamiento de toda la vida.
        match_por_nombre = _resolver_ocupante_por_nombre(
            session, anunciante, destinatario._nombre
        )
        if match_por_nombre is not None:
            recipient_name = match_por_nombre.nombre
            recipient_phone = telefono_notificacion_ocupante(session, match_por_nombre)

    # Normaliza SIEMPRE, aunque en YO_MISMO/PERSONA_REGISTRADA ya venga
    # normalizado desde su propia Persona -- idempotente, un solo punto de
    # verdad para el recipient_name que este Paquete va a congelar.
    recipient_name = normalizar_nombre(recipient_name)

    # --- Congelar el snapshot del apartamento (texto, EN EL INSTANTE) -------- #
    if apartamento is not None:
        snap_conjunto = apartamento.conjunto
        snap_torre = apartamento.torre
        snap_apartamento = apartamento.apartamento
    elif ocupante_resuelto is not None:
        # El Ocupante ya trae su propio Apartamento -- más directo y siempre
        # correcto que pasar por `apartamento_actual_id` de una Persona (que
        # ni siquiera existe si el Ocupante no tiene contacto propio).
        snap_conjunto, snap_torre, snap_apartamento = _terna_snapshot(
            session, ocupante_resuelto.apartamento_id
        )
    else:
        persona_para_apto = persona_destino if persona_destino is not None else anunciante
        snap_conjunto, snap_torre, snap_apartamento = _terna_snapshot(
            session, persona_para_apto.apartamento_actual_id
        )

    paquete = Paquete(
        access_code=_generar_access_code(session),
        announced_by_persona_id=anunciante.id,
        announced_by_phone=anunciante.telefono,
        announced_by_usuario_id=staff_actor.id if staff_actor else None,
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


# Máximo de Paquetes en ANUNCIADO (pendientes de recibir) que un mismo
# Teléfono puede acumular anunciando desde `/anunciar` (vista pública, sin
# sesión) -- pedido del cliente, `.scratch/pendientes-cliente`: evita que un
# error o abuso dispare una ráfaga de notificaciones SMS reales (cada
# ANUNCIADO nuevo notifica). Mismo espíritu que `MAX_OCUPANTES_ACTIVOS` en
# `ocupante_service.py` -- un tope duro con su propio mensaje claro.
MAX_ANUNCIADOS_ACTIVOS_POR_TELEFONO = 10


def contar_anunciados_activos_de_telefono(session: Session, telefono_canonico: str) -> int:
    """Cuántos Paquetes en `ANUNCIADO` (pendientes de recibir) tiene
    `telefono_canonico` como Anunciante -- la "cola" real que le interesa a
    este límite (una vez Recibido/Entregado/Cancelado, ya no aporta al
    problema de acumulación). `telefono_canonico` se recibe YA normalizado,
    mismo criterio que el resto del dominio."""
    return (
        session.query(Paquete)
        .filter(
            Paquete.estado == EstadoPaquete.ANUNCIADO,
            Paquete.announced_by_phone == telefono_canonico,
        )
        .count()
    )


def paquetes_sin_apartamento_de_telefono(
    session: Session, telefono_canonico: str
) -> list[Paquete]:
    """Paquetes "huérfanos" de `telefono_canonico`: `ANUNCIADO` y sin Apartamento
    resuelto en su snapshot (`.scratch/asociacion-retroactiva-apartamento`).

    Trae los que tienen ese teléfono como Anunciante O como Destinatario.
    `telefono_canonico` se recibe YA normalizado -- esta función no normaliza
    (mismo criterio que el resto del dominio: normalizar es responsabilidad
    de quien llama, con el teléfono en mano antes de resolver Personas).

    Nunca trae Paquetes `RECIBIDO`/`ENTREGADO`/`CANCELADO`, aunque no tengan
    Apartamento en su snapshot -- una vez el Paquete avanza de estado, su
    contexto de entrega es inmutable sin excepción (ADR-0001)."""
    return (
        session.query(Paquete)
        .filter(
            Paquete.estado == EstadoPaquete.ANUNCIADO,
            Paquete.snapshot_apartamento.is_(None),
            or_(
                Paquete.announced_by_phone == telefono_canonico,
                Paquete.recipient_phone == telefono_canonico,
            ),
        )
        .all()
    )
