# -*- coding: utf-8 -*-
"""Ruta de salud — smoke de que la capa web monta y responde."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}
