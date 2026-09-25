#!/usr/bin/env bash
# Restaura un respaldo de ESTA instalación (`.scratch/respaldos-y-restauracion`, ticket 02). Se corre en el HOST, por SSH.
#
# Uso:  scripts/respaldos/restaurar.sh <carpeta-del-respaldo | archivo.zip> [--otro-destino]
#
# El script es delgado: las protecciones viven en `app.respaldo_cli` / `respaldo_service` (probadas). Pasos:
#   1. Verifica el respaldo (huellas) y muestra qué contiene -- con el sistema todavía encendido.
#   2. Pide escribir el dominio de esta instalación para confirmar.
#   3. Pausa el importador v1 (toma su candado: su cron se salta mientras tanto) y detiene la app.
#   4. Restaura: el comando revisa mismo sistema y versión, respalda lo actual (`antes_de_restaurar`), reemplaza la
#      base y aplica las migraciones pendientes.
#   5. Enciende la app (siempre, aunque algo falle) y verifica /health.
#
# Un respaldo descargado (.zip o carpeta fuera de la carpeta de respaldos) se copia primero a
# <carpeta de respaldos>/.restaurar/, que el contenedor ve como /respaldos/.restaurar/.
set -euo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/app/PaqueteX}"
RESPALDOS_HOST="${RESPALDOS_HOST:-/home/ubuntu/paquetex-respaldos}"
IMPORTADOR_LOCK="${IMPORTADOR_LOCK:-/home/ubuntu/importador_v1/importador.lock}"

if [ $# -lt 1 ]; then
  echo "Uso: $0 <carpeta-del-respaldo | archivo.zip> [--otro-destino]" >&2
  exit 64
fi
ORIGEN="$(realpath "$1")"; shift
EXTRA=("$@")

# --- Ubicar el respaldo donde el contenedor lo ve --------------------------------------------------------------------
if [ -f "$ORIGEN" ] && [[ "$ORIGEN" == *.zip ]]; then
  NOMBRE="$(basename "$ORIGEN" .zip)"
  sudo rm -rf "$RESPALDOS_HOST/.restaurar/$NOMBRE"
  sudo mkdir -p "$RESPALDOS_HOST/.restaurar/$NOMBRE"
  sudo python3 -m zipfile -e "$ORIGEN" "$RESPALDOS_HOST/.restaurar/$NOMBRE"
  # El .zip puede traer la carpeta adentro o los archivos sueltos.
  if [ ! -f "$RESPALDOS_HOST/.restaurar/$NOMBRE/manifiesto.txt" ] && [ -f "$RESPALDOS_HOST/.restaurar/$NOMBRE/$NOMBRE/manifiesto.txt" ]; then
    RELATIVA=".restaurar/$NOMBRE/$NOMBRE"
  else
    RELATIVA=".restaurar/$NOMBRE"
  fi
elif [ -d "$ORIGEN" ] && [[ "$ORIGEN" == "$(realpath "$RESPALDOS_HOST")"/* ]]; then
  RELATIVA="${ORIGEN#"$(realpath "$RESPALDOS_HOST")"/}"
elif [ -d "$ORIGEN" ]; then
  NOMBRE="$(basename "$ORIGEN")"
  sudo rm -rf "$RESPALDOS_HOST/.restaurar/$NOMBRE"
  sudo mkdir -p "$RESPALDOS_HOST/.restaurar"
  sudo cp -a "$ORIGEN" "$RESPALDOS_HOST/.restaurar/$NOMBRE"
  RELATIVA=".restaurar/$NOMBRE"
else
  echo "No existe: $ORIGEN" >&2
  exit 66
fi
EN_CONTENEDOR="/respaldos/$RELATIVA"

cd "$APP_DIR"
comando() {
  # Contenedor de un solo uso con la misma imagen, montajes y entorno que la app: funciona con la app detenida.
  # `</dev/null`: el contenedor no debe tragarse la entrada del script (la confirmación puede venir por tubería).
  sudo docker compose --env-file .env run --rm --no-deps -T -w /app/src app python -m app.respaldo_cli "$@" </dev/null
}

# --- 1. Verificar ------------------------------------------------------------------------------------------------------
echo "== Verificando el respaldo =="
comando verificar "$EN_CONTENEDOR"

# --- 2. Confirmar ------------------------------------------------------------------------------------------------------
echo
echo "Esto REEMPLAZA toda la base de datos de esta instalación por la del respaldo."
echo "Lo registrado después de ese respaldo se pierde (antes se guarda una copia de lo actual)."
read -r -p "Escribe el dominio de esta instalación para confirmar: " CONFIRMACION

# --- 3. Pausar importador y detener la app -----------------------------------------------------------------------------
mkdir -p "$(dirname "$IMPORTADOR_LOCK")"
exec 9>"$IMPORTADOR_LOCK"
echo "== Pausando el importador v1 (esperando a que termine su pasada, si hay una) =="
flock -w 600 9 || { echo "El importador no soltó su candado en 10 minutos; no se restauró." >&2; exit 75; }

echo "== Deteniendo la app =="
sudo docker compose --env-file .env stop app
encender() {
  echo "== Encendiendo la app =="
  sudo docker compose --env-file .env start app
}
trap encender EXIT

# --- 4. Restaurar ------------------------------------------------------------------------------------------------------
echo "== Restaurando =="
set +e
comando restaurar "$EN_CONTENEDOR" --confirmacion "$CONFIRMACION" "${EXTRA[@]}"
ESTADO=$?
set -e

# --- 5. Encender y verificar -------------------------------------------------------------------------------------------
trap - EXIT
encender
echo "== Verificando /health =="
for intento in $(seq 1 20); do
  if sudo docker compose --env-file .env exec -T app python -c \
      "import urllib.request,sys; sys.exit(0 if b'ok' in urllib.request.urlopen('http://localhost:8000/health', timeout=5).read() else 1)" \
      >/dev/null 2>&1; then
    if [ $ESTADO -eq 0 ]; then
      echo "LISTO: restaurado y el sistema responde."
    else
      echo "NO se restauró (ver el motivo arriba). El sistema quedó como estaba y responde."
    fi
    exit $ESTADO
  fi
  sleep 3
done
echo "ATENCIÓN: la app no responde en /health tras 60 s. Revisar: sudo docker compose logs app" >&2
exit 1
