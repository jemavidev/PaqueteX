#!/usr/bin/env bash
# Respaldo de esta instalación (`.scratch/respaldos-y-restauracion`), corrido en el HOST.
#
# Mismo patrón que `scripts/importador_v1/importar_v1_cron.sh`: ejecuta el comando dentro del contenedor de la app
# y deja una línea por corrida en un log del host, rotado a .1 al pasar de 5 MB. Que no corran dos respaldos a la vez
# lo garantiza el propio comando (candado sobre la carpeta de respaldos, compartida entre host y contenedor).
#
# Uso:  scripts/respaldos/respaldar.sh [diario|antes_de_deploy|a_pedido]   (default: diario)
# Código de salida: el del comando (0 = respaldo listo).
set -uo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/app/PaqueteX}"
LOG_DIR="${LOG_DIR:-/home/ubuntu/paquetex-respaldos-log}"
LOG="$LOG_DIR/respaldos.log"
MAX_BYTES=$((5 * 1024 * 1024))
MOTIVO="${1:-diario}"

mkdir -p "$LOG_DIR"
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt "$MAX_BYTES" ]; then
  mv -f "$LOG" "$LOG.1"
fi

cd "$APP_DIR" || exit 1
salida=$(sudo docker compose --env-file .env exec -T -w /app/src app \
  python -m app.respaldo_cli respaldar --motivo "$MOTIVO" 2>&1)
estado=$?
echo "[$(date -u +%FT%TZ)] motivo=$MOTIVO codigo=$estado $salida" >> "$LOG"
echo "$salida"
exit $estado
