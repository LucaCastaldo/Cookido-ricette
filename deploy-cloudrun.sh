#!/usr/bin/env bash
# Pubblica Bimby Bridge su Google Cloud Run (piano gratuito).
# Da eseguire in Google Cloud Shell, dentro la cartella bimby-bridge.
set -euo pipefail

REGION="${REGION:-europe-west1}"   # Belgio: regione di fascia 1, coperta dal piano gratuito
SERVICE="bimby-bridge"

PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
if [ -z "$PROJECT" ]; then
  echo "Nessun progetto selezionato. Esegui: gcloud config set project <ID-PROGETTO>"; exit 1
fi
echo "Progetto: $PROJECT  Regione: $REGION"

read -rp  "Email Cookidoo: " COOKIDOO_EMAIL
read -rsp "Password Cookidoo: " COOKIDOO_PASSWORD; echo
BRIDGE_SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"

echo "Abilito i servizi necessari (la prima volta richiede un minuto)…"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --quiet

# Le variabili passano da un file temporaneo per non lasciarle nella cronologia della shell
ENVFILE="$(mktemp)"
trap 'rm -f "$ENVFILE"' EXIT
COOKIDOO_EMAIL="$COOKIDOO_EMAIL" COOKIDOO_PASSWORD="$COOKIDOO_PASSWORD" BRIDGE_SECRET="$BRIDGE_SECRET" \
python3 - "$ENVFILE" <<'PY'
import json, os, sys
keys = ["COOKIDOO_EMAIL", "COOKIDOO_PASSWORD", "BRIDGE_SECRET"]
with open(sys.argv[1], "w") as f:
    for k in keys:
        f.write(f"{k}: {json.dumps(os.environ[k])}\n")   # stringhe JSON = YAML valido, password con caratteri speciali incluse
    f.write('COOKIDOO_COUNTRY: "it"\nCOOKIDOO_LANGUAGE: "it-IT"\n')
PY

gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 1 \
  --memory 512Mi --cpu 1 \
  --timeout 120 \
  --env-vars-file "$ENVFILE" \
  --quiet

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')"
echo
echo "Controllo: $(curl -s "$URL/health")"
echo
echo "================================================================"
echo " URL da incollare nel connettore 'Bimby Bridge' su claude.ai:"
echo " $URL/$BRIDGE_SECRET/mcp"
echo " Salvalo: contiene il segreto e non verrà mostrato di nuovo."
echo "================================================================"
