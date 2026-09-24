# -*- coding: utf-8 -*-
"""
Importador espejo v1 → v2 (`.scratch/importador-v1-espejo`).

Copia en UN solo sentido el contenido de la producción v1 al modelo de la v2,
y se corre repetidamente (cron cada 15 min + a mano). Reglas (spec, grilling
2026-09-23):

- **La v1 manda.** Todo registro con `origen_v1_id` se reescribe con lo que diga
  la v1; lo creado directamente en la v2 (sin `origen_v1_id`) nunca se toca.
- **Idempotente.** Correr dos veces sobre la misma instantánea no cambia nada.
- **Sin avisos.** Escribe directo en el modelo, sin pasar por el ciclo de vida
  que notifica.

Este módulo es la costura que se prueba: recibe una `InstantaneaV1` de filas
planas (armada por el lector SQL, `importador_v1_lector`, o en memoria por los
tests) y no sabe nada de la base de la v1 ni del mundo viejo (ADR-0004).
"""

import enum
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional, Protocol

from sqlalchemy.orm import Session

from .cobro import Cobro
from .guia import normalizar_guia
from .motivo_cancelacion import MotivoCancelacion
from .ocupante import Ocupante
from .paquete import CondicionPaquete, EstadoPaquete, Paquete, TipoPaquete
from .paquete_foto import PaqueteFoto
from .paquete_service import _generar_access_code
from .persona import Persona
from .preferencia_notificacion import PersonaPreferenciaNotificacion
from .saldo_contra_entrega import MovimientoSaldoContraEntrega
from .telefono import normalizar_telefono
from .usuario import RolUsuario, Usuario


class ModoSincronizacion(str, enum.Enum):
    NORMAL = "normal"
    # Calcula y reporta todo igual que una pasada real, sin dejar nada escrito.
    SIMULAR = "simular"
    # La pasada del día del corte: igual que NORMAL, pero cualquier choque o
    # error la hace fallar y revierte todo lo escrito en la base (historia 44).
    FINAL = "final"


@dataclass(frozen=True)
class ClienteV1:
    """Una fila de `customers` de la v1."""

    id: str
    telefono: str
    nombre: str
    email: Optional[str] = None


@dataclass(frozen=True)
class PaqueteV1:
    """Una fila de `packages` de la v1 (fechas ya en UTC, las normaliza el lector)."""

    id: int
    cliente_id: str
    # El código de 4 caracteres que el residente recibió por SMS -- el
    # `access_code` de 8 caracteres de la v1 nunca se mostró y se descarta.
    tracking_number: str
    guide_number: Optional[str]
    display_name: Optional[str]
    estado: str
    package_type: Optional[str]
    package_condition: Optional[str]
    announced_at: datetime
    received_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    # Anuncio de la v1 del que salió este paquete (`package_announcements_new.
    # package_id`), para reconocer el Paquete `ANUNCIADO` ya importado.
    anuncio_id: Optional[str] = None
    # Lo que cobró la v1 al entregar (1.500/2.000 COP, sin bodegaje).
    total_amount: Optional[Decimal] = None


@dataclass(frozen=True)
class AnuncioV1:
    """Un anuncio ACTIVO de la v1 que todavía no es paquete."""

    id: str
    cliente_id: str
    tracking_code: str
    guide_number: Optional[str]
    nombre_destinatario: Optional[str]
    announced_at: datetime


@dataclass(frozen=True)
class UsuarioV1:
    """Una fila de `users` de la v1."""

    username: str
    email: Optional[str]
    nombre: str
    rol: str


@dataclass(frozen=True)
class HistorialV1:
    """Una fila de `package_history` de la v1: la única traza de quién hizo qué
    (`packages.created_by`/`updated_by` están vacíos en la v1)."""

    paquete_id: int
    estado: str
    changed_by: Optional[str]
    changed_at: datetime
    # `additional_data.cancellation_reason` de las filas `CANCELADO`.
    motivo_cancelacion: Optional[str] = None


