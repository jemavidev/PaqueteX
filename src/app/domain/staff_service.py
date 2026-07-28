# -*- coding: utf-8 -*-
"""
Servicio de dominio de credenciales de staff (Seam A).

Crea y verifica cuentas de staff (`Usuario`) con **email + contraseña fuerte**:

  - La contraseña se guarda SOLO **hasheada** (bcrypt), nunca en claro.
  - **Solo un ADMIN** crea cuentas (`create_staff`); el primer ADMIN se siembra
    por `create_initial_admin` (bootstrap operativo, no expuesto por HTTP).
  - `verify_credentials` acepta la contraseña correcta y rechaza la mala y el
    email inexistente **por igual** (sin filtrar cuál falló).
"""

import re

import bcrypt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .usuario import RolUsuario, Usuario

# bcrypt solo considera los primeros 72 bytes de la contraseña.
_BCRYPT_MAX_BYTES = 72
_MIN_PASSWORD_LEN = 10


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8")[:_BCRYPT_MAX_BYTES], bcrypt.gensalt()
    ).decode("ascii")


def _verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(
            (password or "").encode("utf-8")[:_BCRYPT_MAX_BYTES],
            hashed.encode("ascii"),
        )
    except (ValueError, TypeError):
        return False


# Hash "señuelo" para igualar el tiempo de cómputo cuando el email no existe
# (mitiga la enumeración de usuarios por timing).
_DUMMY_HASH = _hash_password("timing-equalizer-placeholder")


def _normalizar_email(email: str) -> str:
    if not email or not str(email).strip():
        raise ValueError("El email es obligatorio.")
    return str(email).strip().lower()


def _validar_password(password: str) -> None:
    """Política de contraseña fuerte: longitud mínima + letra y dígito."""
    if password is None or len(password) < _MIN_PASSWORD_LEN:
        raise ValueError(
            f"La contraseña debe tener al menos {_MIN_PASSWORD_LEN} caracteres."
        )
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        raise ValueError("La contraseña debe incluir al menos una letra y un dígito.")


def _crear_usuario(
    session: Session, email: str, nombre: str, password: str, rol: RolUsuario
) -> Usuario:
    email_norm = _normalizar_email(email)
    if not (nombre or "").strip():
        raise ValueError("El nombre es obligatorio.")
    _validar_password(password)

    if session.query(Usuario).filter(Usuario.email == email_norm).one_or_none():
        raise ValueError(f"Ya existe un usuario con el email {email_norm!r}.")

    usuario = Usuario(
        nombre=nombre,
        email=email_norm,
        password_hash=_hash_password(password),
        rol=rol,
    )
    session.add(usuario)
    try:
        session.flush()
    except IntegrityError:  # carrera contra la unicidad del email
        session.rollback()
        raise ValueError(f"Ya existe un usuario con el email {email_norm!r}.")
    return usuario


def create_staff(
    session: Session,
    actor: Usuario,
    email: str,
    nombre: str,
    password: str,
    rol: RolUsuario,
) -> Usuario:
    """Crea una cuenta de staff. **Exige que `actor` sea un ADMIN.**

    Raises:
        PermissionError: si `actor` no es un ADMIN.
        ValueError: email inválido/duplicado, nombre vacío o contraseña débil.
    """
    if actor is None or actor.rol != RolUsuario.ADMIN:
        raise PermissionError("Solo un ADMIN puede crear cuentas de staff.")
    return _crear_usuario(session, email, nombre, password, rol)


def create_initial_admin(
    session: Session, email: str, nombre: str, password: str
) -> Usuario:
    """Bootstrap: crea el PRIMER ADMIN, sin actor.

    Solo procede cuando no existe ningún ADMIN (no crea un segundo por esta vía).
    No se expone por HTTP: lo usa una tarea/CLI operativa.

    Raises:
        RuntimeError: si ya existe algún ADMIN.
    """
    ya_admin = (
        session.query(Usuario).filter(Usuario.rol == RolUsuario.ADMIN).first()
    )
    if ya_admin is not None:
        raise RuntimeError("Ya existe un ADMIN; el bootstrap no crea un segundo.")
    return _crear_usuario(session, email, nombre, password, RolUsuario.ADMIN)


