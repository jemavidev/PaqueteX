# -*- coding: utf-8 -*-
"""
Seam A — Catálogo editable de motivos de cancelación.

Comportamiento observable: crear/editar/borrar un motivo, las validaciones
(vacío, duplicado exacto, no borrar el último), y que el listado respete el
orden de creación. La migración `0039_motivos_cancelacion` sembró 4 filas
("Anuncio erróneo", "Devuelto al transportador", "No reclamado", "Otro"),
reducidas a solo "Otro" por `0040_motivos_solo_otro` (pedido explícito del
cliente en vivo, 2026-09-03: un motivo genérico alcanza) -- `db_session`
arranca con "Otro" ya presente, así que los tests no asumen un catálogo
vacío, pero tampoco un tamaño fijo (ver `crear_motivo`/`eliminar_motivo` de
más abajo, que solo razonan en relativo a lo que ya exista).
"""

import pytest

from app.domain.motivo_cancelacion_service import (
    crear_motivo,
    editar_motivo,
    eliminar_motivo,
    listar_motivos,
    motivo_valido,
)

pytestmark = pytest.mark.integration


def test_crear_motivo_lo_agrega_al_listado(db_session):
    motivo = crear_motivo(db_session, "Vecino no estaba")

    etiquetas = [m.etiqueta for m in listar_motivos(db_session)]
    assert "Vecino no estaba" in etiquetas
    assert motivo.etiqueta == "Vecino no estaba"


def test_crear_motivo_con_espacios_alrededor_se_limpia(db_session):
    motivo = crear_motivo(db_session, "  Paquete dañado  ")
    assert motivo.etiqueta == "Paquete dañado"


def test_crear_motivo_vacio_lanza_valueerror_sin_guardar(db_session):
    antes = {m.etiqueta for m in listar_motivos(db_session)}

    with pytest.raises(ValueError):
        crear_motivo(db_session, "   ")

    despues = {m.etiqueta for m in listar_motivos(db_session)}
    assert antes == despues


def test_crear_motivo_duplicado_lanza_valueerror_sin_guardar(db_session):
    crear_motivo(db_session, "Dirección incorrecta")
    antes = len(listar_motivos(db_session))

    with pytest.raises(ValueError):
        crear_motivo(db_session, "Dirección incorrecta")

    assert len(listar_motivos(db_session)) == antes


def test_crear_motivo_demasiado_largo_lanza_valueerror(db_session):
    with pytest.raises(ValueError):
        crear_motivo(db_session, "x" * 41)


def test_editar_motivo_cambia_el_texto_sin_crear_fila_nueva(db_session):
    motivo = crear_motivo(db_session, "Texto original")
    total_antes = len(listar_motivos(db_session))

    editado = editar_motivo(db_session, motivo.id, "Texto corregido")

    assert editado.id == motivo.id
    assert editado.etiqueta == "Texto corregido"
    assert len(listar_motivos(db_session)) == total_antes


def test_editar_motivo_a_vacio_lanza_valueerror(db_session):
    motivo = crear_motivo(db_session, "Texto que no debe borrarse")

    with pytest.raises(ValueError):
        editar_motivo(db_session, motivo.id, "")

    recargado = listar_motivos(db_session)
    assert any(m.id == motivo.id and m.etiqueta == "Texto que no debe borrarse" for m in recargado)


def test_editar_motivo_a_un_texto_ya_usado_por_otro_lanza_valueerror(db_session):
    crear_motivo(db_session, "Motivo A")
    motivo_b = crear_motivo(db_session, "Motivo B")

    with pytest.raises(ValueError):
        editar_motivo(db_session, motivo_b.id, "Motivo A")


def test_editar_motivo_conservando_su_propio_texto_no_falla(db_session):
    motivo = crear_motivo(db_session, "Mismo texto")
    editado = editar_motivo(db_session, motivo.id, "Mismo texto")
    assert editado.etiqueta == "Mismo texto"


def test_eliminar_motivo_lo_quita_del_listado(db_session):
    motivo = crear_motivo(db_session, "Motivo desechable")

    eliminar_motivo(db_session, motivo.id)

    etiquetas = [m.etiqueta for m in listar_motivos(db_session)]
    assert "Motivo desechable" not in etiquetas


def test_eliminar_el_ultimo_motivo_lanza_valueerror_sin_efecto(db_session):
    motivos = listar_motivos(db_session)
    # Deja solo uno, borrando todos los demás con la misma función.
    for m in motivos[1:]:
        eliminar_motivo(db_session, m.id)
    ultimo = listar_motivos(db_session)
    assert len(ultimo) == 1

    with pytest.raises(ValueError):
        eliminar_motivo(db_session, ultimo[0].id)

    assert listar_motivos(db_session) == ultimo


def test_listar_motivos_respeta_el_orden_de_creacion(db_session):
    crear_motivo(db_session, "Primero en crearse")
    crear_motivo(db_session, "Segundo en crearse")

    etiquetas = [m.etiqueta for m in listar_motivos(db_session)]
    assert etiquetas.index("Primero en crearse") < etiquetas.index("Segundo en crearse")


def test_motivo_valido_distingue_existentes_de_inexistentes(db_session):
    crear_motivo(db_session, "Motivo real")

    assert motivo_valido(db_session, "Motivo real") is True
    assert motivo_valido(db_session, "Motivo que no existe") is False
    assert motivo_valido(db_session, "") is False