# `changed_by` que firmó todas las entregas y cancelaciones de la v1 sin ser
# nadie en concreto (spec, historia 35).
OPERADOR_V1_USERNAME = "operator_1"
OPERADOR_V1_NOMBRE = "Operador v1 (sin identificar)"

# Usernames de la v1 que son la MISMA persona que otro (grilling 2026-09-23,
# pregunta 6: `jesus` y `jveyes` son JESUS VILLALOBOS).
_ALIAS_USUARIOS_V1 = {"jesus": "jveyes"}


@dataclass(frozen=True)
class FotoV1:
    """Una fila de `file_uploads` de la v1 (siempre una imagen de recepción)."""

    id: int
    paquete_id: int
    s3_key: str
    creada_en: Optional[datetime] = None


class CopiadorFotosV1(Protocol):
    """Puerto: copia un objeto del bucket PRIVADO de la v1 al bucket de fotos
    de la v2 (público) y devuelve su URL pública. El destino se deriva de la key
    de origen, así que copiar dos veces la misma foto no la duplica. Lanza si
    la copia falla. Implementación real: `s3_copiador_fotos_v1`."""

    def copiar(self, s3_key_origen: str) -> str: ...


def origen_de_anuncio(anuncio_id: str) -> str:
    return f"anuncio:{anuncio_id}"


@dataclass
class InstantaneaV1:
    """Todo lo que se lee de la v1 en una pasada."""

    clientes: list[ClienteV1] = field(default_factory=list)
    usuarios: list[UsuarioV1] = field(default_factory=list)
    paquetes: list[PaqueteV1] = field(default_factory=list)
    historial: list[HistorialV1] = field(default_factory=list)
    anuncios: list[AnuncioV1] = field(default_factory=list)
    fotos: list[FotoV1] = field(default_factory=list)


@dataclass
class ContadoresEntidad:
    creados: int = 0
    actualizados: int = 0
    sin_cambios: int = 0
    # Registros nativos de la v2 que el importador tomó como propios (misma
    # clave natural que un registro de la v1).
    adoptados: int = 0
    borrados: int = 0


@dataclass
class ReporteSincronizacion:
    modo: ModoSincronizacion = ModoSincronizacion.NORMAL
    personas: ContadoresEntidad = field(default_factory=ContadoresEntidad)
    usuarios: ContadoresEntidad = field(default_factory=ContadoresEntidad)
    paquetes: ContadoresEntidad = field(default_factory=ContadoresEntidad)
    cobros: ContadoresEntidad = field(default_factory=ContadoresEntidad)
    fotos: ContadoresEntidad = field(default_factory=ContadoresEntidad)
    # Choques de clave natural entre la v1 y otro registro de la v2 (teléfono,
    # `access_code`): se resuelven o se saltan, pero siempre quedan aquí.
    choques: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    # Motivos por los que la pasada ABORTÓ sin escribir nada (tope de borrado).
    alertas: list[str] = field(default_factory=list)

    @property
    def exitosa(self) -> bool:
        """¿La pasada terminó bien? Una alerta nunca lo es. En modo FINAL,
        además, cualquier choque o error la hace fallar; en los demás modos
        quedan en el reporte y se reintentan en la siguiente pasada."""
        if self.alertas:
            return False
        if self.modo is ModoSincronizacion.FINAL:
            return not (self.choques or self.errores)
        return True

    def contadores_por_entidad(self) -> dict[str, ContadoresEntidad]:
        return {"personas": self.personas, "usuarios": self.usuarios, "paquetes": self.paquetes, "cobros": self.cobros,
            "fotos": self.fotos,
        }


