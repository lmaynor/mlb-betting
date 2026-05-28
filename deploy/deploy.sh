#!/usr/bin/env bash
# deploy.sh — provision GCP infrastructure and deploy to Cloud Run.
#
# Run once to bootstrap, then use deploy_update.sh for subsequent pushes.
#
# Prerequisites:
#   gcloud CLI installed and authenticated
#   PROJECT_ID set below or via env var
#   Discord webhook URL(s) ready
#
# Usage:
#   chmod +x deploy.sh
#   PROJECT_ID=my-gcp-project ./deploy.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="us-central1"
SERVICE_NAME="mlb-betting"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
BUCKET="${PROJECT_ID}-mlb-data"
DB_INSTANCE="${SERVICE_NAME}-db"
DB_NAME="mlb_betting"
DB_USER="mlb"

echo "=== MLB Betting GCP Deploy ==="
echo "Project : $PROJECT_ID"
echo "Region  : $REGION"
echo "Service : $SERVICE_NAME"
echo ""

# ── 1. Set project ──────────────────────────────────────────────────────────
gcloud config set project "$PROJECT_ID"

# ── 2. Enable APIs ──────────────────────────────────────────────────────────
echo "Enabling APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  sqladmin.googleapis.com \
  storage.googleapis.com \
  --quiet

# ── 3. GCS bucket ───────────────────────────────────────────────────────────
echo "Creating GCS bucket gs://$BUCKET ..."
gsutil mb -p "$PROJECT_ID" -l "$REGION" "gs://$BUCKET" 2>/dev/null || echo "  (bucket already exists)"
gsutil versioning set on "gs://$BUCKET"

# ── 4. Cloud SQL (Postgres) ─────────────────────────────────────────────────
echo "Creating Cloud SQL instance $DB_INSTANCE (this takes ~5 min)..."
gcloud sql instances create "$DB_INSTANCE" \
  --database-version=POSTGRES_15 \
  --tier=db-f1-micro \
  --region="$REGION" \
  --no-assign-ip \
  --quiet 2>/dev/null || echo "  (instance already exists)"

gcloud sql databases create "$DB_NAME" --instance="$DB_INSTANCE" --quiet 2>/dev/null || true
DB_PASSWORD=$(openssl rand -base64 24)
gcloud sql users create "$DB_USER" \
  --instance="$DB_INSTANCE" \
  --password="$DB_PASSWORD" \
  --quiet 2>/dev/null || echo "  (user already exists — password NOT reset)"

INSTANCE_CONNECTION_NAME=$(gcloud sql instances describe "$DB_INSTANCE" \
  --format="value(connectionName)")
DB_URL="postgresql+pg8000://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?unix_sock=/cloudsql/${INSTANCE_CONNECTION_NAME}/.s.PGSQL.5432"

# ── 5. Secrets ──────────────────────────────────────────────────────────────
echo "Storing secrets..."

_store_secret() {
  local name="$1"
  local value="$2"
  echo -n "$value" | gcloud secrets create "$name" --data-file=- --quiet 2>/dev/null \
    || echo -n "$value" | gcloud secrets versions add "$name" --data-file=- --quiet
}

_store_secret "mlb-db-url" "$DB_URL"
_store_secret "mlb-gcs-bucket" "$BUCKET"

# Discord webhooks — prompt if not already set as env vars
if [[ -z "${DISCORD_WEBHOOK_URL:-}" ]]; then
  echo ""
  echo "Enter your Discord webhook URL (or press Enter to skip):"
  read -r DISCORD_WEBHOOK_URL
fi
if [[ -n "${DISCORD_WEBHOOK_URL:-}" ]]; then
  _store_secret "discord-webhook-url" "$DISCORD_WEBHOOK_URL"
fi

# ── 6. Service account ──────────────────────────────────────────────────────
SA_NAME="${SERVICE_NAME}-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
echo "Creating service account $SA_EMAIL ..."
gcloud iam service-accounts create "$SA_NAME" \
  --display-name="MLB Betting Cloud Run SA" \
  --quiet 2>/dev/null || echo "  (SA already exists)"

