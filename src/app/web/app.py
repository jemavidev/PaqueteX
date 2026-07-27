# -*- coding: utf-8 -*-
"""
App factory de la capa web del rebuild (clean-room, ADR-0004).

Arranca SIN credenciales AWS y sin importar el `config`/app viejos. Crece ruta por
ruta; eventualmente reemplaza `src/main.py` (strangler fig).
"""

from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from .config import secret_key
from .rate_limit import InMemoryRateLimiter
from .routes.announce import router as announce_router
from .routes.auth import router as auth_router
from .routes.health import router as health_router
from .routes.admin import router as admin_router
from .routes.announce_new import router as announce_new_router
from .routes.customer_auth import router as customer_auth_router
from .routes.customer_verify import router as customer_verify_router
from .routes.customers_manage import router as customers_manage_router
from .routes.packages import router as packages_router
from .routes.search import router as search_router

_WEB_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _WEB_DIR / "static"


# Prefijos de ruta que pertenecen a la audiencia CLIENTE (current_customer);
# cualquier otro 401 se asume de la audiencia STAFF (current_staff/require_admin).
# OJO al añadir/renombrar rutas: verificar que ningún prefijo aquí sea también
# prefijo (por coincidencia de substring) de una ruta STAFF — ya pasó una vez
# con "/customer" vs "/customers/manage" (ver historial de commits).
_RUTAS_CLIENTE = ("/otp", "/mis-datos")


async def _redirigir_no_autenticado(request: Request, exc: StarletteHTTPException):
    """Un 401 en una ruta con privilegios manda al login correspondiente — de
    cliente si la ruta es de la audiencia cliente, de staff en el resto (dos
    audiencias con sesiones y logins independientes)."""
    if exc.status_code == 401:
        destino = (
            "/otp"
            if request.url.path.startswith(_RUTAS_CLIENTE)
            else "/ingresar"
        )
        return RedirectResponse(destino, status_code=303)
    return await http_exception_handler(request, exc)


def create_app() -> FastAPI:
    app = FastAPI(title="PAQUETEX — rebuild PaqueteXv.2")
    # Contador de rate-limit por app (no un singleton de módulo): cada app —
    # cada test vía create_app() — arranca con su propio contador limpio.
    app.state.rate_limiter = InMemoryRateLimiter()
    # Sesión por cookie firmada (el actor de las acciones sale de aquí).
    app.add_middleware(SessionMiddleware, secret_key=secret_key())
    app.add_exception_handler(StarletteHTTPException, _redirigir_no_autenticado)

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def raiz():
        # Sin ruta explícita, la app 404. El punto de entrada público por
        # defecto es /anunciar -- el mismo destino al que ya apunta la marca
        # del header para un visitante sin sesión (ver base.html).
        return RedirectResponse("/anunciar", status_code=status.HTTP_302_FOUND)

    app.include_router(health_router)
    app.include_router(announce_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(announce_new_router)
    app.include_router(customer_auth_router)
    app.include_router(customers_manage_router)
    app.include_router(customer_verify_router)
    app.include_router(packages_router)
    app.include_router(search_router)
    return app


app = create_app()
