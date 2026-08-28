#!/usr/bin/env bash
# Ambiente local persistente de PaqueteXv.2 -- .scratch/pendientes-cliente
# (pedido en vivo 2026-08-10): staff testea manualmente en localhost, sin
# esperar un deploy a test.papyrus.com.co por cada cambio.
#
# Levanta (o reusa, si ya existe) un Postgres persistente dedicado --
# nombre/puerto propios para no chocar con succionar-db (5432) ni con
# paqueteria_staging (8001) -- migra, siembra un admin SOLO si la base está
# vacía, y deja `uvicorn --reload` corriendo en foreground: cualquier
# cambio de código se refleja al refrescar el navegador, sin rebuild de
# imagen (a diferencia de docker-compose).
#
# Uso: ./scripts/paquetex_dev_up.sh
# Reset a datos limpios: ./scripts/paquetex_dev_reset.sh

set -euo pipefail

CODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER=paquetex_dev_pg
VOLUME=paquetex_dev_pgdata          # con NOMBRE -- nunca queda huérfano
PG_PORT=5433
APP_PORT=8010
DB_URL="postgresql://paquetex:paquetex_dev@localhost:${PG_PORT}/paquetex_dev"
ADMIN_EMAIL="info@papyrus.com.co"
ADMIN_PASSWORD="Contrasena1"

if [ "$(docker ps -aq -f name="^${CONTAINER}$")" = "" ]; then
  echo "==> Creando ${CONTAINER} (Postgres persistente, volumen ${VOLUME})..."
  docker run -d --name "$CONTAINER" \
    -e POSTGRES_USER=paquetex \
    -e POSTGRES_PASSWORD=paquetex_dev \
    -e POSTGRES_DB=paquetex_dev \
    -p "${PG_PORT}:5432" \
    -v "${VOLUME}:/var/lib/postgresql/data" \
    postgres:16 >/dev/null
elif [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" != "true" ]; then
  echo "==> Reanudando ${CONTAINER}..."
  docker start "$CONTAINER" >/dev/null
else
  echo "==> ${CONTAINER} ya está corriendo."
fi

echo "==> Esperando a que Postgres acepte conexiones..."
for _ in $(seq 1 60); do
  if docker exec "$CONTAINER" pg_isready -U paquetex >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

echo "==> Migrando (alembic upgrade head)..."
(cd "$CODE_DIR" && DATABASE_URL="$DB_URL" .venv/bin/python -m alembic -x "db_url=$DB_URL" upgrade head)

echo "==> Sembrando admin inicial si la base está vacía..."
DATABASE_URL="$DB_URL" "$CODE_DIR/.venv/bin/python" - <<PYEOF
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import sys
sys.path.insert(0, "${CODE_DIR}/src")
from app.domain.usuario import Usuario
from app.domain.staff_service import create_initial_admin

engine = create_engine(os.environ["DATABASE_URL"])
Session = sessionmaker(bind=engine)
db = Session()
if db.query(Usuario).count() == 0:
    create_initial_admin(db, "${ADMIN_EMAIL}", "Admin Local", "${ADMIN_PASSWORD}")
    db.commit()
    print("Admin creado: ${ADMIN_EMAIL} / ${ADMIN_PASSWORD}")
else:
    print("Ya hay usuarios -- no se siembra nada nuevo.")
PYEOF

echo ""
echo "=================================================================="
echo " PaqueteX local:  http://localhost:${APP_PORT}"
echo " Staff:           ${ADMIN_EMAIL} / ${ADMIN_PASSWORD}  (si ya existía otro admin, usa ese)"
echo " Postgres:         localhost:${PG_PORT} (paquetex_dev / paquetex_dev)"
echo " Ctrl+C para apagar uvicorn -- el Postgres se queda corriendo (docker stop ${CONTAINER} para pararlo)."
echo "=================================================================="
echo ""

cd "$CODE_DIR/src"
exec env DATABASE_URL="$DB_URL" PUBLIC_BASE_URL="http://localhost:${APP_PORT}" \
  "$CODE_DIR/.venv/bin/python" -m uvicorn app.web.app:app --reload --host 127.0.0.1 --port "$APP_PORT"
