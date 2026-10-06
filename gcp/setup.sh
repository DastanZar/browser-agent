#!/usr/bin/env bash
# Zero-click Google Cloud setup: edit the variables, run with DRY_RUN=1 to see every command,
# then run for real. Safe to re-run: each step skips what already exists.
#   DRY_RUN=1 ./setup.sh     # print only
#   ./setup.sh               # apply (needs `gcloud auth login` first)
set -euo pipefail

PROJECT="${PROJECT:-my-project-id}"
CREATE_PROJECT="${CREATE_PROJECT:-0}"          # 1 = create the project if missing
BILLING_ACCOUNT="${BILLING_ACCOUNT:-}"          # e.g. 0123AB-4567CD-89EF01 (gcloud billing accounts list)
APIS=(youtube.googleapis.com youtubeanalytics.googleapis.com drive.googleapis.com)
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-automation}"
SA_ROLES=(roles/storage.objectAdmin)
BUCKET="${BUCKET:-}"                            # e.g. gs://my-project-assets (blank = skip)
REGION="${REGION:-us-central1}"

run() { echo "+ $*"; [[ "${DRY_RUN:-0}" == 1 ]] || "$@"; }
SA_EMAIL="$SERVICE_ACCOUNT@$PROJECT.iam.gserviceaccount.com"

if [[ "$CREATE_PROJECT" == 1 ]] && ! gcloud projects describe "$PROJECT" >/dev/null 2>&1; then
  run gcloud projects create "$PROJECT"
fi
[[ -n "$BILLING_ACCOUNT" ]] && run gcloud billing projects link "$PROJECT" --billing-account "$BILLING_ACCOUNT"

run gcloud services enable "${APIS[@]}" --project "$PROJECT"

if ! gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT" >/dev/null 2>&1; then
  run gcloud iam service-accounts create "$SERVICE_ACCOUNT" --project "$PROJECT" --display-name "$SERVICE_ACCOUNT"
fi
for role in "${SA_ROLES[@]}"; do
  run gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA_EMAIL" \
    --role "$role" --condition None --quiet --format none
done

if [[ -n "$BUCKET" ]] && ! gcloud storage buckets describe "$BUCKET" >/dev/null 2>&1; then
  run gcloud storage buckets create "$BUCKET" --project "$PROJECT" --location "$REGION" --uniform-bucket-level-access
fi
echo "done: $PROJECT"
