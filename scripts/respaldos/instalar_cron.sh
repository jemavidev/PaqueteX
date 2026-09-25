#!/usr/bin/env bash
# Instala (o deja al día) en el crontab del usuario actual las tareas de respaldos (`.scratch/respaldos-y-restauracion`).
# Idempotente: reemplaza solo el bloque marcado, no toca el resto del crontab (ej. el importador v1).
# El servidor está en UTC; Colombia es UTC-5 (sin horario de verano):
#   diario              03:00 hora Colombia = 08:00 UTC
#   prueba del domingo  04:00 hora Colombia = 09:00 UTC, domingo
#   resumen del lunes   07:00 hora Colombia = 12:00 UTC, lunes
set -euo pipefail
APP_DIR="${APP_DIR:-/home/ubuntu/app/PaqueteX}"
INICIO="# >>> respaldos paquetex >>>"
FIN="# <<< respaldos paquetex <<<"
BLOQUE="$INICIO
0 8 * * * $APP_DIR/scripts/respaldos/respaldar.sh diario >/dev/null 2>&1
0 9 * * 0 $APP_DIR/scripts/respaldos/probar_restauracion.sh >/dev/null 2>&1
0 12 * * 1 $APP_DIR/scripts/respaldos/resumen_semanal.sh >/dev/null 2>&1
$FIN"
ACTUAL="$(crontab -l 2>/dev/null | sed "/^$INICIO\$/,/^$FIN\$/d")"
printf '%s\n%s\n' "$ACTUAL" "$BLOQUE" | sed '/./,$!d' | crontab -
crontab -l | sed -n "/^$INICIO\$/,/^$FIN\$/p"
