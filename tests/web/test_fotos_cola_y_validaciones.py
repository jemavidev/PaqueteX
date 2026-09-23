# -*- coding: utf-8 -*-
"""
Fotos de Recibir: validaciones del servidor y subida diferida asociada al paquete (issue 389, `.scratch/pendientes-cliente`).

- Solo imágenes (lo que Pillow no abre se rechaza) y como máximo 15 MB.
- `fotos_urls` al recibir solo acepta URLs del propio almacenamiento.
- `asociar=1`: la cola del equipo sube las fotos DESPUÉS de confirmar "Recibir" (Recibir ya no espera ni lleva fotos),
  así que el endpoint las asocia directo al paquete ya recibido. Si todavía está Anunciado responde "reintentar";
  si ya no admite fotos (cupo lleno, cancelado) responde "no reintentar", para que la cola la descarte.
"""

import io

from PIL import Image

from app.domain.paquete import EstadoPaquete, Paquete
from app.domain.paquete_foto import PaqueteFoto
from app.domain.paquete_lifecycle import cancel, receive
from app.domain.paquete_service import Destinatario, announce
from app.domain.staff_service import create_initial_admin
from app.domain.usuario import Usuario

_PW = "Contrasena1"


def _jpeg(ancho=64, alto=48):
    buffer = io.BytesIO()
    Image.new("RGB", (ancho, alto), color=(120, 90, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _preparar(client):
    create_initial_admin(client.db, "staff@club.com", "Operador", _PW)
    client.db.commit()
    client.post("/ingresar", data={"email": "staff@club.com", "password": _PW})
    p = announce(client.db, anunciante_telefono="3001234567", anunciante_nombre="Ana",
                 destinatario=Destinatario.yo_mismo())
    client.db.commit()
    return p, client.db.query(Usuario).one()


def _subir(client, p, contenido, asociar=False, nombre="foto.jpg"):
    return client.post(
        f"/paquetes/{p.id}/fotos",
        data={"asociar": "1"} if asociar else {},
        files={"foto": (nombre, contenido, "image/jpeg")},
    )


def _fotos(client, p):
    client.db.expire_all()
    return client.db.query(PaqueteFoto).filter(PaqueteFoto.paquete_id == p.id).all()


def test_subir_algo_que_no_es_imagen_se_rechaza(client):
    p, _ = _preparar(client)

    r = _subir(client, p, b"<html>no soy una foto</html>", nombre="pagina.html")

    assert r.status_code == 400


def test_subir_una_foto_de_mas_de_15_mb_se_rechaza(client):
    p, _ = _preparar(client)

    r = _subir(client, p, b"\xff\xd8" + b"0" * (15 * 1024 * 1024 + 1))

    assert r.status_code == 413


def test_asociar_una_foto_a_un_paquete_ya_recibido_crea_la_fila(client):
    p, staff = _preparar(client)
    receive(client.db, p, staff)
    client.db.commit()

    r = _subir(client, p, _jpeg(), asociar=True)

    assert r.status_code == 200
    assert [f.url for f in _fotos(client, p)] == [r.json()["url"]]


def test_asociar_a_un_paquete_todavia_anunciado_pide_reintentar(client):
    """Puede pasar si la cola corre antes de que la recepción quede guardada: la foto espera, no se pierde."""
    p, _ = _preparar(client)

    r = _subir(client, p, _jpeg(), asociar=True)

    assert r.status_code == 409
    assert r.json()["reintentar"] is True
    assert _fotos(client, p) == []


def test_asociar_con_el_cupo_de_3_lleno_no_se_reintenta(client):
    p, staff = _preparar(client)
    receive(client.db, p, staff)
    client.db.commit()
    for _ in range(3):
        assert _subir(client, p, _jpeg(), asociar=True).status_code == 200

    r = _subir(client, p, _jpeg(), asociar=True)

    assert r.status_code == 409
    assert r.json()["reintentar"] is False
    assert len(_fotos(client, p)) == 3


def test_asociar_a_un_paquete_cancelado_no_se_reintenta(client):
    p, staff = _preparar(client)
    cancel(client.db, p, staff, "Anuncio erróneo")
    client.db.commit()

    r = _subir(client, p, _jpeg(), asociar=True)

    assert r.status_code == 409
    assert r.json()["reintentar"] is False


def test_recibir_ignora_urls_de_fotos_ajenas_al_almacenamiento(client):
    p, _ = _preparar(client)
    propia = _subir(client, p, _jpeg()).json()["url"]

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        data={"fotos_urls": [propia, "https://otro-sitio.example/foto.jpg"]},
        follow_redirects=False,
    )

    assert r.status_code == 303
    assert [f.url for f in _fotos(client, p)] == [propia]


def test_recibir_con_un_archivo_crudo_que_no_es_imagen_recibe_igual_sin_foto(client):
    """Recibir nunca falla por una foto: el archivo inválido se descarta y el paquete queda Recibido."""
    p, _ = _preparar(client)

    r = client.post(
        f"/paquetes/{p.id}/recibir",
        files={"fotos": ("x.jpg", b"no-es-una-imagen", "image/jpeg")},
        follow_redirects=False,
    )

    assert r.status_code == 303
    client.db.expire_all()
    assert client.db.get(Paquete, p.id).estado == EstadoPaquete.RECIBIDO
    assert _fotos(client, p) == []