def verify_credentials(session: Session, email: str, password: str):
    """Devuelve el `Usuario` si las credenciales son correctas, o `None`.

    Rechaza la contraseña mala y el email inexistente por igual (sin distinguir),
    e iguala el tiempo de cómputo con un hash señuelo cuando el email no existe.
    """
    try:
        email_norm = _normalizar_email(email)
    except ValueError:
        _verify_password(password, _DUMMY_HASH)
        return None

    usuario = session.query(Usuario).filter(Usuario.email == email_norm).one_or_none()
    if usuario is None or not usuario.password_hash or not usuario.activo:
        # Cuenta desactivada (Grupo 18, Ronda 2): mismo rechazo genérico que
        # email inexistente/contraseña mala -- no revela que la cuenta EXISTE
        # pero está desactivada.
        _verify_password(password, _DUMMY_HASH)
        return None
    if not _verify_password(password, usuario.password_hash):
        return None
    return usuario


def listar_staff(session: Session) -> list[Usuario]:
    """Todas las cuentas de staff, activas primero, por nombre."""
    return (
        session.query(Usuario)
        .order_by(Usuario.activo.desc(), Usuario.nombre.asc())
        .all()
    )


def editar_staff(
    session: Session,
    actor: Usuario,
    usuario: Usuario,
    nombre: str = None,
    rol: RolUsuario = None,
) -> Usuario:
    """Edita nombre y/o rol de `usuario`. **Exige que `actor` sea ADMIN.**

    Un `ADMIN` no puede degradarse a sí mismo a `OPERADOR` -- evita dejar el
    sistema sin ningún admin activo por accidente (Grupo 18, Ronda 2).

    Raises:
        PermissionError: si `actor` no es un ADMIN.
        ValueError: nombre vacío, o `actor` intentando degradarse a sí mismo.
    """
    if actor is None or actor.rol != RolUsuario.ADMIN:
        raise PermissionError("Solo un ADMIN puede editar cuentas de staff.")
    if rol is not None and rol != RolUsuario.ADMIN and usuario.id == actor.id:
        raise ValueError("Un ADMIN no puede degradarse a sí mismo.")

    if nombre is not None:
        if not nombre.strip():
            raise ValueError("El nombre es obligatorio.")
        usuario.nombre = nombre
    if rol is not None:
        usuario.rol = rol

    session.flush()
    return usuario


def resetear_password(
    session: Session, actor: Usuario, usuario: Usuario, nueva_password: str
) -> Usuario:
    """Resetea la contraseña de `usuario`. **Exige que `actor` sea ADMIN.**

    Raises:
        PermissionError: si `actor` no es un ADMIN.
        ValueError: si `nueva_password` no cumple la política de fuerza.
    """
    if actor is None or actor.rol != RolUsuario.ADMIN:
        raise PermissionError("Solo un ADMIN puede resetear contraseñas de staff.")
    _validar_password(nueva_password)
    usuario.password_hash = _hash_password(nueva_password)
    session.flush()
    return usuario


def set_activo_staff(
    session: Session, actor: Usuario, usuario: Usuario, activo: bool
) -> Usuario:
    """Activa/desactiva `usuario`. **Exige que `actor` sea ADMIN.**

    Un `ADMIN` no puede desactivarse a sí mismo -- evita dejar el sistema sin
    ningún admin activo por accidente (Grupo 18, Ronda 2). Desactivar NUNCA
    borra la fila (las FK de actor de `Paquete` dependen de que el Usuario
    siga existiendo).

    Raises:
        PermissionError: si `actor` no es un ADMIN.
        ValueError: si `actor` intenta desactivarse a sí mismo.
    """
    if actor is None or actor.rol != RolUsuario.ADMIN:
        raise PermissionError("Solo un ADMIN puede activar/desactivar staff.")
    if not activo and usuario.id == actor.id:
        raise ValueError("Un ADMIN no puede desactivarse a sí mismo.")

    usuario.activo = activo
    session.flush()
    return usuario
