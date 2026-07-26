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