for role in \
  roles/storage.objectAdmin \
  roles/cloudsql.client \
  roles/secretmanager.secretAccessor \
  roles/run.invoker; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$role" \
    --quiet
done

# ── 7. Build & push Docker image ────────────────────────────────────────────
echo "Building Docker image..."
gcloud builds submit --tag "$IMAGE" --quiet

# ── 8. Deploy Cloud Run service ─────────────────────────────────────────────
echo "Deploying Cloud Run service..."
gcloud run deploy "$SERVICE_NAME" \
  --image="$IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --service-account="$SA_EMAIL" \
  --add-cloudsql-instances="$INSTANCE_CONNECTION_NAME" \
  --set-secrets="MLB_DB_URL=mlb-db-url:latest,MLB_GCS_BUCKET=mlb-gcs-bucket:latest,DISCORD_WEBHOOK_URL=discord-webhook-url:latest" \
  --set-env-vars="GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION}" \
  --memory=2Gi \
  --cpu=2 \
  --timeout=900 \
  --concurrency=1 \
  --max-instances=1 \
  --no-allow-unauthenticated \
  --quiet

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" --format="value(status.url)")
echo "Service deployed at: $SERVICE_URL"

# ── 9. Cloud Scheduler jobs ─────────────────────────────────────────────────
echo "Creating Cloud Scheduler jobs..."

SCHEDULER_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud iam service-accounts create "scheduler-invoker" \
  --display-name="Cloud Scheduler Invoker" \
  --quiet 2>/dev/null || true
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
  --region="$REGION" \
  --member="serviceAccount:${SCHEDULER_SA}" \
  --role="roles/run.invoker" \
  --quiet

# Morning run: 9 AM ET = 13:00 UTC (data refresh + predictions)
gcloud scheduler jobs create http "${SERVICE_NAME}-morning" \
  --location="$REGION" \
  --schedule="0 13 * * *" \
  --uri="${SERVICE_URL}/run" \
  --message-body='{"systems":["HR","1IOU","F5","K","BATTER_HITS","BATTER_TB","GAME","1I"],"run_type":"morning"}' \
  --headers="Content-Type=application/json" \
  --oidc-service-account-email="$SCHEDULER_SA" \
  --oidc-token-audience="$SERVICE_URL" \
  --time-zone="UTC" \
  --quiet 2>/dev/null || echo "  (morning job already exists)"

# Evening run: 5 PM ET = 21:00 UTC (predictions only, odds updated)
gcloud scheduler jobs create http "${SERVICE_NAME}-evening" \
  --location="$REGION" \
  --schedule="0 21 * * *" \
  --uri="${SERVICE_URL}/run" \
  --message-body='{"systems":["HR","1IOU","F5","K","BATTER_HITS","BATTER_TB","GAME","1I"],"run_type":"evening"}' \
  --headers="Content-Type=application/json" \
  --oidc-service-account-email="$SCHEDULER_SA" \
  --oidc-token-audience="$SERVICE_URL" \
  --time-zone="UTC" \
  --quiet 2>/dev/null || echo "  (evening job already exists)"

echo ""
echo "=== Deploy complete ==="
echo "Service URL : $SERVICE_URL"
echo "GCS bucket  : gs://$BUCKET"
echo "Cloud SQL   : $INSTANCE_CONNECTION_NAME"
echo ""
echo "Next steps:"
echo "  1. Upload model files:  gsutil -m cp -r ./models gs://$BUCKET/"
echo "  2. Upload historical data (if not rebuilding from scratch)"
echo "  3. Test a manual run:"
echo "     curl -X POST $SERVICE_URL/run \\"
echo "       -H 'Authorization: Bearer \$(gcloud auth print-identity-token)' \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"systems\":[\"NRFI\"],\"run_type\":\"data\"}'"
