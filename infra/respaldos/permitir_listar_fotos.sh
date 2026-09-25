#!/usr/bin/env bash
# Ticket 10 (`.scratch/respaldos-y-restauracion`): la llave de fotos del servidor gana `s3:ListBucket` SOLO sobre el
# bucket/prefijo de fotos, para copiarlas al servidor (ya tiene `s3:GetObject`). Idempotente; se corre desde una PC
# con la AWS CLI autenticada en la cuenta 172460160630.
#
#   infra/respaldos/permitir_listar_fotos.sh <usuario-iam-de-fotos> <bucket-de-fotos> [prefijo]
set -euo pipefail
USUARIO="${1:?Uso: $0 <usuario-iam-de-fotos> <bucket-de-fotos> [prefijo]}"
BUCKET_FOTOS="${2:?Falta el bucket de fotos}"
PREFIJO="${3:-paquetes-recibidos-imagenes/}"
AQUI="$(cd "$(dirname "$0")" && pwd)"
POLITICA="$(sed -e "s|__BUCKET_FOTOS__|$BUCKET_FOTOS|g" -e "s|__PREFIJO__|$PREFIJO|g" "$AQUI/politica_listar_fotos.json.plantilla")"
aws iam put-user-policy --user-name "$USUARIO" --policy-name listar-fotos-para-respaldos --policy-document "$POLITICA"
echo "$USUARIO: puede listar $BUCKET_FOTOS/$PREFIJO*"
