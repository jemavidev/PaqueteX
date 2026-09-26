# -*- coding: utf-8 -*-
"""
Respaldos, ticket 10 (`.scratch/respaldos-y-restauracion`): copia incremental de las fotos de S3 al disco del
servidor, con la misma estructura de carpetas de S3 -- la primera vez todas, después solo las que falten, nunca borra
nada local. Seam A con un origen de fotos falso en memoria.
"""

from app.domain.respaldo_fotos_service import copiar_fotos


class OrigenFalso:
    """El puerto `OrigenFotos` en memoria: {clave: bytes}. Cuenta cuántas descargas se pidieron."""

    def __init__(self, fotos):
        self.fotos = dict(fotos)
        self.descargadas = []

    def listar(self):
        return [(clave, len(datos)) for clave, datos in sorted(self.fotos.items())]

    def descargar(self, clave, destino):
        self.descargadas.append(clave)
        destino.write_bytes(self.fotos[clave])


_FOTOS = {
    "fotos/2026/09/a1.jpg": b"foto a1",
    "fotos/2026/09/a2.jpg": b"foto a2",
    "fotos/2026/10/b1.jpg": b"foto b1 (octubre)",
}


def test_la_primera_copia_trae_todas_las_fotos_con_su_estructura(tmp_path):
    origen = OrigenFalso(_FOTOS)
    avances = []

    resultado = copiar_fotos(origen, tmp_path, al_avanzar=lambda actual, total: avances.append((actual, total)))

    for clave, datos in _FOTOS.items():
        assert (tmp_path / clave).read_bytes() == datos
    assert (resultado.copiadas, resultado.ya_estaban) == (3, 0)
    assert avances[-1] == (3, 3)


def test_la_siguiente_copia_solo_trae_las_nuevas_y_no_borra_nada(tmp_path):
    copiar_fotos(OrigenFalso(_FOTOS), tmp_path)
    (tmp_path / "mi_nota_local.txt").write_text("no tocar")
    origen = OrigenFalso({**_FOTOS, "fotos/2026/10/b2.jpg": b"foto nueva"})
    del origen.fotos["fotos/2026/09/a1.jpg"]  # ya no está en S3: la copia local se queda igual

    resultado = copiar_fotos(origen, tmp_path)

    assert origen.descargadas == ["fotos/2026/10/b2.jpg"]
    assert (resultado.copiadas, resultado.ya_estaban) == (1, 2)
    assert (tmp_path / "fotos/2026/09/a1.jpg").read_bytes() == b"foto a1"
    assert (tmp_path / "mi_nota_local.txt").read_text() == "no tocar"


def test_una_foto_a_medio_bajar_se_vuelve_a_bajar(tmp_path):
    (tmp_path / "fotos/2026/09").mkdir(parents=True)
    (tmp_path / "fotos/2026/09/a1.jpg").write_bytes(b"fot")  # quedó cortada (tamaño distinto al de S3)
    origen = OrigenFalso(_FOTOS)

    copiar_fotos(origen, tmp_path)

    assert (tmp_path / "fotos/2026/09/a1.jpg").read_bytes() == b"foto a1"
    assert "fotos/2026/09/a1.jpg" in origen.descargadas


def test_en_local_las_fotos_se_copian_desde_la_carpeta_local_con_la_clave_de_su_url(tmp_path):
    # En desarrollo las fotos no están en S3 sino en disco (`LocalFotoStorage`), servidas en /static/fotos-recibidas/.
    from app.domain.respaldo_fotos_service import LocalOrigenFotos, clave_de_url

    origen_dir = tmp_path / "fotos-recibidas"
    origen_dir.mkdir()
    (origen_dir / "abc_recibo.jpg").write_bytes(b"foto local")
    copia = tmp_path / "copia"

    copiar_fotos(LocalOrigenFotos(origen_dir, prefijo="static/fotos-recibidas/"), copia)

    clave = clave_de_url("/static/fotos-recibidas/abc_recibo.jpg")
    assert (copia / clave).read_bytes() == b"foto local"
