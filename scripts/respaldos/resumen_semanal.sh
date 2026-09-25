#!/usr/bin/env bash
# Resumen semanal de respaldos por correo (lunes; `.scratch/respaldos-y-restauracion`, ticket 06), corrido en el HOST.
set -uo pipefail
APP_DIR="${APP_DIR:-/home/ubuntu/app/PaqueteX}"
LOG_DIR="${LOG_DIR:-/home/ubuntu/paquetex-respaldos-log}"
mkdir -p "$LOG_DIR"
cd "$APP_DIR" || exit 1
salida=$(sudo docker compose --env-file .env exec -T -w /app/src app python -m app.respaldo_cli resumen 2>&1)
estado=$?
echo "[$(date -u +%FT%TZ)] resumen codigo=$estado" >> "$LOG_DIR/respaldos.log"
echo "$salida"
exit $estado
