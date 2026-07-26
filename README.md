# PAQUETEX

Gestión de paquetería para conjuntos residenciales — FastAPI + SQLAlchemy + Postgres.

## Variables de entorno

- `DATABASE_URL` — obligatoria. Cadena de conexión Postgres.
- `SECRET_KEY` — obligatoria si `WEB_ENV=production`. Firma la cookie de sesión.
- `WEB_ENV=production` — activa las validaciones estrictas de producción.

## Correr localmente

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://usuario:clave@localhost:5432/paquetex
alembic -x db_url="$DATABASE_URL" upgrade head
uvicorn app.web.app:app --app-dir src --host 0.0.0.0 --port 8000
```

## Con Docker

```bash
docker build -t paquetex .
docker run -p 8000:8000 -e DATABASE_URL=... -e SECRET_KEY=... -e WEB_ENV=production paquetex
```

## Tests

```bash
pip install -r requirements.txt pytest httpx
pytest
```

Requiere un Postgres efímero — ver `tests/conftest.py` (`TEST_DATABASE_URL` o Docker local).

## Staging

`docker-compose.yml` incluye `caddy` como reverse proxy con HTTPS automático
(Let's Encrypt, renovación integrada — sin cron/certbot). Editar el dominio en
`Caddyfile`.

- URL: https://test.papyrus.com.co
- Deploy automático: todo push a `main` dispara `.github/workflows/deploy-staging.yml`.
