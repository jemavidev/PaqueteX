FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_ENV=production

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
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
