#!/usr/bin/env bash
# Prueba de restauración de los domingos (`.scratch/respaldos-y-restauracion`, ticket 06), corrida en el HOST por cron.
#
# Levanta un Postgres 16 DESECHABLE en la red de docker de la instalación (nunca toca la base real ni sus
# credenciales: contraseña aleatoria de un solo uso), le pide a la app que restaure ahí el último respaldo diario y lo
# compare con su manifiesto, y lo borra al terminar pase lo que pase. El resultado va al historial (resumen del lunes);
# si falla, la app manda el correo inmediato.
set -uo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/app/PaqueteX}"
LOG_DIR="${LOG_DIR:-/home/ubuntu/paquetex-respaldos-log}"
LOG="$LOG_DIR/respaldos.log"
NOMBRE="paquetex-prueba-restauracion"
mkdir -p "$LOG_DIR"
cd "$APP_DIR" || exit 1

RED="$(sudo docker inspect "$(sudo docker compose --env-file .env ps -q app)" \
  --format '{{range $red, $_ := .NetworkSettings.Networks}}{{$red}}{{end}}')"
CLAVE="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
sudo docker rm -f "$NOMBRE" >/dev/null 2>&1
trap 'sudo docker rm -f "$NOMBRE" >/dev/null 2>&1' EXIT
sudo docker run -d --rm --name "$NOMBRE" --network "$RED" -e POSTGRES_PASSWORD="$CLAVE" postgres:16 >/dev/null
for _ in $(seq 1 30); do
  sudo docker exec "$NOMBRE" pg_isready -U postgres >/dev/null 2>&1 && break
  sleep 2
done

salida=$(sudo docker compose --env-file .env exec -T -w /app/src app \
  python -m app.respaldo_cli probar --url-bd-temporal "postgresql://postgres:${CLAVE}@${NOMBRE}:5432/postgres" 2>&1)
estado=$?
echo "[$(date -u +%FT%TZ)] prueba_restauracion codigo=$estado $salida" >> "$LOG"
echo "$salida"
exit $estado