def sincronizar_desde_v1(
    session: Session,
    instantanea: InstantaneaV1,
    modo: ModoSincronizacion = ModoSincronizacion.NORMAL,
    copiador_fotos: Optional[CopiadorFotosV1] = None,
) -> ReporteSincronizacion:
    """Vuelca `instantanea` en la v2 y devuelve el reporte de la pasada.

    No hace commit: el llamador (el script) decide. En modo `SIMULAR` todo el
    trabajo corre dentro de un savepoint que se revierte al final.
    """
    reporte = ReporteSincronizacion(modo=modo)
    desaparecidos = _desaparecidos(session, instantanea)
    _verificar_tope_de_borrado(session, desaparecidos, reporte)
    if reporte.alertas:
        return reporte

    savepoint = session.begin_nested()
    try:
        _volcar(session, instantanea, modo, copiador_fotos, desaparecidos, reporte)
    except Exception:
        savepoint.rollback()
        raise
    if modo is ModoSincronizacion.SIMULAR or not reporte.exitosa:
        savepoint.rollback()
    else:
        savepoint.commit()
    return reporte


def _volcar(
    session: Session,
    instantanea: InstantaneaV1,
    modo: ModoSincronizacion,
    copiador_fotos: Optional[CopiadorFotosV1],
    desaparecidos: dict[str, set[str]],
    reporte: ReporteSincronizacion,
) -> None:
    for cliente in instantanea.clientes:
        _sincronizar_persona(session, cliente, reporte)
    session.flush()
    personas = _personas_importadas(session)
    usuarios = _ResolutorUsuarios(session, instantanea.usuarios, reporte)
    autoria = _autoria_por_paquete(session, instantanea, usuarios)
    for paquete_v1 in instantanea.paquetes:
        paquete = _sincronizar_paquete_v1(session, paquete_v1, personas, autoria.get(paquete_v1.id, {}), reporte)
        if paquete is not None:
            _sincronizar_cobro(session, paquete, paquete_v1, usuarios, reporte)
    usuarios.reportar_desconocidos()
    for anuncio in instantanea.anuncios:
        _sincronizar_anuncio_v1(session, anuncio, personas, reporte)
    session.flush()
    _sincronizar_fotos(session, instantanea.fotos, copiador_fotos, modo, reporte)
    session.flush()
    _borrar_desaparecidos(session, desaparecidos, reporte)
    session.flush()


def _sincronizar_persona(session: Session, cliente: ClienteV1, reporte: ReporteSincronizacion) -> None:
    try:
        telefono = normalizar_telefono(cliente.telefono)
    except ValueError as exc:
        reporte.errores.append(f"persona v1 {cliente.id}: {exc}")
        return
    datos = {"telefono": telefono, "nombre": cliente.nombre, "email": cliente.email}

    persona = session.query(Persona).filter(Persona.origen_v1_id == cliente.id).one_or_none()
    if persona is not None:
        if persona.telefono != telefono and _telefono_ocupado(session, telefono, persona.id):
            reporte.choques.append(
                f"persona v1 {cliente.id}: el teléfono nuevo {telefono} ya es de otra Persona; se deja sin cambios"
            )
            return
        _aplicar(persona, datos, reporte.personas)
        return

    # Adopción: una Persona nativa con el mismo teléfono pasa a ser el espejo
    # de este cliente (spec, historia 18) en vez de duplicarla o fallar.
    nativa = (
        session.query(Persona)
        .filter(Persona.telefono == telefono, Persona.origen_v1_id.is_(None))
        .one_or_none()
    )
    if nativa is not None:
        nativa.origen_v1_id = cliente.id
        for campo, valor in datos.items():
            setattr(nativa, campo, valor)
        reporte.personas.adoptados += 1
        return

    if _telefono_ocupado(session, telefono, excepto_id=None):
        reporte.choques.append(
            f"persona v1 {cliente.id}: el teléfono {telefono} ya es de otra Persona importada; no se crea"
        )
        return

    session.add(Persona(origen_v1_id=cliente.id, **datos))
    session.flush()
    reporte.personas.creados += 1


