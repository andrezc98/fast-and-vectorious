#!/usr/bin/env bash
# Deletes everything deploy.sh created (table, Lambda, API, logs; and the DNS records in event mode).
set -euo pipefail
cd "$(dirname "$0")"
terraform -chdir=terraform destroy -input=false -auto-approve "$@"
