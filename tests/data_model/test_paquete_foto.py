# -*- coding: utf-8 -*-
"""
Seam A — fotos de Paquete (Grupo 2 de ajustes-post-referencia-funcional).

`LocalFotoStorage` guarda en disco local sin depender de red; `agregar_foto`/
`listar_fotos` asocian esa URL a un Paquete.
"""

import tempfile
from pathlib import Path

import pytest

from app.domain.foto_storage import LocalFotoStorage
from app.domain.paquete_foto_service import agregar_foto, listar_fotos
from app.domain.paquete_service import Destinatario, announce

pytestmark = pytest.mark.integration


def test_local_foto_storage_guarda_y_devuelve_url_servible(tmp_path):
    storage = LocalFotoStorage(tmp_path)
    url = storage.guardar("recibo.jpg", b"contenido-de-prueba")

    assert url.startswith("/static/fotos-recibidas/")
    assert url.endswith("_recibo.jpg")

    # El archivo realmente quedó en disco, con el mismo contenido.
    nombre_archivo = url.rsplit("/", 1)[-1]
    ruta = Path(tmp_path) / nombre_archivo
    assert ruta.read_bytes() == b"contenido-de-prueba"


def test_local_foto_storage_no_colisiona_nombres_repetidos(tmp_path):
    storage = LocalFotoStorage(tmp_path)
    url1 = storage.guardar("foto.jpg", b"uno")
    url2 = storage.guardar("foto.jpg", b"dos")

    assert url1 != url2


def test_agregar_foto_asocia_al_paquete(db_session):
    p = announce(
        db_session, "3001234567", "Ana", Destinatario.yo_mismo()
    )
    storage = LocalFotoStorage(Path(tempfile.mkdtemp()))

    foto = agregar_foto(db_session, p, storage, "recibo.jpg", b"contenido")

    assert foto.paquete_id == p.id
    assert foto.url.startswith("/static/fotos-recibidas/")

    fotos = listar_fotos(db_session, p)
    assert len(fotos) == 1
    assert fotos[0].id == foto.id