def _telefono_ocupado(session: Session, telefono: str, excepto_id) -> bool:
    return (
        session.query(Persona.id)
        .filter(Persona.telefono == telefono, Persona.id != excepto_id)
        .first()
        is not None
    )


def _aplicar(registro, datos: dict, contadores: ContadoresEntidad) -> None:
    """La v1 gana: escribe en `registro` los campos que difieren de `datos`."""
    cambio = False
    for campo, valor in datos.items():
        if getattr(registro, campo) != valor:
            setattr(registro, campo, valor)
            cambio = True
    if cambio:
        contadores.actualizados += 1
    else:
        contadores.sin_cambios += 1


# --------------------------------------------------------------------------- #
# Paquetes
# --------------------------------------------------------------------------- #


def _personas_importadas(session: Session) -> dict[str, Persona]:
    return {p.origen_v1_id: p for p in session.query(Persona).filter(Persona.origen_v1_id.isnot(None))}


def _sincronizar_paquete_v1(
    session: Session,
    paquete: PaqueteV1,
    personas: dict[str, Persona],
    autoria: dict,
    reporte: ReporteSincronizacion,
) -> Optional[Paquete]:
    etiqueta = f"paquete v1 {paquete.id}"
    persona = personas.get(paquete.cliente_id)
    if persona is None:
        reporte.errores.append(f"{etiqueta}: su cliente {paquete.cliente_id} no está importado")
        return None
    try:
        datos = {
            **_datos_de_anunciante(persona, paquete.display_name),
            "guide_number": normalizar_guia(paquete.guide_number),
            "estado": EstadoPaquete(paquete.estado),
            "package_type": TipoPaquete(paquete.package_type) if paquete.package_type else None,
            "package_condition": CondicionPaquete(paquete.package_condition) if paquete.package_condition else None,
            "announced_at": paquete.announced_at,
            "received_at": paquete.received_at,
            "delivered_at": paquete.delivered_at,
            "cancelled_at": paquete.cancelled_at,
            "received_by_usuario_id": autoria.get("received_by_usuario_id"),
            "delivered_by_usuario_id": autoria.get("delivered_by_usuario_id"),
            "cancelled_by_usuario_id": autoria.get("cancelled_by_usuario_id"),
            "cancel_reason": autoria.get("cancel_reason"),
        }
        if datos["received_at"] is None and autoria.get("recibido_en") is not None:
            datos["received_at"] = autoria["recibido_en"]
    except ValueError as exc:
        reporte.errores.append(f"{etiqueta}: {exc}")
        return None
    origen_anterior = origen_de_anuncio(paquete.anuncio_id) if paquete.anuncio_id is not None else None
    return _sincronizar_paquete(
        session, str(paquete.id), paquete.tracking_number, datos, reporte, etiqueta, origen_anterior
    )


def _sincronizar_anuncio_v1(
    session: Session, anuncio: AnuncioV1, personas: dict[str, Persona], reporte: ReporteSincronizacion
) -> None:
    etiqueta = f"anuncio v1 {anuncio.id}"
    persona = personas.get(anuncio.cliente_id)
    if persona is None:
        reporte.errores.append(f"{etiqueta}: su cliente {anuncio.cliente_id} no está importado")
        return
    try:
        guia = normalizar_guia(anuncio.guide_number)
    except ValueError as exc:
        reporte.errores.append(f"{etiqueta}: {exc}")
        return
    datos = {
        **_datos_de_anunciante(persona, anuncio.nombre_destinatario),
        "guide_number": guia,
        "estado": EstadoPaquete.ANUNCIADO,
        "announced_at": anuncio.announced_at,
    }
    _sincronizar_paquete(session, origen_de_anuncio(anuncio.id), anuncio.tracking_code, datos, reporte, etiqueta)


