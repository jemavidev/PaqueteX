# -*- coding: utf-8 -*-
"""
Importador espejo v1 → v2 (`.scratch/importador-v1-espejo`, ticket 07) --
borrado reflejado: lo que desaparece de la v1 desaparece de la v2, salvo que
desaparezca más del 5 % (lectura vacía o rota de la v1), en cuyo caso la pasada
aborta sin escribir nada. Seam: `sincronizar_desde_v1`, contra el Postgres
efímero.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.cobro import Cobro
from app.domain.importador_v1_service import (
    AnuncioV1,
    ClienteV1,
    FotoV1,
    HistorialV1,
    InstantaneaV1,
    PaqueteV1,
    sincronizar_desde_v1,
)
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_foto import PaqueteFoto
from app.domain.persona import Persona
from app.domain.preferencia_notificacion import CanalNotificacion, PersonaPreferenciaNotificacion

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
# 21 clientes y 21 paquetes: que desaparezca UNO es < 5 % (1/21 = 4,8 %).
CLIENTES = [ClienteV1(id=f"c-{i}", telefono=f"+5730011122{i:02d}", nombre=f"Cliente {i}") for i in range(21)]


def _paquete(i):
    return PaqueteV1(
        id=i, cliente_id=f"c-{i}", tracking_number=f"C{i:03d}", guide_number=None, display_name=None,
        estado="ENTREGADO", package_type="NORMAL", package_condition="BUENO", announced_at=T0,
        received_at=T0 + timedelta(hours=1), delivered_at=T0 + timedelta(days=1), total_amount=Decimal("1500"),
    )


PAQUETES = [_paquete(i) for i in range(21)]


class CopiadorFalso:
    def copiar(self, s3_key_origen):
        return "https://bucket-v2.test/" + s3_key_origen


def _sincronizar(db_session, clientes=CLIENTES, paquetes=PAQUETES, fotos=(), anuncios=()):
    historial = [
        HistorialV1(paquete_id=p.id, estado="ENTREGADO", changed_by="operator_1", changed_at=T0) for p in paquetes
    ]
    return sincronizar_desde_v1(
        db_session,
        InstantaneaV1(
            clientes=list(clientes), paquetes=list(paquetes), historial=historial,
            fotos=list(fotos), anuncios=list(anuncios),
        ),
        copiador_fotos=CopiadorFalso(),
    )


def test_un_paquete_que_desaparece_de_la_v1_se_borra_con_su_cobro_y_sus_fotos(db_session):
    fotos = [FotoV1(id=i, paquete_id=i, s3_key=f"f/{i}.webp") for i in range(21)]
    _sincronizar(db_session, fotos=fotos)

    # En la v1, borrar el paquete 0 borra también su foto.
    reporte = _sincronizar(db_session, paquetes=PAQUETES[1:], fotos=fotos[1:])

    assert reporte.paquetes.borrados == 1
    assert reporte.cobros.borrados == 1
    assert reporte.fotos.borrados == 1
    assert db_session.query(Paquete).filter_by(origen_v1_id="0").count() == 0
    assert db_session.query(Paquete).count() == 20
    assert db_session.query(Cobro).count() == 20
    assert db_session.query(PaqueteFoto).count() == 20


def test_un_anuncio_vencido_que_la_v1_borra_desaparece_de_la_v2(db_session):
    anuncio = AnuncioV1(id="a-1", cliente_id="c-0", tracking_code="W6JQ", guide_number=None,
                        nombre_destinatario=None, announced_at=T0)
    _sincronizar(db_session, anuncios=[anuncio])

    reporte = _sincronizar(db_session, anuncios=[])

    assert reporte.paquetes.borrados == 1
    assert db_session.query(Paquete).filter_by(access_code="W6JQ").count() == 0


def test_un_anuncio_que_la_v1_convirtio_en_paquete_no_cuenta_como_borrado(db_session):
    anuncio = AnuncioV1(id="a-1", cliente_id="c-0", tracking_code="W6JQ", guide_number=None,
                        nombre_destinatario=None, announced_at=T0)
    # Solo el anuncio está importado: si su conversión contara como
    # desaparición, sería el 100 % de los paquetes y la pasada abortaría.
    _sincronizar(db_session, paquetes=[], anuncios=[anuncio])
    nuevo = PaqueteV1(
        id=99, cliente_id="c-0", tracking_number="W6JQ", guide_number=None, display_name=None,
        estado="RECIBIDO", package_type="NORMAL", package_condition="BUENO", announced_at=T0,
        received_at=T0 + timedelta(hours=1), anuncio_id="a-1",
    )

    reporte = _sincronizar(db_session, paquetes=[nuevo], anuncios=[])

    assert not reporte.alertas
    assert reporte.paquetes.borrados == 0
    assert db_session.query(Paquete).filter_by(access_code="W6JQ").one().estado == EstadoPaquete.RECIBIDO


def test_un_cliente_que_desaparece_se_borra_con_sus_preferencias(db_session):
    clientes = CLIENTES + [ClienteV1(id="c-sin-paquetes", telefono="+573009990000", nombre="Sin paquetes")]
    _sincronizar(db_session, clientes=clientes)
    # Preferencia que el residente configuró en la v2 (el importador no importa las de la v1).
    persona = db_session.query(Persona).filter_by(origen_v1_id="c-sin-paquetes").one()
    db_session.add(PersonaPreferenciaNotificacion(persona_id=persona.id, canal=CanalNotificacion.SMS,
                                                  evento="ANUNCIADO", activo=False))
    db_session.flush()

    reporte = _sincronizar(db_session, clientes=CLIENTES)

    assert reporte.personas.borrados == 1
    assert db_session.query(Persona).filter_by(origen_v1_id="c-sin-paquetes").count() == 0
    assert db_session.query(PersonaPreferenciaNotificacion).count() == 0


def test_una_persona_con_paquetes_nativos_solo_se_desvincula(db_session):
    clientes = CLIENTES + [ClienteV1(id="c-x", telefono="+573009990000", nombre="Con nativo")]
    _sincronizar(db_session, clientes=clientes)
    persona = db_session.query(Persona).filter_by(origen_v1_id="c-x").one()
    db_session.add(Paquete(access_code="NATV", announced_by_persona_id=persona.id, recipient_name="Con nativo",
                           estado=EstadoPaquete.ANUNCIADO, announced_at=T0))
    db_session.flush()

    reporte = _sincronizar(db_session, clientes=CLIENTES)

    db_session.refresh(persona)
    assert persona.origen_v1_id is None
    assert persona.nombre == "Con nativo"
    assert reporte.personas.borrados == 0
    assert db_session.query(Paquete).filter_by(access_code="NATV").count() == 1


def test_nunca_se_borra_lo_nativo(db_session):
    _sincronizar(db_session)
    nativa = Persona(telefono="+573009998877", nombre="Nativa")
    db_session.add(nativa)
    db_session.flush()
    db_session.add(Paquete(access_code="NATV", announced_by_persona_id=nativa.id, recipient_name="Nativa",
                           estado=EstadoPaquete.ANUNCIADO, announced_at=T0))
    db_session.flush()

    _sincronizar(db_session, paquetes=PAQUETES[1:])

    assert db_session.query(Persona).filter_by(nombre="Nativa").count() == 1
    assert db_session.query(Paquete).filter_by(access_code="NATV").count() == 1


def test_si_desaparece_mas_del_5_por_ciento_la_pasada_aborta_sin_escribir_nada(db_session):
    _sincronizar(db_session)
    persona = db_session.query(Persona).filter_by(origen_v1_id="c-5").one()
    persona.nombre = "Editado en la v2"
    db_session.flush()

    # La v1 "devuelve" solo 19 de 21 paquetes (9,5 %) y trae un cambio de nombre.
    clientes = [c if c.id != "c-5" else ClienteV1(id="c-5", telefono=c.telefono, nombre="Otro") for c in CLIENTES]
    reporte = _sincronizar(db_session, clientes=clientes, paquetes=PAQUETES[2:])

    assert reporte.alertas
    assert "paquetes" in reporte.alertas[0]
    assert db_session.query(Paquete).count() == 21
    db_session.refresh(persona)
    assert persona.nombre == "Editado en la v2"  # ni siquiera se aplicaron las actualizaciones


def test_una_v1_vacia_nunca_vacia_la_v2(db_session):
    _sincronizar(db_session)

    reporte = _sincronizar(db_session, clientes=[], paquetes=[])

    assert reporte.alertas
    assert db_session.query(Persona).count() == 21
    assert db_session.query(Paquete).count() == 21
