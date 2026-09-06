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
    de quién", solo escribe un nombre. SOLO se honra ese nombre si coincide con
    un co-residente YA CONOCIDO de la MISMA unidad del Anunciante -- pedido
    explícito, conversación 2026-08-15: nadie puede anunciar "a nombre de"
    alguien que no comparte su unidad. Sin apartamento propio, o sin
    coincidencia dentro de esa unidad, el anuncio se hace individual --
    mismo resultado que `yo_mismo()`, sin error visible para el cliente --
    ver `REFERENCIA_FUNCIONAL_APLICATIVO.md` y el Grupo 1 de
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
import re
import secrets

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from .apartamento import Apartamento
from .ocupante import Ocupante
from .ocupante_service import (
    listar_ocupantes,
    ocupante_activo_de_persona,
    telefono_notificacion_de_persona,
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
        # Issue 163 (.scratch/pendientes-cliente): "siempre debe haber un
        # número... responsable" -- propio si tiene, si no el del Principal
        # de SU unidad actual (si es Ocupante de alguna). Antes era
        # directo `anunciante.telefono`, sin este fallback.
        recipient_phone = telefono_notificacion_de_persona(session, anunciante)
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
        # Issue 163: mismo fallback -- propio, si no el del Principal de su
        # unidad. Este caso YA venía con Teléfono propio garantizado
        # (`_persona_por_telefono` busca por Teléfono), así que el fallback
        # es defensivo, no cambia el resultado normal.
        recipient_phone = telefono_notificacion_de_persona(session, persona_destino)
    elif destinatario._tipo is _TipoDestinatario.SOLO_NOMBRE:
        # Un nombre bajo el teléfono del Anunciante, sin Persona -- sin
        # identidad real detrás, no hay de dónde sacar un Principal a quien
        # recurrir (issue 163 no aplica acá, a propósito).
        persona_destino = None
        recipient_name = destinatario._nombre
        recipient_phone = None
    elif destinatario._tipo is _TipoDestinatario.OCUPANTE:
        # Ocupante YA IDENTIFICADO por id (staff, `/announce` -- ticket 03).
        # Issue 163 (.scratch/pendientes-cliente): "siempre debe haber un
        # número... responsable" -- ahora SÍ usa `telefono_notificacion_
        # ocupante` (propio del Ocupante, si no el del Principal ACTIVO de
        # SU unidad) en vez del Teléfono crudo del Ocupante sin fallback --
        # antes quedaba en `None` si el Ocupante solo tenía WhatsApp propio,
        # aunque su unidad sí tuviera un Principal alcanzable por Teléfono.
        # `or anunciante.telefono` al final: último recurso si ni el
        # Ocupante ni su unidad tienen a nadie alcanzable por Teléfono
        # todavía (unidad recién declarada, sin Principal) -- mismo
        # comportamiento de respaldo que ya tenía este camino.
        persona_destino = None
        ocupante_resuelto = session.get(Ocupante, destinatario._ocupante_id)
        if ocupante_resuelto is None:
            raise LookupError(f"No existe un Ocupante con id {destinatario._ocupante_id!r}.")
        recipient_name = ocupante_resuelto.nombre
        recipient_phone = (
            telefono_notificacion_ocupante(session, ocupante_resuelto) or anunciante.telefono
        )
    else:  # DECLARADO_POR_CLIENTE — solo puede ser para un co-residente, o el propio Anunciante.
        persona_destino = anunciante
        # Default: el propio Anunciante (mismo criterio que YO_MISMO) --
        # NO el nombre tal cual lo escribió (pedido explícito, conversación
        # 2026-08-15: en `/anunciar` -- único consumidor de este
        # constructor -- solo se puede anunciar para alguien más si esa
        # persona es co-residente YA CONOCIDO de la misma unidad del
        # Anunciante; sin esa unidad, o sin que el nombre coincida con
        # nadie de ahí, el anuncio se hace individual, sin error visible).
        recipient_name = anunciante.nombre
        # Issue 163: mismo fallback que YO_MISMO -- propio, si no el del
        # Principal de la unidad del Anunciante.
        recipient_phone = telefono_notificacion_de_persona(session, anunciante)
        # Auto-match contra el roster de Ocupantes del apartamento del
        # anunciante (.scratch/mis-datos, ticket 08) -- si el nombre
        # coincide con uno YA CONOCIDO (él mismo u otro Ocupante de su misma
        # unidad), se resuelve a ESE Ocupante. Sin apartamento propio, o sin
        # coincidencia dentro de esa unidad, se queda en el default de
        # arriba (el propio Anunciante).
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


def paquetes_abiertos_de_persona(session: Session, persona: Persona) -> list[Paquete]:
    """Paquetes ANUNCIADO/RECIBIDO "a nombre de" `persona` -- issue 164
    (`.scratch/pendientes-cliente`): identificar a un residente en
    `/announce` y mostrarle ahí mismo lo que ya tiene en curso, para poder
    continuar su flujo (Recibir/Entregar) sin ir a `/paquetes` a buscarlo.

    Sin FK de `Paquete` al Destinatario (ADR-0001 -- el destinatario es
    snapshot de texto, nunca una referencia viva) -- se busca por las 2
    vías reales que sí pueden enlazar un Paquete a esta Persona:
    - `recipient_phone == persona.telefono` (si tiene Teléfono propio,
      mismo criterio que `/mis-paquetes`).
    - `announced_by_persona_id == persona.id` (FK real, disponible sin
      importar el tipo de contacto -- cubre a un destinatario solo-WhatsApp
      que anunció su propio paquete, `Destinatario.yo_mismo()`).

    Limitación real, no un descuido: un destinatario solo-WhatsApp cuyo
    paquete lo anunció OTRA persona en su nombre no queda cubierto -- no
    existe ningún dato en `Paquete` que enlace ESE caso puntual a esta
    Persona (ver docstring de `Paquete`, ADR-0001, y el fallback a
    Teléfono del Principal que ya intenta `announce()`, issue 163)."""
    condiciones = [Paquete.announced_by_persona_id == persona.id]
    if persona.telefono:
        condiciones.append(Paquete.recipient_phone == persona.telefono)
    return (
        session.query(Paquete)
        .filter(
            or_(*condiciones),
            Paquete.estado.in_([EstadoPaquete.ANUNCIADO, EstadoPaquete.RECIBIDO]),
        )
        .order_by(Paquete.announced_at.desc())
        .all()
    )


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


def es_primera_entrega_a_telefono(session: Session, recipient_phone: str | None) -> bool:
    """True si NUNCA se entregó (`ENTREGADO`) un paquete a `recipient_phone`
    -- issue 314 (.scratch/pendientes-cliente, pedido explícito): bandera
    "primera entrega" en el modal Entregar, por NÚMERO DE TELÉFONO, sin
    importar con qué otros residentes viva (no mira `persona_destino_id` ni
    la unidad). Sin teléfono, `False` -- no se puede afirmar "primera vez"
    sin ese dato.

    Versión de UN SOLO paquete para callers que resuelven uno a la vez (ej.
    `/consultar`, `search.py`) -- `/paquetes` (`packages.py::_listar`) usa
    su propia versión en batch (un query agrupando TODOS los teléfonos
    RECIBIDO de la página, mismo criterio de "un puñado fijo de consultas"
    del resto de esa función) en vez de esta, para no caer en un N+1 al
    listar muchos paquetes a la vez -- misma regla, dos formas, cada una
    del tamaño que le corresponde a su caller."""
    if not recipient_phone:
        return False
    ya_hubo_entrega = (
        session.query(Paquete)
        .filter(
            Paquete.recipient_phone == recipient_phone,
            Paquete.estado == EstadoPaquete.ENTREGADO,
        )
        .exists()
    )
    return not bool(session.query(ya_hubo_entrega).scalar())


def condiciones_busqueda_paquetes(q: str, conectados: bool) -> list:
    """Set de condiciones OR de texto libre para un `q` YA no vacío -- MISMA
    regla que usa el campo de búsqueda de `/paquetes` (issue 308, .scratch/
    pendientes-cliente: "exacto" = dato PROPIO del destinatario, "conectado"
    = solo vía el Anunciante). Vivía como función privada dentro de
    `packages.py` (`_condiciones_busqueda`) -- se relocó acá (issue 321,
    mismo criterio que `es_primera_entrega_a_telefono`) porque ahora también
    la necesita `contar_paquetes_de_persona`, usada desde `customers_manage.
    py` -- la regla de qué cuenta como "propio de un cliente" no puede vivir
    duplicada en 2 rutas sin arriesgar que diverjan."""
    patron = f"%{q}%"
    mismo_destinatario = func.lower(Persona.nombre) == func.lower(Paquete.recipient_name)
    digitos = re.sub(r"\D", "", q)
    tiene_digitos = len(digitos) >= 4
    patron_telefono = f"%{digitos}%" if tiene_digitos else None

    if not conectados:
        condiciones = [
            Paquete.access_code.ilike(patron),
            Paquete.guide_number.ilike(patron),
            Paquete.recipient_name.ilike(patron),
            Paquete.snapshot_torre.ilike(patron),
            Paquete.snapshot_apartamento.ilike(patron),
            and_(Persona.email.ilike(patron), mismo_destinatario),
            and_(Persona.whatsapp_usuario.ilike(patron), mismo_destinatario),
        ]
        if tiene_digitos:
            condiciones.append(Paquete.recipient_phone.ilike(patron_telefono))
    else:
        condiciones = [
            and_(Persona.nombre.ilike(patron), ~mismo_destinatario),
            and_(Persona.email.ilike(patron), ~mismo_destinatario),
            and_(Persona.whatsapp_usuario.ilike(patron), ~mismo_destinatario),
        ]
        if tiene_digitos:
            condiciones.append(
                and_(
                    Paquete.announced_by_phone.ilike(patron_telefono),
                    or_(
                        Paquete.recipient_phone.is_(None),
                        ~Paquete.recipient_phone.ilike(patron_telefono),
                    ),
                )
            )
    return condiciones


def paquetes_relacionados_por_codigo(session: Session, q: str) -> list[Paquete] | None:
    """Si `q` calza EXACTO con el `access_code` de un Paquete (único por
    diseño), expande ese único resultado a sus RECIBIDO relacionados --
    pedido explícito del cliente (2026-09-06): "si tiene 3 paquetes
    recibidos, al consultar uno se muestren los otros del mismo cliente o
    los residentes del apartamento", para entregar todo en una sola consulta
    en vez de repetir código por código. Devuelve `None` si `q` no calza con
    ningún `access_code` -- el caller sigue con la búsqueda de texto libre
    normal (`condiciones_busqueda_paquetes`), que sí acepta coincidencia
    PARCIAL (útil para código incompleto, guía, nombre, etc.).

    Dos criterios de relación, siempre juntos (no uno como fallback del
    otro): mismo destinatario (`recipient_phone`, el mismo campo que ya usa
    el resto de la identidad de destinatario en este módulo) O misma unidad
    (terna `snapshot_conjunto`/`snapshot_torre`/`snapshot_apartamento`,
    ignorada si el paquete encontrado no tiene apartamento resuelto). Ignora
    a propósito el `estado` que esté filtrando `/paquetes` en ese momento
    (ej. la pestaña "Cancelado") -- esto es una consulta puntual por código,
    no una navegación de listado, así que el filtro pasivo no debe esconder
    el propio resultado buscado.

    El paquete encontrado va SIEMPRE primero (es el que el staff buscó
    específicamente), seguido de sus relacionados RECIBIDO -- nunca
    duplicado así el propio esté también en RECIBIDO."""
    codigo = (q or "").strip().upper()
    if not codigo:
        return None
    principal = session.query(Paquete).filter(Paquete.access_code == codigo).one_or_none()
    if principal is None:
        return None

    condiciones_relacion = []
    if principal.recipient_phone:
        condiciones_relacion.append(Paquete.recipient_phone == principal.recipient_phone)
    if principal.snapshot_conjunto and principal.snapshot_torre and principal.snapshot_apartamento:
        condiciones_relacion.append(
            and_(
                Paquete.snapshot_conjunto == principal.snapshot_conjunto,
                Paquete.snapshot_torre == principal.snapshot_torre,
                Paquete.snapshot_apartamento == principal.snapshot_apartamento,
            )
        )

    relacionados = []
    if condiciones_relacion:
        relacionados = (
            session.query(Paquete)
            .filter(
                Paquete.estado == EstadoPaquete.RECIBIDO,
                Paquete.id != principal.id,
                or_(*condiciones_relacion),
            )
            .order_by(Paquete.received_at.desc())
            .all()
        )
    return [principal, *relacionados]


def contar_paquetes_de_persona(session: Session, persona: Persona) -> tuple[int, str | None]:
    """Cuántos paquetes (CUALQUIER estado -- Anunciado/Recibido/Entregado/
    Cancelado, todos suman) tiene `persona` como destinatario propio, y CON
    QUÉ término buscarlos en `/paquetes` para llegar exactamente a esos
    mismos resultados -- issue 321 (.scratch/pendientes-cliente, pedido
    explícito): píldora "N paquetes" en `/residentes`, cliqueable, que
    redirige a `/paquetes?q=<ese término>`.

    Prioridad teléfono > usuario de WhatsApp > email para elegir el término
    ÚNICO -- una Persona puede tener más de uno, pero `/paquetes` solo
    busca por UNO a la vez; se resuelve el conteo con el MISMO término y la
    MISMA regla "exacta" (`condiciones_busqueda_paquetes`, `conectados=
    False`) que usará el link, para que la píldora y la pantalla a la que
    lleva SIEMPRE coincidan -- nunca "la píldora dice 7 pero la búsqueda
    trae 5". Se sacrifica a propósito un conteo "más completo" que uniera
    los 3 campos (un paquete con teléfono prestado, ver issue 163/308,
    podría en teoría solo ser encontrable por email/whatsapp si esos datos
    no coinciden con el teléfono elegido acá) -- una píldora que promete de
    más es peor que una que cuenta de menos.

    `(0, None)` si la Persona no tiene ningún dato utilizable (no debería
    ocurrir en la práctica -- `ck_personas_telefono_o_whatsapp`, ADR-0007,
    garantiza al menos teléfono o WhatsApp)."""
    termino = persona.telefono or persona.whatsapp_usuario or persona.email
    if not termino:
        return 0, None
    total = (
        session.query(Paquete)
        .outerjoin(Persona, Paquete.announced_by_persona_id == Persona.id)
        .filter(or_(*condiciones_busqueda_paquetes(termino, conectados=False)))
        .count()
    )
    return total, termino