def _datos_de_anunciante(persona: Persona, nombre_destinatario: Optional[str]) -> dict:
    """La v1 no distingue Anunciante de Destinatario: el cliente es los dos, y
    el nombre "a nombre de" (si lo hay) es el `recipient_name` (spec, historia 25).
    El snapshot de apartamento queda fuera: la v1 nunca lo tuvo y el importador
    no lo administra (una corrección hecha en la v2 se respeta)."""
    return {
        "announced_by_persona_id": persona.id,
        "announced_by_phone": persona.telefono,
        "recipient_name": (nombre_destinatario or "").strip() or persona.nombre,
        "recipient_phone": persona.telefono,
    }


def _sincronizar_paquete(
    session: Session,
    origen: str,
    codigo: str,
    datos: dict,
    reporte: ReporteSincronizacion,
    etiqueta: str,
    origen_anterior: Optional[str] = None,
) -> Optional[Paquete]:
    paquete = session.query(Paquete).filter(Paquete.origen_v1_id == origen).one_or_none()
    if paquete is None and origen_anterior is not None:
        # El anuncio de la v1 se convirtió en paquete: el mismo Paquete de la
        # v2 avanza, sin cambiar de código ni de identidad (historia 28).
        paquete = session.query(Paquete).filter(Paquete.origen_v1_id == origen_anterior).one_or_none()
        if paquete is not None:
            paquete.origen_v1_id = origen
    if paquete is not None:
        # `access_code` NO está en `datos`: se fija solo al crear, así nunca se
        # deshace el sufijo de año de "Migrar año" (historia 23).
        _aplicar(paquete, datos, reporte.paquetes)
        return paquete

    ocupante = session.query(Paquete).filter(Paquete.access_code == codigo).one_or_none()
    if ocupante is not None:
        if ocupante.origen_v1_id is not None:
            reporte.choques.append(
                f"{etiqueta}: el código {codigo} ya es del paquete importado {ocupante.origen_v1_id}; no se crea"
            )
            return None
        # El paquete de la v1 conserva el código que el residente conoce; el
        # nativo (de prueba) recibe uno nuevo (historia 24).
        ocupante.access_code = _generar_access_code(session)
        reporte.choques.append(
            f"{etiqueta}: el código {codigo} lo tenía un paquete de la v2; se le asignó {ocupante.access_code}"
        )
        session.flush()

    paquete = Paquete(origen_v1_id=origen, access_code=codigo, **datos)
    session.add(paquete)
    session.flush()
    reporte.paquetes.creados += 1
    return paquete


# --------------------------------------------------------------------------- #
# Autoría (staff)
# --------------------------------------------------------------------------- #


def _autoria_por_paquete(
    session: Session, instantanea: InstantaneaV1, resolutor: "_ResolutorUsuarios"
) -> dict[int, dict]:
    """Por paquete de la v1: quién lo recibió (primer `RECIBIDO`), entregó y
    canceló (último de cada uno), según `package_history` (spec, historias 32-35)."""
    motivos = {m.etiqueta.casefold(): m.etiqueta for m in session.query(MotivoCancelacion)}
    autoria: dict[int, dict] = {}
    for fila in sorted(instantanea.historial, key=lambda h: h.changed_at):
        datos = autoria.setdefault(fila.paquete_id, {})
        usuario_id = resolutor.resolver(fila.changed_by)
        if fila.estado == "RECIBIDO" and "received_by_usuario_id" not in datos:
            datos["received_by_usuario_id"] = usuario_id
            datos["recibido_en"] = fila.changed_at
        elif fila.estado == "ENTREGADO":
            datos["delivered_by_usuario_id"] = usuario_id
        elif fila.estado == "CANCELADO":
            datos["cancelled_by_usuario_id"] = usuario_id
            motivo = (fila.motivo_cancelacion or "").strip()
            datos["cancel_reason"] = motivos.get(motivo.casefold(), motivo[:40]) if motivo else None
    return autoria


