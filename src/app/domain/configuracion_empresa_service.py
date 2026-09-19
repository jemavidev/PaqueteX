# -*- coding: utf-8 -*-
"""
Servicio de dominio de ConfiguracionEmpresa (Seam A).

Mismo patrón que `configuracion_conjunto_service`: única fila (override),
editable solo por ADMIN. Sin fila, se usan los defaults en código de abajo
(los mismos datos que hoy vivían hardcodeados e idénticos en las 4
plantillas legales -- `web/templates/{ayuda,terms,privacy,cookies}/
form.html`, reescritas en el grilling 2026-09-18) -- así las páginas
públicas NUNCA quedan vacías, ni el día del deploy ni si la fila se borra.
"""

import re
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .configuracion_empresa import ID_SINGLETON, ConfiguracionEmpresa
from .usuario import RolUsuario, Usuario

RAZON_SOCIAL_POR_DEFECTO = "Papyrus Soluciones Integrales S.A.S."
NIT_POR_DEFECTO = ""
DIRECCION_POR_DEFECTO = "Cra. 91 #54-120, Local 12, El Club Apartamentos, Cartagena"
EMAIL_CONTACTO_POR_DEFECTO = "paquetex@papyrus.com.co"
TELEFONO_CONTACTO_POR_DEFECTO = "(333) 400-4007"


@dataclass(frozen=True)
class DatosEmpresa:
    razon_social: str
    nit: str
    direccion: str
    email_contacto: str
    telefono_contacto: str


def _fila_vigente(session: Session) -> ConfiguracionEmpresa | None:
    return session.get(ConfiguracionEmpresa, ID_SINGLETON)


def obtener_datos_empresa(session: Session) -> DatosEmpresa:
    """Los datos vigentes de la empresa operadora -- personalizados si algún
    ADMIN ya los editó, si no los defaults (los mismos que ya se mostraban
    hardcodeados antes de este feature)."""
    fila = _fila_vigente(session)
    if fila is None:
        return DatosEmpresa(
            razon_social=RAZON_SOCIAL_POR_DEFECTO,
            nit=NIT_POR_DEFECTO,
            direccion=DIRECCION_POR_DEFECTO,
            email_contacto=EMAIL_CONTACTO_POR_DEFECTO,
            telefono_contacto=TELEFONO_CONTACTO_POR_DEFECTO,
        )
    return DatosEmpresa(
        razon_social=fila.razon_social,
        nit=fila.nit or "",
        direccion=fila.direccion or "",
        email_contacto=fila.email_contacto or "",
        telefono_contacto=fila.telefono_contacto or "",
    )


def actualizar_datos_empresa(
    session: Session,
    *,
    razon_social: str,
    nit: str,
    direccion: str,
    email_contacto: str,
    telefono_contacto: str,
    actor: Usuario,
) -> DatosEmpresa:
    """Fija los datos vigentes de la empresa operadora.

    Raises:
        PermissionError: si `actor` no es un ADMIN.
        ValueError: si `razon_social` queda vacía tras `strip()` -- el resto
            de los campos SÍ puede quedar vacío (ej. NIT todavía pendiente).
    """
    if actor is None or actor.rol != RolUsuario.ADMIN:
        raise PermissionError("Solo un ADMIN puede editar los datos de la empresa.")

    # Sin `normalizar_nombre` (mayúsculas forzadas) a propósito -- una razón
    # social lleva casing/puntuación legal propia ("Papyrus Soluciones
    # Integrales S.A.S."), a diferencia del nombre del Conjunto, que sí es
    # un identificador corto normalizado. Solo se colapsan espacios.
    razon_social_limpia = re.sub(r"\s+", " ", (razon_social or "")).strip()
    if not razon_social_limpia:
        raise ValueError("La razón social no puede quedar vacía.")

    valores = {
        "razon_social": razon_social_limpia,
        "nit": (nit or "").strip() or None,
        "direccion": (direccion or "").strip() or None,
        "email_contacto": (email_contacto or "").strip() or None,
        "telefono_contacto": (telefono_contacto or "").strip() or None,
    }

    fila = _fila_vigente(session)
    if fila is None:
        fila = ConfiguracionEmpresa(id=ID_SINGLETON, **valores)
        session.add(fila)
        try:
            session.flush()
        except IntegrityError:
            # Carrera: otro ADMIN ya creó la fila (misma PK fija,
            # ID_SINGLETON) -- mismo patrón que
            # `configuracion_conjunto_service.renombrar_conjunto`.
            session.rollback()
            fila = session.get(ConfiguracionEmpresa, ID_SINGLETON)
            for campo, valor in valores.items():
                setattr(fila, campo, valor)
    else:
        for campo, valor in valores.items():
            setattr(fila, campo, valor)

    session.flush()
    return obtener_datos_empresa(session)
