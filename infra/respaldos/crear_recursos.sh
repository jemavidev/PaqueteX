#!/usr/bin/env bash
# Crea (o deja al día) en la cuenta de AWS de Jesús lo que necesitan los respaldos (`.scratch/respaldos-y-restauracion`,
# ticket 04). Idempotente: se puede correr de nuevo sin romper nada. Se corre desde una PC con la AWS CLI autenticada
# en la cuenta 172460160630 (NO desde el servidor).
#
#   infra/respaldos/crear_recursos.sh <dominio> [archivo-para-la-llave]
#
# - Bucket `paquetex-respaldos`: privado (bloqueo de acceso público), cifrado por defecto de S3, versionado (una subida
#   con el mismo nombre nunca destruye la anterior) y las reglas de `ciclo_de_vida.json` (diario/puntual 30 días,
#   mensual 365; anual sin regla = sin fecha de borrado). Las reglas filtran por la etiqueta `tipo` de cada objeto.
# - Usuario IAM `paquetex-respaldos-<dominio>` con la política de `politica_servidor.json.plantilla`: SOLO puede subir
#   bajo `<dominio>/` -- ni leer, ni listar, ni borrar, ni tocar la carpeta de otro dominio.
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
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration "file://$AQUI/ciclo_de_vida.json"
echo "Bucket $BUCKET listo (privado, cifrado, versionado, con reglas de conservación)."

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