class _ResolutorUsuarios:
    """Traduce un `changed_by` de la v1 al id de un Usuario de la v2, creando
    lo que falte UNA sola vez por pasada:

    - `operator_1` → el Usuario técnico inactivo "Operador v1 (sin identificar)".
    - username de la v1 (tras aplicar `_ALIAS_USUARIOS_V1`) → el Usuario de la v2
      con ese email, que se enlaza (adopción); si no hay, uno INACTIVO nuevo
      con su nombre, sin email ni contraseña (nunca puede iniciar sesión).
    - cualquier otro valor → sin autor, y se reporta.
    """

    def __init__(self, session: Session, usuarios_v1: list[UsuarioV1], reporte: ReporteSincronizacion):
        self._session = session
        self._usuarios_v1 = {u.username: u for u in usuarios_v1}
        self._reporte = reporte
        self._cache: dict[str, Optional[object]] = {}
        self._desconocidos: dict[str, int] = {}

    def resolver(self, changed_by: Optional[str]):
        if not changed_by:
            return None
        username = _ALIAS_USUARIOS_V1.get(changed_by, changed_by)
        if username not in self._cache:
            self._cache[username] = self._buscar_o_crear(username)
        if self._cache[username] is None:
            self._desconocidos[changed_by] = self._desconocidos.get(changed_by, 0) + 1
        return self._cache[username]

    def reportar_desconocidos(self) -> None:
        for nombre, veces in sorted(self._desconocidos.items()):
            self._reporte.errores.append(
                f"changed_by desconocido {nombre!r} en {veces} acciones del historial: quedan sin autor"
            )

    def _buscar_o_crear(self, username: str):
        existente = self._session.query(Usuario).filter(Usuario.origen_v1_id == username).one_or_none()
        if existente is not None:
            self._reporte.usuarios.sin_cambios += 1
            return existente.id

        if username == OPERADOR_V1_USERNAME:
            return self._crear_inactivo(username, OPERADOR_V1_NOMBRE, RolUsuario.OPERADOR)

        usuario_v1 = self._usuarios_v1.get(username)
        if usuario_v1 is None:
            return None
        if usuario_v1.email:
            por_email = (
                self._session.query(Usuario)
                .filter(
                    Usuario.email.ilike(usuario_v1.email.strip()),
                    Usuario.origen_v1_id.is_(None),
                )
                .one_or_none()
            )
            if por_email is not None:
                por_email.origen_v1_id = username
                self._reporte.usuarios.adoptados += 1
                return por_email.id
        try:
            rol = RolUsuario(usuario_v1.rol)
        except ValueError:
            rol = RolUsuario.OPERADOR
        return self._crear_inactivo(username, usuario_v1.nombre, rol)

    def _crear_inactivo(self, username: str, nombre: str, rol: RolUsuario):
        usuario = Usuario(
            origen_v1_id=username, nombre=nombre, rol=rol, activo=False, email=None, password_hash=None
        )
        self._session.add(usuario)
        self._session.flush()
        self._reporte.usuarios.creados += 1
        return usuario.id


# --------------------------------------------------------------------------- #
# Cobros
# --------------------------------------------------------------------------- #


def _sincronizar_cobro(
    session: Session,
    paquete: Paquete,
    paquete_v1: PaqueteV1,
    usuarios: _ResolutorUsuarios,
    reporte: ReporteSincronizacion,
) -> None:
    """Un Cobro por paquete ENTREGADO, copia fiel de la v1: el `total_amount`
    tal cual, sin bodegaje y sin recalcular con las reglas de la v2 (exención
    de primera entrega, bloques) -- spec, historias 37-38. Es la única
    excepción al Cobro append-only, y solo sobre paquetes importados."""
    cobro = session.query(Cobro).filter(Cobro.paquete_id == paquete.id).one_or_none()
    if paquete.estado is not EstadoPaquete.ENTREGADO:
        if cobro is not None:
            session.delete(cobro)
            reporte.cobros.borrados += 1
            reporte.errores.append(
                f"paquete v1 {paquete_v1.id}: dejó de estar entregado en la v1; se borró su cobro importado"
            )
        return
    if paquete.delivered_at is None:
        reporte.errores.append(f"paquete v1 {paquete_v1.id}: entregado sin fecha de entrega; no se crea cobro")
        return
    monto = int(round(paquete_v1.total_amount or 0))
    datos = {
        "monto_base": monto,
        "bloques_bodegaje": 0,
        "monto_bodegaje": 0,
        "monto_total": monto,
        "motivo_anulacion": None,
        "cobrado_por_usuario_id": usuarios.resolver(OPERADOR_V1_USERNAME),
        "cobrado_en": paquete.delivered_at,
    }
    if cobro is None:
        session.add(Cobro(paquete_id=paquete.id, **datos))
        reporte.cobros.creados += 1
        return
    _aplicar(cobro, datos, reporte.cobros)


