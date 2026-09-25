FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_ENV=production

WORKDIR /app

# Cliente de Postgres 16 para los respaldos (`.scratch/respaldos-y-restauracion`): desde el repositorio oficial de
# PostgreSQL, NO el de Debian -- el de esta Debian es 17, y un volcado de `pg_dump` 17 trae comandos (ej.
# `SET transaction_timeout`) que el servidor 16 rechaza al restaurar. Debe coincidir con la versión mayor de `db`.
# `git`: la copia del código de cada respaldo es un `git archive` del commit desplegado (nunca el `.env`).
RUN apt-get update && apt-get install -y --no-install-recommends gcc curl ca-certificates git \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo "$VERSION_CODENAME")-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY alembic/ alembic/
COPY src/ src/

EXPOSE 8000

# Aplica las migraciones (alembic upgrade head, nunca create_all) y arranca.
# `--proxy-headers --forwarded-allow-ips '*'` (issue 404): sin esto la app ve a TODO cliente con la IP del
# contenedor de Caddy, y cada límite "por IP" (login, OTP, /consultar) queda como un solo contador global.
# `'*'` es seguro porque la app solo hace `expose` (nadie más que Caddy la alcanza) y Caddy reescribe
# `X-Forwarded-For` con la IP real, descartando la que mande el cliente. La IP de Caddy cambia al recrearlo.
CMD alembic -x db_url="$DATABASE_URL" upgrade head && \
    uvicorn app.web.app:app --app-dir src --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'
