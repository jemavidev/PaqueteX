#!/bin/bash
# Pasada del importador espejo v1 → v2 para cron (`.scratch/importador-v1-espejo`,
# ticket 11). Corre en el HOST del servidor v2, dentro del checkout del repo de
# despliegue; la pasada en sí corre DENTRO del contenedor `app` (la imagen no
# incluye `scripts/`, por eso se invoca el módulo desde /app/src).
#
# crontab del usuario ubuntu (cada 15 minutos):
#   */15 * * * * /home/ubuntu/app/scripts/importador_v1/importar_v1_cron.sh
#
# - `flock -n`: si la pasada anterior sigue corriendo, esta se salta (nunca
#   dos pasadas a la vez).
# - Log en ~/importador_v1/importador.log, rotado a .1 al pasar de 5 MB.
# - Argumentos extra se pasan tal cual (ej. `--simular`, `--final`).
set -uo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/app}"
LOG_DIR="${LOG_DIR:-/home/ubuntu/importador_v1}"
LOG="$LOG_DIR/importador.log"
LOCK="$LOG_DIR/importador.lock"
MAX_BYTES=$((5 * 1024 * 1024))

mkdir -p "$LOG_DIR"
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt "$MAX_BYTES" ]; then
  mv -f "$LOG" "$LOG.1"
fi

cd "$APP_DIR" || exit 1
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(date -u +%FT%TZ)] pasada anterior aún en curso; se salta esta" >> "$LOG"
  exit 0
fi

sudo docker compose --env-file .env exec -T -w /app/src app \
  python -m app.importador_v1_cli "$@" >> "$LOG" 2>&1
estado=$?
if [ $estado -ne 0 ]; then
  echo "[$(date -u +%FT%TZ)] la pasada terminó con código $estado" >> "$LOG"
fi
exit $estado
