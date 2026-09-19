# -*- coding: utf-8 -*-
"""
Servicio de dominio de ConfiguracionConjunto (Seam A).

Único valor global, editable solo por ADMIN (`.scratch/apartamento-catalogo-
confirmacion/spec.md`). Renombrar propaga a las 804 filas de `Apartamento`
que ya comparten el nombre anterior, para que ninguna quede desincronizada.
"""

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .apartamento import Apartamento
from .configuracion_conjunto import ID_SINGLETON, ConfiguracionConjunto
from .texto import normalizar_nombre
from .usuario import RolUsuario, Usuario

# Misma forma canónica que `Apartamento.conjunto` (`normalizar_nombre`) -- así
# el nombre vigente siempre compara/sincroniza igual contra la terna del
# Apartamento, sin importar el casing con que un ADMIN lo haya escrito.
NOMBRE_CONJUNTO_POR_DEFECTO = normalizar_nombre("El Club")

# Horarios que ya se mostraban hardcodeados en `/ayuda` antes de este
# feature (grilling 2026-09-18, issue 350) -- el cliente confirmó que
# siguen vigentes, así que son el default cuando ningún ADMIN los editó
# todavía.
HORARIO_LUNES_VIERNES_POR_DEFECTO = "9:30 AM - 7:30 PM"
HORARIO_SABADOS_POR_DEFECTO = "9:30 AM - 2:00 PM"
HORARIO_DOMINGOS_POR_DEFECTO = "2:00 PM - 6:00 PM"


@dataclass(frozen=True)
class DatosOperativosConjunto:
    horario_lunes_viernes: str
    horario_sabados: str
    horario_domingos: str
    # Cadena vacía si ningún ADMIN lo configuró -- a diferencia de los
    # horarios (que sí tienen default en código), el número de WhatsApp NO
    # tiene un default propio acá: quien llama (capa web) decide si cae al
    # de siempre (`WHATSAPP_SOPORTE_NUMERO`, variable de entorno) -- el
    # dominio no depende de esa capa (mismo criterio documentado en
    # `notificacion_service.py` sobre `base_url`).
    numero_whatsapp: str


def _fila_vigente(session: Session) -> ConfiguracionConjunto | None:
    return session.get(ConfiguracionConjunto, ID_SINGLETON)


def obtener_nombre_conjunto(session: Session) -> str:
    """El nombre vigente del Conjunto -- personalizado si algún ADMIN ya lo
    renombró, si no el default."""
    fila = _fila_vigente(session)
    return fila.nombre if fila is not None else NOMBRE_CONJUNTO_POR_DEFECTO


def obtener_datos_operativos(session: Session) -> DatosOperativosConjunto:
    """Horarios de atención y WhatsApp de soporte vigentes -- personalizados
    si algún ADMIN ya los editó, si no los defaults de arriba (`numero_
    whatsapp` cae a cadena vacía, sin default propio -- ver el dataclass)."""
    fila = _fila_vigente(session)
    if fila is None:
        return DatosOperativosConjunto(
            horario_lunes_viernes=HORARIO_LUNES_VIERNES_POR_DEFECTO,
            horario_sabados=HORARIO_SABADOS_POR_DEFECTO,
            horario_domingos=HORARIO_DOMINGOS_POR_DEFECTO,
            numero_whatsapp="",
        )
    return DatosOperativosConjunto(
        horario_lunes_viernes=fila.horario_lunes_viernes or HORARIO_LUNES_VIERNES_POR_DEFECTO,
        horario_sabados=fila.horario_sabados or HORARIO_SABADOS_POR_DEFECTO,
        horario_domingos=fila.horario_domingos or HORARIO_DOMINGOS_POR_DEFECTO,
        numero_whatsapp=fila.numero_whatsapp or "",
    )


def actualizar_datos_operativos(
    session: Session,
    *,
    horario_lunes_viernes: str,
    horario_sabados: str,
    horario_domingos: str,
    numero_whatsapp: str,
    actor: Usuario,
) -> DatosOperativosConjunto:
    """Fija horarios de atención y WhatsApp de soporte. No toca `nombre`
    (responsabilidad exclusiva de `renombrar_conjunto`) -- si hace falta
    crear la fila porque nunca existió, se usa el nombre por defecto, nunca
    uno vacío.

    Raises:
        PermissionError: si `actor` no es un ADMIN.
    """
    if actor is None or actor.rol != RolUsuario.ADMIN:
        raise PermissionError("Solo un ADMIN puede editar los datos operativos del Conjunto.")

    valores = {
        "horario_lunes_viernes": (horario_lunes_viernes or "").strip() or None,
        "horario_sabados": (horario_sabados or "").strip() or None,
        "horario_domingos": (horario_domingos or "").strip() or None,
        "numero_whatsapp": (numero_whatsapp or "").strip() or None,
    }

    fila = _fila_vigente(session)
    if fila is None:
        fila = ConfiguracionConjunto(
            id=ID_SINGLETON, nombre=NOMBRE_CONJUNTO_POR_DEFECTO, **valores
        )
        session.add(fila)
        try:
            session.flush()
        except IntegrityError:
            # Carrera: mismo patrón que `renombrar_conjunto`.
            session.rollback()
            fila = session.get(ConfiguracionConjunto, ID_SINGLETON)
            for campo, valor in valores.items():
                setattr(fila, campo, valor)
    else:
        for campo, valor in valores.items():
            setattr(fila, campo, valor)

    session.flush()
    return obtener_datos_operativos(session)


def renombrar_conjunto(session: Session, nuevo_nombre: str, actor: Usuario) -> str:
    """Fija el nombre vigente del Conjunto y sincroniza `Apartamento.conjunto`
    para las filas que ya tenían el nombre anterior.

    Raises:
        PermissionError: si `actor` no es un ADMIN.
        ValueError: si `nuevo_nombre` queda vacío tras `strip()`.
    """
    if actor is None or actor.rol != RolUsuario.ADMIN:
        raise PermissionError("Solo un ADMIN puede renombrar el Conjunto.")

    nombre_limpio = normalizar_nombre(nuevo_nombre) or ""
    if not nombre_limpio:
        raise ValueError("El nombre del Conjunto no puede quedar vacío.")

    nombre_anterior = obtener_nombre_conjunto(session)

    fila = _fila_vigente(session)
    if fila is None:
        fila = ConfiguracionConjunto(id=ID_SINGLETON, nombre=nombre_limpio)
        session.add(fila)
        try:
            session.flush()
        except IntegrityError:
            # Carrera: otro ADMIN ya creó la fila (misma PK fija,
            # ID_SINGLETON) -- se reintenta como UPDATE sobre esa fila, mismo
            # patrón que `persona_service.get_or_create_persona`.
            session.rollback()
            fila = session.get(ConfiguracionConjunto, ID_SINGLETON)
            fila.nombre = nombre_limpio
    else:
        fila.nombre = nombre_limpio

    if nombre_anterior != nombre_limpio:
        session.query(Apartamento).filter(
            Apartamento.conjunto == nombre_anterior
        ).update({"conjunto": nombre_limpio}, synchronize_session=False)

    session.flush()
    return nombre_limpio
