# -*- coding: utf-8 -*-
"""
Importador espejo v1 → v2 (`.scratch/importador-v1-espejo`, ticket 06) --
fotos: se copian del bucket privado de la v1 al de la v2 (vía el puerto
copiador) y quedan como `PaqueteFoto`. Seam: `sincronizar_desde_v1`, contra el
Postgres efímero, con un copiador falso en memoria.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.importador_v1_service import (
    ClienteV1,
    FotoV1,
    InstantaneaV1,
    ModoSincronizacion,
    PaqueteV1,
    sincronizar_desde_v1,
)
from app.domain.paquete import Paquete
from app.domain.paquete_foto import PaqueteFoto

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
CLIENTE = ClienteV1(id="c-1", telefono="+573001112233", nombre="Juan Perez")
PAQUETE = PaqueteV1(
    id=1, cliente_id="c-1", tracking_number="HSZN", guide_number=None, display_name=None,
    estado="RECIBIDO", package_type="NORMAL", package_condition="BUENO",
    announced_at=T0, received_at=T0 + timedelta(hours=1),
)


class CopiadorFalso:
    """Copia "en memoria": la URL de destino se deriva de la key de origen,
    como el copiador real. `fallar` simula errores de S3 para ciertas keys."""

    def __init__(self, fallar=()):
        self.copiadas = []
        self._fallar = set(fallar)

    def copiar(self, s3_key_origen: str) -> str:
        if s3_key_origen in self._fallar:
            raise RuntimeError("S3 no respondió")
        self.copiadas.append(s3_key_origen)
        return "https://bucket-v2.test/fotos/legacy_" + s3_key_origen.rsplit("/", 1)[-1]


def _foto(id_v1, paquete_id=1):
    return FotoV1(id=id_v1, paquete_id=paquete_id, s3_key=f"2026/09/01/packages/HSZN/foto_{id_v1}.webp", creada_en=T0)


def _sincronizar(db_session, fotos, copiador, modo=ModoSincronizacion.NORMAL):
    return sincronizar_desde_v1(
        db_session,
        InstantaneaV1(clientes=[CLIENTE], paquetes=[PAQUETE], fotos=list(fotos)),
        copiador_fotos=copiador,
        modo=modo,
    )


def test_las_fotos_de_la_v1_quedan_en_el_paquete_con_su_url_de_la_v2(db_session):
    copiador = CopiadorFalso()

    reporte = _sincronizar(db_session, [_foto(10), _foto(11)], copiador)

    assert reporte.fotos.creados == 2
    paquete = db_session.query(Paquete).one()
    urls = sorted(f.url for f in db_session.query(PaqueteFoto).filter_by(paquete_id=paquete.id))
    assert urls == [
        "https://bucket-v2.test/fotos/legacy_foto_10.webp",
        "https://bucket-v2.test/fotos/legacy_foto_11.webp",
    ]


def test_una_foto_ya_importada_no_se_vuelve_a_copiar(db_session):
    _sincronizar(db_session, [_foto(10)], CopiadorFalso())
    copiador = CopiadorFalso()

    reporte = _sincronizar(db_session, [_foto(10)], copiador)

    assert copiador.copiadas == []
    assert reporte.fotos.sin_cambios == 1
    assert db_session.query(PaqueteFoto).count() == 1


def test_una_foto_que_falla_se_reporta_no_frena_y_se_reintenta(db_session):
    reporte = _sincronizar(db_session, [_foto(10), _foto(11)], CopiadorFalso(fallar={_foto(10).s3_key}))

    assert reporte.fotos.creados == 1
    assert any("foto v1 10" in e for e in reporte.errores)

    reporte = _sincronizar(db_session, [_foto(10), _foto(11)], CopiadorFalso())

    assert reporte.fotos.creados == 1
    assert db_session.query(PaqueteFoto).count() == 2


def test_simular_no_llama_al_copiador(db_session):
    copiador = CopiadorFalso()

    reporte = _sincronizar(db_session, [_foto(10)], copiador, modo=ModoSincronizacion.SIMULAR)

    assert copiador.copiadas == []
    assert reporte.fotos.creados == 1
    assert db_session.query(PaqueteFoto).count() == 0


def test_una_foto_de_un_paquete_no_importado_se_reporta(db_session):
    reporte = _sincronizar(db_session, [_foto(10, paquete_id=999)], CopiadorFalso())

    assert reporte.fotos.creados == 0
    assert any("foto v1 10" in e for e in reporte.errores)


def test_las_fotos_nativas_del_paquete_no_se_tocan(db_session):
    _sincronizar(db_session, [], CopiadorFalso())
    paquete = db_session.query(Paquete).one()
    db_session.add(PaqueteFoto(paquete_id=paquete.id, url="https://bucket-v2.test/fotos/nativa.webp"))
    db_session.flush()

    _sincronizar(db_session, [_foto(10)], CopiadorFalso())

    assert db_session.query(PaqueteFoto).filter_by(url="https://bucket-v2.test/fotos/nativa.webp").count() == 1
