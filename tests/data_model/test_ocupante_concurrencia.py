# -*- coding: utf-8 -*-
"""
Seam A — carreras reales de Ocupante (auditoría `.scratch/pendientes-cliente`,
2026-08-05), contra el Postgres efímero.

A diferencia del resto de `test_ocupante_service.py` (una sola `db_session`
transaccional con rollback), estos tests abren DOS conexiones/sesiones
independientes en hilos separados que SÍ confirman (`commit`), para
reproducir una carrera de verdad -- no solo razonar sobre ella. Usan su
propio Apartamento dedicado (TORRE 9/TORRE 10, sin overlap con el resto de
la suite) y limpian sus propias filas al final, porque `migrated_db_url` es
compartido (scope=session) y estos tests SÍ dejan datos confirmados si no
se limpian.
"""

import threading

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.domain.apartamento_service import resolver_apartamento
from app.domain.ocupante import Ocupante
from app.domain.ocupante_service import MAX_OCUPANTES_ACTIVOS, agregar_ocupante
from app.domain.persona import Persona

pytestmark = pytest.mark.integration


def _sesion(migrated_db_url):
    engine = create_engine(migrated_db_url)
    return engine, sessionmaker(bind=engine)()


def _correr_en_hilo(fn):
    resultado = {}

    def _envoltorio():
        try:
            resultado["valor"] = fn()
        except Exception as exc:  # noqa: BLE001 -- se re-lanza en el hilo principal
            resultado["error"] = exc

    hilo = threading.Thread(target=_envoltorio)
    hilo.start()
    return hilo, resultado


def test_agregar_ocupante_concurrente_no_supera_el_maximo(migrated_db_url):
    # Setup: Apartamento con MAX-1 Ocupantes activos ya confirmados (fuera de
    # los hilos, sin carrera).
    engine_setup, s = _sesion(migrated_db_url)
    apto = resolver_apartamento(s, "TORRE 10", "101")
    for i in range(MAX_OCUPANTES_ACTIVOS - 1):
        agregar_ocupante(s, apto, f"Previo{i}", telefono=f"301000{i:04d}")
    s.commit()
    apto_id = apto.id

    engine_a, sesion_a = _sesion(migrated_db_url)
    engine_b, sesion_b = _sesion(migrated_db_url)
    barrera = threading.Barrier(2)

    def _agregar(sesion, nombre, telefono):
        apto_local = sesion.get(type(apto), apto_id)
        barrera.wait(timeout=5)  # ambos hilos entran a agregar_ocupante juntos
        ocupante = agregar_ocupante(sesion, apto_local, nombre, telefono=telefono)
        sesion.commit()
        return ocupante

    hilo_a, res_a = _correr_en_hilo(lambda: _agregar(sesion_a, "Quinto", "3019990001"))
    hilo_b, res_b = _correr_en_hilo(lambda: _agregar(sesion_b, "Sexto", "3019990002"))
    hilo_a.join(timeout=10)
    hilo_b.join(timeout=10)

    try:
        exitos = [r for r in (res_a, res_b) if "valor" in r]
        fallos = [r for r in (res_a, res_b) if "error" in r]
        # El lock (`FOR UPDATE` sobre el Apartamento en `agregar_ocupante`)
        # serializa: exactamente UNO de los dos hilos concurrentes logra
        # agregar el último Ocupante permitido (el MAX_OCUPANTES_ACTIVOS-ésimo):
        # el otro, al re-contar tras esperar el lock, ve el conteo YA
        # actualizado y rechaza con ValueError -- sin el fix, ambos verían
        # el mismo conteo viejo (MAX_OCUPANTES_ACTIVOS - 1) y ambos
        # pasarían, superando MAX_OCUPANTES_ACTIVOS.
        assert len(exitos) == 1, f"esperaba exactamente 1 éxito, hubo {len(exitos)}"
        assert len(fallos) == 1, f"esperaba exactamente 1 fallo, hubo {len(fallos)}"
        assert isinstance(fallos[0]["error"], ValueError)

        engine_check, sesion_check = _sesion(migrated_db_url)
        try:
            activos = (
                sesion_check.query(Ocupante)
                .filter(
                    Ocupante.apartamento_id == apto_id,
                    Ocupante.desvinculado_en.is_(None),
                )
                .count()
            )
            assert activos == MAX_OCUPANTES_ACTIVOS, (
                f"quedaron {activos} Ocupantes activos, "
                f"el máximo nunca debió superarse ({MAX_OCUPANTES_ACTIVOS})"
            )
        finally:
            sesion_check.close()
            engine_check.dispose()
    finally:
        # Limpieza: `migrated_db_url` es compartido entre tests (scope de
        # sesión) -- estos commits no se revierten solos.
        engine_cleanup, sesion_cleanup = _sesion(migrated_db_url)
        try:
            ocupantes = (
                sesion_cleanup.query(Ocupante)
                .filter(Ocupante.apartamento_id == apto_id)
                .all()
            )
            persona_ids = {o.persona_id for o in ocupantes if o.persona_id is not None}
            for o in ocupantes:
                sesion_cleanup.delete(o)
            if persona_ids:
                sesion_cleanup.query(Persona).filter(Persona.id.in_(persona_ids)).delete(
                    synchronize_session=False
                )
            sesion_cleanup.commit()
        finally:
            sesion_cleanup.close()
            engine_cleanup.dispose()
        for eng, ses in ((engine_setup, s), (engine_a, sesion_a), (engine_b, sesion_b)):
            ses.close()
            eng.dispose()