# --------------------------------------------------------------------------- #
# Fotos
# --------------------------------------------------------------------------- #


def _sincronizar_fotos(
    session: Session,
    fotos: list[FotoV1],
    copiador: Optional[CopiadorFotosV1],
    modo: ModoSincronizacion,
    reporte: ReporteSincronizacion,
) -> None:
    """Cada foto de la v1 se copia UNA vez al bucket de la v2 y queda como
    `PaqueteFoto` (spec, historias 39-42). Una copia que falla se reporta y no
    crea la fila, así la siguiente pasada la reintenta. En `SIMULAR` nunca se
    llama al copiador: solo se cuenta lo que se copiaría."""
    if not fotos:
        return
    ya_importadas = {
        origen for (origen,) in session.query(PaqueteFoto.origen_v1_id).filter(PaqueteFoto.origen_v1_id.isnot(None))
    }
    paquetes = {
        p.origen_v1_id: p.id for p in session.query(Paquete.origen_v1_id, Paquete.id).filter(Paquete.origen_v1_id.isnot(None))
    }
    for foto in fotos:
        etiqueta = f"foto v1 {foto.id}"
        if str(foto.id) in ya_importadas:
            reporte.fotos.sin_cambios += 1
            continue
        paquete_id = paquetes.get(str(foto.paquete_id))
        if paquete_id is None:
            reporte.errores.append(f"{etiqueta}: su paquete v1 {foto.paquete_id} no está importado")
            continue
        if modo is ModoSincronizacion.SIMULAR:
            reporte.fotos.creados += 1
            continue
        if copiador is None:
            reporte.errores.append(f"{etiqueta}: no hay copiador de fotos configurado")
            continue
        try:
            url = copiador.copiar(foto.s3_key)
        except Exception as exc:  # noqa: BLE001 -- cualquier falla de S3 se reporta y se reintenta
            reporte.errores.append(f"{etiqueta}: no se pudo copiar ({exc})")
            continue
        datos = {"origen_v1_id": str(foto.id), "paquete_id": paquete_id, "url": url}
        if foto.creada_en is not None:
            datos["created_at"] = foto.creada_en
        session.add(PaqueteFoto(**datos))
        reporte.fotos.creados += 1


# --------------------------------------------------------------------------- #
# Borrado reflejado
# --------------------------------------------------------------------------- #

# Si desaparece de la v1 más que esta fracción de lo importado de una entidad,
# se asume una lectura rota (tabla vacía, conexión a la base equivocada) y la
# pasada aborta sin escribir nada (spec, historia 15).
TOPE_BORRADO = 0.05


def _origenes_presentes(instantanea: InstantaneaV1) -> dict[str, set[str]]:
    paquetes = {str(p.id) for p in instantanea.paquetes}
    paquetes |= {origen_de_anuncio(a.id) for a in instantanea.anuncios}
    # Un anuncio que la v1 convirtió en paquete NO desapareció: su Paquete se
    # renombra en esta misma pasada (`_sincronizar_paquete`).
    paquetes |= {origen_de_anuncio(p.anuncio_id) for p in instantanea.paquetes if p.anuncio_id is not None}
    return {
        "personas": {c.id for c in instantanea.clientes},
        "paquetes": paquetes,
        "fotos": {str(f.id) for f in instantanea.fotos},
    }


