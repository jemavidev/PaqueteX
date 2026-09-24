# -*- coding: utf-8 -*-
"""
Importador espejo v1 → v2 (`.scratch/importador-v1-espejo`, ticket 03) --
autoría del staff, tomada de `package_history.changed_by` (lo único que la v1
registra). Seam: `sincronizar_desde_v1`, contra el Postgres efímero.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.importador_v1_service import (
    ClienteV1,
    HistorialV1,
    InstantaneaV1,
    PaqueteV1,
    UsuarioV1,
    sincronizar_desde_v1,
)
from app.domain.motivo_cancelacion import MotivoCancelacion
from app.domain.paquete import Paquete
from app.domain.paquete_timeline_service import timeline_de_paquete
from app.domain.usuario import RolUsuario, Usuario

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
CLIENTE = ClienteV1(id="c-1", telefono="+573001112233", nombre="Juan Perez")
USUARIOS_V1 = [
    UsuarioV1(username="rafael", email="rafael@papyrus.test", nombre="Rafael", rol="OPERADOR"),
    UsuarioV1(username="jveyes", email="jesus@papyrus.test", nombre="Jesus", rol="ADMIN"),
    UsuarioV1(username="jesus", email="otro-correo-de-jesus@papyrus.test", nombre="jesus", rol="OPERADOR"),
    UsuarioV1(username="MARIANELLA", email="marianella@papyrus.test", nombre="Marianella R", rol="OPERADOR"),
    UsuarioV1(username="test_cache", email="cache@papyrus.test", nombre="Test", rol="ADMIN"),
]


def _staff_v2(db_session):
    """Los usuarios que ya existen en la v2 (creados a mano, no por el importador)."""
    rafael = Usuario(nombre="RAFAEL TORRES", email="rafael@papyrus.test", rol=RolUsuario.OPERADOR)
    jesus = Usuario(nombre="JESUS VILLALOBOS", email="jesus@papyrus.test", rol=RolUsuario.ADMIN)
    db_session.add_all([rafael, jesus])
    db_session.flush()
    return rafael, jesus


def _paquete(id_v1=1, estado="ENTREGADO", **extra):
    base = dict(
        id=id_v1, cliente_id="c-1", tracking_number=f"C{id_v1:03d}", guide_number=None,
        display_name=None, estado=estado, package_type="NORMAL", package_condition="BUENO",
        announced_at=T0, received_at=T0 + timedelta(hours=1),
        delivered_at=T0 + timedelta(days=1) if estado == "ENTREGADO" else None,
        cancelled_at=T0 + timedelta(days=1) if estado == "CANCELADO" else None,
    )
    base.update(extra)
    return PaqueteV1(**base)


def _historial(paquete_id, estado, quien, horas=1, motivo=None):
    return HistorialV1(
        paquete_id=paquete_id, estado=estado, changed_by=quien,
        changed_at=T0 + timedelta(hours=horas), motivo_cancelacion=motivo,
    )


def _sincronizar(db_session, paquetes, historial):
    return sincronizar_desde_v1(
        db_session,
        InstantaneaV1(clientes=[CLIENTE], usuarios=USUARIOS_V1, paquetes=paquetes, historial=historial),
    )


def test_quien_recibio_se_enlaza_al_usuario_existente_por_email(db_session):
    rafael, _ = _staff_v2(db_session)

    _sincronizar(db_session, [_paquete(estado="RECIBIDO")], [_historial(1, "RECIBIDO", "rafael")])

    paquete = db_session.query(Paquete).one()
    assert paquete.received_by_usuario_id == rafael.id
    assert paquete.announced_by_usuario_id is None


def test_jesus_y_jveyes_son_el_mismo_usuario(db_session):
    _, jesus = _staff_v2(db_session)

    _sincronizar(
        db_session,
        [_paquete(1, estado="RECIBIDO"), _paquete(2, estado="RECIBIDO")],
        [_historial(1, "RECIBIDO", "jesus"), _historial(2, "RECIBIDO", "jveyes")],
    )

    assert {p.received_by_usuario_id for p in db_session.query(Paquete)} == {jesus.id}
    assert db_session.query(Usuario).count() == 2  # no se creó un "jesus" aparte


def test_un_operador_que_no_existe_en_la_v2_se_crea_inactivo_y_sin_acceso(db_session):
    _staff_v2(db_session)

    _sincronizar(db_session, [_paquete(estado="RECIBIDO")], [_historial(1, "RECIBIDO", "MARIANELLA")])

    marianella = db_session.query(Usuario).filter_by(origen_v1_id="MARIANELLA").one()
    assert marianella.nombre == "Marianella R"
    assert marianella.activo is False
    assert marianella.password_hash is None
    assert marianella.email is None
    assert db_session.query(Paquete).one().received_by_usuario_id == marianella.id


def test_operator_1_es_el_operador_tecnico_de_entregas_y_cancelaciones(db_session):
    _staff_v2(db_session)
    if not db_session.query(MotivoCancelacion).filter_by(etiqueta="Otro").count():
        db_session.add(MotivoCancelacion(etiqueta="Otro"))
        db_session.flush()

    _sincronizar(
        db_session,
        [_paquete(1, estado="ENTREGADO"), _paquete(2, estado="CANCELADO")],
        [
            _historial(1, "RECIBIDO", "rafael"),
            _historial(1, "ENTREGADO", "operator_1", horas=24),
            _historial(2, "RECIBIDO", "rafael"),
            _historial(2, "CANCELADO", "operator_1", horas=24, motivo="otro"),
        ],
    )

    tecnico = db_session.query(Usuario).filter_by(origen_v1_id="operator_1").one()
    assert tecnico.nombre == "Staff Papyrus"
    assert tecnico.activo is False
    assert tecnico.password_hash is None
    entregado = db_session.query(Paquete).filter_by(origen_v1_id="1").one()
    cancelado = db_session.query(Paquete).filter_by(origen_v1_id="2").one()
    assert entregado.delivered_by_usuario_id == tecnico.id
    assert cancelado.cancelled_by_usuario_id == tecnico.id
    assert cancelado.cancel_reason == "Otro"  # la etiqueta del catálogo de la v2


def test_usuarios_de_la_v1_sin_actividad_no_se_crean(db_session):
    _staff_v2(db_session)

    _sincronizar(db_session, [_paquete(estado="RECIBIDO")], [_historial(1, "RECIBIDO", "rafael")])

    assert db_session.query(Usuario).filter_by(origen_v1_id="test_cache").count() == 0


def test_un_changed_by_desconocido_queda_sin_autor_y_se_reporta(db_session):
    _staff_v2(db_session)

    reporte = _sincronizar(db_session, [_paquete(estado="RECIBIDO")], [_historial(1, "RECIBIDO", "fantasma")])

    assert db_session.query(Paquete).one().received_by_usuario_id is None
    assert any("fantasma" in e for e in reporte.errores)


def test_la_linea_de_tiempo_muestra_quien_hizo_cada_paso(db_session):
    rafael, _ = _staff_v2(db_session)

    _sincronizar(
        db_session,
        [_paquete(estado="ENTREGADO")],
        [_historial(1, "RECIBIDO", "rafael"), _historial(1, "ENTREGADO", "operator_1", horas=24)],
    )

    pasos = timeline_de_paquete(db_session, db_session.query(Paquete).one())
    actores = " ".join(str(p) for p in pasos)
    assert "RAFAEL TORRES" in actores
    assert "Staff Papyrus" in actores


def test_segunda_pasada_no_duplica_usuarios_ni_cambia_la_autoria(db_session):
    _staff_v2(db_session)
    paquetes = [_paquete(estado="ENTREGADO")]
    historial = [_historial(1, "RECIBIDO", "MARIANELLA"), _historial(1, "ENTREGADO", "operator_1", horas=24)]
    _sincronizar(db_session, paquetes, historial)

    reporte = _sincronizar(db_session, paquetes, historial)

    assert db_session.query(Usuario).count() == 4
    assert reporte.paquetes.sin_cambios == 1


def test_el_usuario_tecnico_con_el_nombre_viejo_pasa_a_staff_papyrus(db_session):
    """Issue 398 (`.scratch/pendientes-cliente`): el Usuario técnico de `operator_1` que ya
    existía con el nombre anterior se renombra solo en la siguiente pasada."""
    _staff_v2(db_session)
    db_session.add(Usuario(origen_v1_id="operator_1", nombre="Operador v1 (sin identificar)",
                           rol=RolUsuario.OPERADOR, activo=False))
    db_session.flush()

    _sincronizar(db_session, [_paquete(estado="ENTREGADO")],
                 [_historial(1, "ENTREGADO", "operator_1", horas=24)])

    tecnico = db_session.query(Usuario).filter_by(origen_v1_id="operator_1").one()
    assert tecnico.nombre == "Staff Papyrus"
    assert db_session.query(Paquete).one().delivered_by_usuario_id == tecnico.id


def test_los_usuarios_reales_enlazados_no_se_renombran(db_session):
    rafael, _ = _staff_v2(db_session)

    _sincronizar(db_session, [_paquete(estado="RECIBIDO")], [_historial(1, "RECIBIDO", "rafael")])

    db_session.refresh(rafael)
    assert rafael.nombre == "RAFAEL TORRES"
