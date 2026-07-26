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
    if usuario is None or not usuario.password_hash:
        _verify_password(password, _DUMMY_HASH)
        return None
    if not _verify_password(password, usuario.password_hash):
        return None
    return usuario
