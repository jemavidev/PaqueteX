# -*- coding: utf-8 -*-
"""
Rate limiting por IP (capa web — infraestructura, no regla de dominio).

Puerto `RateLimiter` (mismo patrón que `OtpSender`/`NotificationSender`) +
`InMemoryRateLimiter`: ventana fija en memoria, correcta para desarrollo/test y
un despliegue de UN SOLO worker. En un despliegue multi-worker cada proceso
cuenta por separado (subestima el total real) — Redis es el backend correcto
ahí (brief §3), pero su integración queda fuera de esta rebanada: no se escribe
código de infraestructura externa sin poder verificarlo contra un Redis real.

El contador vive en `app.state` (no en un singleton de módulo): así cada app —
cada test, vía `create_app()` — arranca con su propio contador limpio, y en
producción persiste mientras el proceso viva (un solo `create_app()` real).

Fail-open: si el `RateLimiter` configurado lanza, la solicitud pasa igual — la
disponibilidad del login no debe depender de esta infraestructura.
"""

import time
from typing import Protocol

from fastapi import Depends, Request


class RateLimiter(Protocol):
    def permitir(self, clave: str, limite: int, ventana_segundos: int) -> bool: ...


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._contadores: dict[str, tuple[int, float]] = {}

    def permitir(self, clave: str, limite: int, ventana_segundos: int) -> bool:
        ahora = time.monotonic()
        conteo, inicio = self._contadores.get(clave, (0, ahora))
        if ahora - inicio >= ventana_segundos:
            conteo, inicio = 0, ahora
        conteo += 1
        self._contadores[clave] = (conteo, inicio)
        return conteo <= limite


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def rate_limit(nombre: str, limite: int, ventana_segundos: int):
    """Fábrica de una dependencia FastAPI: `True` si la solicitud puede pasar.

    La ruta decide qué hacer con el resultado (re-renderizar con 429, etc.) — esta
    dependencia nunca lanza `HTTPException` por sí misma.
    """

    def _dependencia(
        request: Request, limiter: RateLimiter = Depends(get_rate_limiter)
    ) -> bool:
        ip = request.client.host if request.client else "desconocido"
        clave = f"{nombre}:{ip}"
        try:
            return limiter.permitir(clave, limite, ventana_segundos)
        except Exception:
            return True  # fail-open

    return _dependencia
