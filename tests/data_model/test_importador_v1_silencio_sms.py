# -*- coding: utf-8 -*-
"""
Importador espejo v1 → v2 (`.scratch/importador-v1-espejo`, decisión
2026-09-24): mientras el espejo está activo (`IMPORTADOR_V1_ESPEJO_ACTIVO=1`),
la v2 no le envía avisos a los paquetes importados de la v1 -- una prueba en
la v2 nunca le escribe a un residente real de la v1. Los paquetes nativos no
cambian. En el corte se quita la variable y todo vuelve a notificar.
Seam: `preparar_notificacion` (el punto por el que pasa todo aviso de paquete).
"""

from datetime import datetime, timezone

import pytest

from app.domain.importador_v1_service import AnuncioV1, ClienteV1, InstantaneaV1, sincronizar_desde_v1
from app.domain.notificacion_service import preparar_notificacion
from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_service import Destinatario, announce

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)


def _importado(db_session):
    sincronizar_desde_v1(
        db_session,
        InstantaneaV1(
            clientes=[ClienteV1(id="c-1", telefono="+573001112233", nombre="Juan V1")],
            anuncios=[AnuncioV1(id="a-1", cliente_id="c-1", tracking_code="ANU1", guide_number=None,
                                nombre_destinatario=None, announced_at=T0)],
        ),
    )
    return db_session.query(Paquete).filter_by(origen_v1_id="anuncio:a-1").one()


def _nativo(db_session):
    return announce(db_session, anunciante_telefono="3004445566", anunciante_nombre="Ana V2",
                    destinatario=Destinatario.yo_mismo())


def test_con_el_espejo_activo_un_paquete_importado_no_se_notifica(db_session, monkeypatch):
    monkeypatch.setenv("IMPORTADOR_V1_ESPEJO_ACTIVO", "1")

    assert preparar_notificacion(db_session, _importado(db_session), EstadoPaquete.ANUNCIADO) is None


def test_con_el_espejo_activo_un_paquete_nativo_se_notifica_igual(db_session, monkeypatch):
    monkeypatch.setenv("IMPORTADOR_V1_ESPEJO_ACTIVO", "1")

    destino, _ = preparar_notificacion(db_session, _nativo(db_session), EstadoPaquete.ANUNCIADO)
    assert destino == "+573004445566"


def test_tras_el_corte_el_paquete_importado_vuelve_a_notificarse(db_session, monkeypatch):
    monkeypatch.delenv("IMPORTADOR_V1_ESPEJO_ACTIVO", raising=False)

    destino, _ = preparar_notificacion(db_session, _importado(db_session), EstadoPaquete.ANUNCIADO)
    assert destino == "+573001112233"


@pytest.mark.parametrize("valor", ["0", "", "no"])
def test_solo_un_1_activa_el_silencio(db_session, monkeypatch, valor):
    monkeypatch.setenv("IMPORTADOR_V1_ESPEJO_ACTIVO", valor)

    assert preparar_notificacion(db_session, _importado(db_session), EstadoPaquete.ANUNCIADO) is not None
