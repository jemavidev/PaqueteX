# -*- coding: utf-8 -*-
"""
Seam B — Grafo de migración.

Aserciones delgadas: un solo `head`, una sola raíz (`down_revision = None`
exactamente una vez), y round-trip limpio `upgrade head` → `downgrade base`
sobre un Postgres vacío.
"""

import pytest

import _harness as H


def test_arbol_alembic_de_una_sola_raiz_y_un_solo_head():
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(H.alembic_config())

    heads = script.get_heads()
    assert len(heads) == 1, f"Se esperaba UN solo head; hay: {heads}"

    bases = script.get_bases()
    assert len(bases) == 1, (
        f"Se esperaba UNA sola raíz (down_revision=None una vez); hay: {bases}"
    )


@pytest.mark.integration
def test_upgrade_head_downgrade_base_round_trip(empty_db_url):
    # Postgres vacío: 'personas' no existe todavía.
    assert not H.table_exists(empty_db_url, "personas")

    H.run_alembic(empty_db_url, "upgrade", "head")
    assert H.table_exists(empty_db_url, "personas")

    H.run_alembic(empty_db_url, "downgrade", "base")
    assert not H.table_exists(empty_db_url, "personas")

    # Re-upgrade: las migraciones son reproducibles sobre BD limpia.
    H.run_alembic(empty_db_url, "upgrade", "head")
    assert H.table_exists(empty_db_url, "personas")


@pytest.mark.integration
def test_migracion_0035_corrige_creado_en_legacy_a_created_at(empty_db_url):
    """Regresión -- .scratch/plantillas-notificacion-multicanal,
    /diagnosing-bugs 2026-08-28: `0034_plantilla_historial` creaba
    originalmente `creado_en`; se corrigió editando ESE archivo in-place
    para que creara `created_at`, pero cualquier BD que ya hubiera corrido
    la versión original se quedaba con `creado_en` en la tabla física --
    Alembic no vuelve a ejecutar una migración ya aplicada aunque su
    archivo cambie después. `0035_historial_created_at` corrige hacia
    adelante. Simula el escenario real: migra hasta 0034, renombra la
    columna a mano para reproducir el estado de una BD "vieja" (como
    hubiera quedado con la 0034 original), y confirma que `upgrade head`
    (0035 incluida) la deja en `created_at`."""
    H.run_alembic(empty_db_url, "upgrade", "0034_plantilla_historial")
    assert H.column_exists(empty_db_url, "plantillas_notificacion_historial", "created_at")

    H.rename_column(
        empty_db_url, "plantillas_notificacion_historial", "created_at", "creado_en"
    )
    assert H.column_exists(empty_db_url, "plantillas_notificacion_historial", "creado_en")

    H.run_alembic(empty_db_url, "upgrade", "head")

    assert H.column_exists(empty_db_url, "plantillas_notificacion_historial", "created_at")
    assert not H.column_exists(empty_db_url, "plantillas_notificacion_historial", "creado_en")


@pytest.mark.integration
def test_migracion_0035_no_hace_nada_si_ya_tiene_created_at(empty_db_url):
    """La 0034 actual (ya corregida) crea `created_at` directo -- 0035
    debe ser un no-op seguro en ese caso (BD nueva, nunca vio `creado_en`)."""
    H.run_alembic(empty_db_url, "upgrade", "head")
    assert H.column_exists(empty_db_url, "plantillas_notificacion_historial", "created_at")