_MODELOS_BORRABLES = {"personas": Persona, "paquetes": Paquete, "fotos": PaqueteFoto}


def _desaparecidos(session: Session, instantanea: InstantaneaV1) -> dict[str, set[str]]:
    presentes = _origenes_presentes(instantanea)
    return {
        entidad: _importados(session, modelo) - presentes[entidad]
        for entidad, modelo in _MODELOS_BORRABLES.items()
    }


def _importados(session: Session, modelo) -> set[str]:
    return {o for (o,) in session.query(modelo.origen_v1_id).filter(modelo.origen_v1_id.isnot(None))}


def _verificar_tope_de_borrado(
    session: Session, desaparecidos: dict[str, set[str]], reporte: ReporteSincronizacion
) -> None:
    for entidad, faltan in desaparecidos.items():
        if not faltan:
            continue
        total = len(_importados(session, _MODELOS_BORRABLES[entidad]))
        if len(faltan) / total > TOPE_BORRADO:
            reporte.alertas.append(
                f"{entidad}: desaparecerían de la v1 {len(faltan)} de {total} importados "
                f"(más del {TOPE_BORRADO:.0%}); la pasada abortó sin escribir nada"
            )


def _borrar_desaparecidos(
    session: Session, desaparecidos: dict[str, set[str]], reporte: ReporteSincronizacion
) -> None:
    """Borra lo importado que ya no está en la v1, en cascada y SOLO sobre
    registros con `origen_v1_id` (spec, historias 12-14). Lo que tenga datos
    nativos colgando se desvincula (deja de ser espejo) en vez de borrarse."""
    if desaparecidos["fotos"]:
        for foto in session.query(PaqueteFoto).filter(PaqueteFoto.origen_v1_id.in_(desaparecidos["fotos"])):
            session.delete(foto)
            reporte.fotos.borrados += 1

    for paquete in session.query(Paquete).filter(Paquete.origen_v1_id.in_(desaparecidos["paquetes"])):
        tiene_saldo = (
            session.query(MovimientoSaldoContraEntrega.id)
            .filter(MovimientoSaldoContraEntrega.paquete_id == paquete.id)
            .first()
        )
        if tiene_saldo is not None:
            reporte.errores.append(
                f"paquete v1 {paquete.origen_v1_id}: desapareció de la v1 pero tiene movimientos de saldo "
                "en la v2; se desvincula en vez de borrarse"
            )
            paquete.origen_v1_id = None
            continue
        for foto in session.query(PaqueteFoto).filter(PaqueteFoto.paquete_id == paquete.id):
            session.delete(foto)
            reporte.fotos.borrados += 1
        cobro = session.query(Cobro).filter(Cobro.paquete_id == paquete.id).one_or_none()
        if cobro is not None:
            session.delete(cobro)
            reporte.cobros.borrados += 1
        session.delete(paquete)
        reporte.paquetes.borrados += 1
    session.flush()

    for persona in session.query(Persona).filter(Persona.origen_v1_id.in_(desaparecidos["personas"])):
        if _tiene_datos_nativos(session, persona):
            persona.origen_v1_id = None
            continue
        session.query(PersonaPreferenciaNotificacion).filter(
            PersonaPreferenciaNotificacion.persona_id == persona.id
        ).delete(synchronize_session=False)
        session.delete(persona)
        reporte.personas.borrados += 1


def _tiene_datos_nativos(session: Session, persona: Persona) -> bool:
    for modelo, columna in (
        (Paquete, Paquete.announced_by_persona_id),
        (Ocupante, Ocupante.persona_id),
        (MovimientoSaldoContraEntrega, MovimientoSaldoContraEntrega.persona_id),
    ):
        if session.query(modelo.id).filter(columna == persona.id).first() is not None:
            return True
    return False