def test_mismo_telefono_concurrente_en_dos_apartamentos_solo_uno_gana(migrated_db_url):
    from app.domain.persona_service import get_or_create_persona

    engine_setup, s = _sesion(migrated_db_url)
    apto1 = resolver_apartamento(s, "TORRE 9", "101")
    apto2 = resolver_apartamento(s, "TORRE 9", "102")
    apto1_id, apto2_id = apto1.id, apto2.id
    telefono_compartido = "3019991111"
    # La Persona se crea y confirma ANTES de la barrera -- así
    # `get_or_create_persona` dentro de `agregar_ocupante` es un SELECT
    # simple para ambos hilos (sin carrera aparte en `Persona.telefono`), y
    # la carrera real que este test ejercita queda aislada en
    # `uq_ocupantes_persona_activo`, no en la unicidad de Persona (que ya
    # tiene su propio camino de protección, probado en otro lado).
    get_or_create_persona(s, telefono_compartido, "Ana")
    s.commit()

    engine_a, sesion_a = _sesion(migrated_db_url)
    engine_b, sesion_b = _sesion(migrated_db_url)
    barrera = threading.Barrier(2)

    def _agregar(sesion, apto_id, nombre):
        apto_local = sesion.get(type(apto1), apto_id)
        barrera.wait(timeout=5)
        ocupante = agregar_ocupante(sesion, apto_local, nombre, telefono=telefono_compartido)
        sesion.commit()
        return ocupante

    hilo_a, res_a = _correr_en_hilo(lambda: _agregar(sesion_a, apto1_id, "Ana"))
    hilo_b, res_b = _correr_en_hilo(lambda: _agregar(sesion_b, apto2_id, "Ana Otra Vez"))
    hilo_a.join(timeout=10)
    hilo_b.join(timeout=10)

    try:
        exitos = [r for r in (res_a, res_b) if "valor" in r]
        fallos = [r for r in (res_a, res_b) if "error" in r]
        # `uq_ocupantes_persona_activo` (migración 0024) es lo que cierra
        # esto a nivel de BD -- sin el índice, dos altas concurrentes con el
        # MISMO teléfono (ninguna ve el commit de la otra todavía) podían
        # colar 2 filas activas para la misma Persona, exactamente el bug ya
        # corregido hoy para el caso secuencial, reabierto por la puerta de
        # la concurrencia.
        assert len(exitos) == 1, f"esperaba exactamente 1 éxito, hubo {len(exitos)}"
        assert len(fallos) == 1, f"esperaba exactamente 1 fallo, hubo {len(fallos)}"
        assert isinstance(fallos[0]["error"], ValueError)

        engine_check, sesion_check = _sesion(migrated_db_url)
        try:
            persona = (
                sesion_check.query(Persona)
                .filter(Persona.telefono == "+573019991111")
                .one()
            )
            activos = (
                sesion_check.query(Ocupante)
                .filter(
                    Ocupante.persona_id == persona.id,
                    Ocupante.desvinculado_en.is_(None),
                )
                .count()
            )
            assert activos == 1, f"quedaron {activos} Ocupantes activos para el mismo teléfono"
        finally:
            sesion_check.close()
            engine_check.dispose()
    finally:
        engine_cleanup, sesion_cleanup = _sesion(migrated_db_url)
        try:
            sesion_cleanup.query(Ocupante).filter(
                Ocupante.apartamento_id.in_([apto1_id, apto2_id])
            ).delete(synchronize_session=False)
            sesion_cleanup.execute(
                text("DELETE FROM personas WHERE telefono = :tel"),
                {"tel": "+573019991111"},
            )
            sesion_cleanup.commit()
        finally:
            sesion_cleanup.close()
            engine_cleanup.dispose()
        for eng, ses in ((engine_setup, s), (engine_a, sesion_a), (engine_b, sesion_b)):
            ses.close()
            eng.dispose()
