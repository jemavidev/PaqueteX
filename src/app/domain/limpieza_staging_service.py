# -*- coding: utf-8 -*-
"""
Limpieza previa del staging para el importador espejo v1 → v2
(`.scratch/importador-v1-espejo`, ticket 10; grilling 2026-09-23, preguntas 4-5).

Deja el staging listo para la primera pasada: borra TODO lo de residentes y
paquetes (datos de prueba) y conserva lo que no se puede reconstruir desde la
v1 -- usuarios, tarifas, motivos, plantillas, proveedores, configuración del
conjunto y de la empresa, el censo de `apartamentos` y los contactos externos.

Se niega a correr si ya hay algo importado de la v1 (`origen_v1_id`): una vez
que el espejo está poblado, esta limpieza ya no tiene sentido y borrarlo por
error obligaría a reimportar todo.
"""

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

# En orden de borrado (hijos antes que padres, según las FK reales).
TABLAS_A_BORRAR = (
    "registros_sms",
    "cobros",
    "paquete_fotos",
    "movimientos_saldo_contra_entrega",
    "persona_preferencia_notificacion",
    "ocupantes",
    "paquetes",
    "otps_cliente",
    "personas",
)

TABLAS_CONSERVADAS = (
    "usuarios",
    "password_resets",
    "tarifas_cobro",
    "motivos_cancelacion",
    "motivos_bloqueo",
    "motivos_anulacion_cobro",
    "plantillas_notificacion",
    "plantillas_notificacion_historial",
    "proveedores_notificacion_config",
    "proveedores_notificacion_config_historial",
    "proveedores_credenciales_historial",
    "configuracion_conjunto",
    "configuracion_empresa",
    "apartamentos",
    "contactos_externos",
    "contactos_externos_telefonos",
    "contactos_externos_whatsapps",
    "fuentes_contactos_externos",
)

_TABLAS_CON_ORIGEN_V1 = ("personas", "paquetes", "paquete_fotos", "usuarios")


class LimpiezaRechazada(RuntimeError):
    """Ya hay datos importados de la v1: la limpieza no corre."""


@dataclass
class ResumenLimpieza:
    simular: bool
    borrados: dict[str, int] = field(default_factory=dict)
    conservados: dict[str, int] = field(default_factory=dict)


def limpiar_datos_de_residentes(session: Session, simular: bool = False) -> ResumenLimpieza:
    """Borra los datos de residentes y paquetes. No hace commit (lo decide el
    llamador). Con `simular=True` solo cuenta."""
    importados = {
        tabla: _contar(session, tabla, "origen_v1_id IS NOT NULL") for tabla in _TABLAS_CON_ORIGEN_V1
    }
    if any(importados.values()):
        raise LimpiezaRechazada(
            "Ya hay datos importados de la v1 "
            + ", ".join(f"{t}={n}" for t, n in importados.items() if n)
            + "; la limpieza previa solo corre antes de la primera pasada."
        )
    resumen = ResumenLimpieza(
        simular=simular,
        borrados={tabla: _contar(session, tabla) for tabla in TABLAS_A_BORRAR},
        conservados={tabla: _contar(session, tabla) for tabla in TABLAS_CONSERVADAS},
    )
    if not simular:
        for tabla in TABLAS_A_BORRAR:
            session.execute(text(f"DELETE FROM {tabla}"))  # noqa: S608 -- nombres fijos de este módulo
        session.expire_all()
    return resumen


def _contar(session: Session, tabla: str, condicion: str = "TRUE") -> int:
    return session.execute(text(f"SELECT count(*) FROM {tabla} WHERE {condicion}")).scalar_one()  # noqa: S608
