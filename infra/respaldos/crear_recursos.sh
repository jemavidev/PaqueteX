#!/usr/bin/env bash
# Crea (o deja al día) en la cuenta de AWS de Jesús lo que necesitan los respaldos (`.scratch/respaldos-y-restauracion`,
# ticket 04). Idempotente: se puede correr de nuevo sin romper nada. Se corre desde una PC con la AWS CLI autenticada
# en la cuenta 172460160630 (NO desde el servidor).
#
#   infra/respaldos/crear_recursos.sh <dominio> [archivo-para-la-llave]
#
# - Bucket `paquetex-respaldos`: privado (bloqueo de acceso público), cifrado por defecto de S3 y versionado.
# - Reglas de conservación POR CARPETA del dominio (se suman a las de los otros dominios, sin tocarlas):
#   `diario/` y `puntual/` 30 días, `mensual/` 365, `anual/` sin fecha de borrado.
# - Usuario IAM `paquetex-respaldos-<dominio>` con la política de `politica_servidor.json.plantilla`: SOLO `PutObject`
#   bajo `<dominio>/` -- ni leer, ni listar, ni borrar, ni etiquetar, ni tocar la carpeta de otro dominio.
#   Que el servidor no pueda destruir sus respaldos se apoya en eso y en el versionado: si sobrescribe una copia, la
#   anterior queda como versión previa -- 30 días en diario/puntual, 365 en mensual y para siempre en anual.
# - Si se da un archivo, crea una llave nueva para ese usuario y la guarda ahí (permisos 600), en formato `.env`
#   (RESPALDO_AWS_ACCESS_KEY_ID / RESPALDO_AWS_SECRET_ACCESS_KEY). Nunca la imprime.
set -euo pipefail

DOMINIO="${1:?Uso: $0 <dominio> [archivo-para-la-llave]}"
ARCHIVO_LLAVE="${2:-}"
BUCKET="${BUCKET:-paquetex-respaldos}"
REGION="${REGION:-us-east-1}"
AQUI="$(cd "$(dirname "$0")" && pwd)"
USUARIO="paquetex-respaldos-$(echo "$DOMINIO" | tr '.' '-')"

if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "Creando bucket $BUCKET en $REGION"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  fi
fi
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
# Reglas: las de ESTE dominio se reemplazan; las de los demás dominios y la regla general se conservan.
ACTUALES="$(aws s3api get-bucket-lifecycle-configuration --bucket "$BUCKET" 2>/dev/null || echo '{"Rules": []}')"
REGLAS="$(DOMINIO="$DOMINIO" python3 -c '
import json, os, sys
dominio = os.environ["DOMINIO"]
# Se conservan solo las reglas por carpeta de OTROS dominios (las de la primera versión, por etiqueta, se descartan:
# con permiso de etiquetar, el servidor podía cambiarle la conservación a una copia).
reglas = [
    r for r in json.load(sys.stdin)["Rules"]
    if "/" in r["ID"] and not r["ID"].startswith(dominio + "/") and "Tag" not in r.get("Filter", {})
]
def regla(tipo, dias, dias_previas):
    r = {"ID": f"{dominio}/{tipo}", "Status": "Enabled", "Filter": {"Prefix": f"{dominio}/{tipo}/"}}
    if dias:
        r["Expiration"] = {"Days": dias}
    if dias_previas:
        r["NoncurrentVersionExpiration"] = {"NoncurrentDays": dias_previas}
    return r
# `anual/` no lleva regla: sin regla, S3 no borra nada (ni la copia ni sus versiones previas).
reglas += [regla("diario", 30, 30), regla("puntual", 30, 30), regla("mensual", 365, 365)]
reglas.append({"ID": "general", "Status": "Enabled", "Filter": {"Prefix": ""},
               "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
               "Expiration": {"ExpiredObjectDeleteMarker": True}})
print(json.dumps({"Rules": reglas}))
' <<< "$ACTUALES")"
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration "$REGLAS"
echo "Bucket $BUCKET listo (privado, cifrado, versionado) con las reglas de $DOMINIO/."

if ! aws iam get-user --user-name "$USUARIO" >/dev/null 2>&1; then
  echo "Creando usuario IAM $USUARIO"
  aws iam create-user --user-name "$USUARIO" >/dev/null
fi
POLITICA="$(sed -e "s/__BUCKET__/$BUCKET/g" -e "s/__DOMINIO__/$DOMINIO/g" "$AQUI/politica_servidor.json.plantilla")"
aws iam put-user-policy --user-name "$USUARIO" --policy-name solo-subir-respaldos --policy-document "$POLITICA"
echo "Usuario $USUARIO: solo puede subir bajo $BUCKET/$DOMINIO/."

if [ -n "$ARCHIVO_LLAVE" ]; then
  umask 077
  aws iam create-access-key --user-name "$USUARIO" --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text |
    awk '{print "RESPALDO_AWS_ACCESS_KEY_ID=" $1 "\nRESPALDO_AWS_SECRET_ACCESS_KEY=" $2}' > "$ARCHIVO_LLAVE"
  echo "Llave nueva guardada en $ARCHIVO_LLAVE (no se muestra)."
fi
