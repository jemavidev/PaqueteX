# -*- coding: utf-8 -*-
"""
Servicio de dominio de PaqueteFoto — asociar una foto subida a un Paquete
(Seam A, Grupo 2 de `ajustes-post-referencia-funcional`).

Tope de `_MAX_FOTOS_POR_PAQUETE` (Grupo 15, Ronda 2): hasta 3 fotos en
distintos ángulos, para poder identificar el paquete entre todos los que
haya en portería. El tope se aplica aquí (dominio) como defensa en
profundidad — la UI (`/paquetes`, modal Recibir) ya limita la subida a 3
archivos, pero un POST armado a mano no debería poder saltárselo.
"""

from sqlalchemy.orm import Session

from .foto_storage import FotoStorage
from .paquete import Paquete
from .paquete_foto import PaqueteFoto

_MAX_FOTOS_POR_PAQUETE = 3


def agregar_foto(
    session: Session,
    paquete: Paquete,
    storage: FotoStorage,
    filename: str,
    contenido: bytes,
) -> PaqueteFoto:
    """Guarda `contenido` vía `storage` y asocia la URL resultante a `paquete`.

    Raises:
        ValueError: si `paquete` ya tiene `_MAX_FOTOS_POR_PAQUETE` fotos.
    """
    if len(listar_fotos(session, paquete)) >= _MAX_FOTOS_POR_PAQUETE:
        raise ValueError(
            f"Un paquete no puede tener más de {_MAX_FOTOS_POR_PAQUETE} fotos."
        )
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
