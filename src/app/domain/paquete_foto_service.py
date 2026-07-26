# -*- coding: utf-8 -*-
"""
Servicio de dominio de PaqueteFoto — asociar una foto subida a un Paquete
(Seam A, Grupo 2 de `ajustes-post-referencia-funcional`).
"""

from sqlalchemy.orm import Session

from .foto_storage import FotoStorage
from .paquete import Paquete
from .paquete_foto import PaqueteFoto


def agregar_foto(
    session: Session,
    paquete: Paquete,
    storage: FotoStorage,
    filename: str,
    contenido: bytes,
) -> PaqueteFoto:
    """Guarda `contenido` vía `storage` y asocia la URL resultante a `paquete`."""
    url = storage.guardar(filename, contenido)
    foto = PaqueteFoto(paquete_id=paquete.id, url=url)
    session.add(foto)
    session.flush()
    return foto


def listar_fotos(session: Session, paquete: Paquete) -> list[PaqueteFoto]:
    return (
        session.query(PaqueteFoto)
        .filter(PaqueteFoto.paquete_id == paquete.id)
        .order_by(PaqueteFoto.created_at.asc())
        .all()
    )
