#!/usr/bin/env bash
# One command: build the Lambda, create the stack, load the Ley Chile vectors, print the URL.
# Usage: ./deploy.sh                      (student mode)
#        ./deploy.sh -var-file=event.tfvars  (event mode, needs CLOUDFLARE_API_TOKEN)
set -euo pipefail
cd "$(dirname "$0")"
command -v terraform >/dev/null || { echo "Falta terraform: https://developer.hashicorp.com/terraform/install"; exit 1; }
command -v uv >/dev/null || { echo "Falta uv: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
aws sts get-caller-identity --query Account --output text >/dev/null || { echo "Configura tus credenciales de AWS primero"; exit 1; }

# Lambda bundle: the runtime's boto3 may predate DynamoDB search_vectors (boto3 >= 1.43.64), so ship our own.
mkdir -p build && rm -rf build/pkg && mkdir build/pkg
uv pip install --quiet --target build/pkg "boto3>=1.43.64"
cp app/handler.py app/*.webp build/pkg/
rm -f build/lambda.zip && (cd build/pkg && zip -qr ../lambda.zip .)

terraform -chdir=terraform init -input=false -upgrade >/dev/null
terraform -chdir=terraform apply -input=false -auto-approve "$@"

TABLE=$(terraform -chdir=terraform output -raw table)
REGION=$(terraform -chdir=terraform output -raw region 2>/dev/null || echo us-east-1)
echo "Cargando artículos en $TABLE (la primera vez descarga 11 normas de la BCN y genera los embeddings)…"
uv run --project ../runner python load.py --table "$TABLE" --region "$REGION" ${LOAD_ARGS:-}
echo
echo "Listo: $(terraform -chdir=terraform output -raw url)"
echo "Para borrar todo: ./destroy.sh"
