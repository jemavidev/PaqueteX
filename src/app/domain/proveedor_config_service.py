# -*- coding: utf-8 -*-
"""
Service de `ProveedorConfig` — habilitado/orden de precedencia por
`(canal, proveedor)` (`.scratch/administracion-proveedores/spec.md`, issue
01). Nunca toca credenciales -- esas siguen solo en `.env` del servidor
(Fase 2, issue 04/05).

Sin ruta HTTP ni pantalla en esta rebanada (issue 03) -- este service es el
seam que el refactor de la cadena de failover (issue 02) y la pantalla van a
consumir, sin volver a tocar el modelo de datos.
"""

import uuid
from typing import TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .preferencia_notificacion import CanalNotificacion
from .proveedor_config import ProveedorConfig
from .proveedor_config_historial import ProveedorConfigHistorial
from .proveedor_credencial_historial import ProveedorCredencialHistorial

_T = TypeVar("_T")


def _buscar_config(session: Session, canal: CanalNotificacion, proveedor: str) -> ProveedorConfig | None:
    return (
        session.query(ProveedorConfig)
        .filter(ProveedorConfig.canal == canal.value, ProveedorConfig.proveedor == proveedor)
        .one_or_none()
    )


def listar_config(session: Session, canal: CanalNotificacion) -> list[ProveedorConfig]:
    """Las filas de `canal`, ordenadas por precedencia (`orden` ascendente,
    NULLs al final -- solo relevante para un canal con más de un proveedor;
    hoy únicamente SMS)."""
    return (
        session.query(ProveedorConfig)
        .filter(ProveedorConfig.canal == canal.value)
        .order_by(ProveedorConfig.orden.is_(None), ProveedorConfig.orden)
        .all()
    )


def habilitado_orden_efectivos(config: ProveedorConfig | None) -> tuple[bool, int | None]:
    """`(habilitado, orden)` efectivos de `config` -- `None` (sin fila en BD
    para un proveedor del catálogo; no debería pasar en producción, la
    migración de siembra los crea todos, pero cubre un proveedor agregado al
    catálogo después de esa migración, antes de su primer guardado
    explícito) se asume `habilitado=True`, `orden=None` -- mismo
    comportamiento implícito que existía antes de esta feature (la sola
    presencia de credenciales bastaba). Fuente única para `armar_candidatos`
    (cadena real de envío) y `admin_proveedores._filas_proveedores` (lo que
    se muestra en pantalla) -- las dos deben coincidir siempre."""
    if config is None:
        return True, None
    return config.habilitado, config.orden


def armar_candidatos(
    session: Session,
    canal: CanalNotificacion,
    proveedores: list[tuple[str, bool, _T]],
) -> list[tuple[bool, _T]]:
    """Combina `proveedores` -- `[(clave_del_catálogo, esta_configurado,
    sender), ...]`, YA en el orden por defecto del catálogo (issue 01,
    `proveedores_catalogo.py`) -- con el habilitado/orden guardado en BD
    (issue 02), lista para pasarle directo a `sms_failover.construir_sender()`.

    Un proveedor entra a la cadena SOLO si las dos condiciones son ciertas a
    la vez: habilitado en BD Y `esta_configurado` (`.configurado()`/
    `.sns_habilitado()` de cada proveedor, con credenciales completas en
    `.env`) -- ver `.scratch/administracion-proveedores/spec.md`, decisión
    "habilitado (BD) Y configurado (.env)".

    Sin `orden` explícito (ver `habilitado_orden_efectivos`), el proveedor
    conserva su posición relativa en `proveedores` -- el sort de Python es
    estable, así que los empates caen de vuelta al orden del catálogo sin
    necesidad de consultarlo aparte."""
    config_por_proveedor = {
        c.proveedor: c for c in listar_config(session, canal)
    }

    def _clave_orden(item: tuple[str, bool, _T]) -> tuple[bool, int]:
        clave, _esta_configurado, _sender = item
        _habilitado, orden = habilitado_orden_efectivos(config_por_proveedor.get(clave))
        return (orden is None, orden if orden is not None else 0)

    ordenados = sorted(proveedores, key=_clave_orden)

    resultado = []
    for clave, esta_configurado, sender in ordenados:
        habilitado, _orden = habilitado_orden_efectivos(config_por_proveedor.get(clave))
        resultado.append((habilitado and esta_configurado, sender))
    return resultado


def guardar_habilitado_orden(
    session: Session,
    canal: CanalNotificacion,
    proveedor: str,
    habilitado: bool,
    orden: int | None = None,
    usuario_id: uuid.UUID | None = None,
) -> ProveedorConfig:
    """Crea o actualiza la fila de `(canal, proveedor)`, y deja un registro
    en `ProveedorConfigHistorial` por cada guardado exitoso -- append-only,
    nunca se edita ni se borra.

    `usuario_id` es opcional (default `None`): un historial con
    `usuario_id=NULL` es honesto para un caller sin actor real (tests de
    dominio, la migración de siembra), no un dato inventado -- mismo
    criterio que `notificacion_service.guardar_plantilla`.

    A diferencia de las credenciales (Fase 2), habilitado/orden nunca es
    secreto -- el historial guarda el valor COMPLETO de antes/después.

    Carrera (dos guardados simultáneos del mismo proveedor NUEVO -- mismo
    patrón que `notificacion_service.guardar_plantilla`): si el `INSERT`
    choca contra `uq_proveedores_config_canal_proveedor`, se reintenta como
    UPDATE sobre la fila que la otra transacción ya creó, en vez de propagar
    el `IntegrityError`."""
    config = _buscar_config(session, canal, proveedor)
    if config is None:
        config = ProveedorConfig(canal=canal.value, proveedor=proveedor)
        session.add(config)
        config.habilitado = habilitado
        config.orden = orden
        config.updated_by = usuario_id
        try:
            session.flush()
            habilitado_anterior, orden_anterior = None, None
        except IntegrityError:
            session.rollback()
            config = _buscar_config(session, canal, proveedor)
            habilitado_anterior, orden_anterior = config.habilitado, config.orden
            config.habilitado = habilitado
            config.orden = orden
            config.updated_by = usuario_id
            session.flush()
    else:
        habilitado_anterior, orden_anterior = config.habilitado, config.orden
        config.habilitado = habilitado
        config.orden = orden
        config.updated_by = usuario_id
        session.flush()

    session.add(
        ProveedorConfigHistorial(
            canal=canal.value,
            proveedor=proveedor,
            usuario_id=usuario_id,
            habilitado_anterior=habilitado_anterior,
            habilitado_nuevo=habilitado,
            orden_anterior=orden_anterior,
            orden_nuevo=orden,
        )
    )
    session.flush()
    return config


def registrar_cambio_credencial(
    session: Session,
    canal: CanalNotificacion,
    proveedor: str,
    campo: str,
    usuario_id: uuid.UUID | None = None,
) -> None:
    """Deja un registro en `ProveedorCredencialHistorial` -- issue 05, Fase
    2. SOLO el nombre de `campo` (una variable de entorno del allowlist,
    nunca su valor); llamar DESPUÉS de que `app/infra/deploy_ssh.py::
    aplicar_credenciales_proveedor` confirme éxito, nunca antes -- un
    registro de auditoría de un cambio que en realidad falló sería peor que
    no tener registro."""
    session.add(
        ProveedorCredencialHistorial(
            canal=canal.value, proveedor=proveedor, campo=campo, usuario_id=usuario_id
        )
    )
    session.flush()
