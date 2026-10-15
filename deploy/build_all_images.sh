#!/usr/bin/env bash
# build_all_images.sh -- build the container image ONCE from a clean checkout and retag that exact digest onto every image
# repository the Cloud Run service and jobs use, so no job quietly runs a different build than the rest:
#   gcr.io/<project>/mlb-betting:latest        service + every scheduled MLB job
#   gcr.io/<project>/mlb-betting-feeds:latest  odds banking, NHL ingest, props alerts (deploy/setup_*.sh default IMAGE)
#   gcr.io/<project>/mlb-betting-ev:shadow     ev-nfl-props-sweep
#   gcr.io/<project>/mlb-betting-bp-crawl:latest  BettingPros crawl jobs
# All four are the same Dockerfile (cloudbuild.yaml); they were separate repos only so a feature build could not change what
# the production MLB jobs run. This script re-couples them ON PURPOSE, from one reviewed commit.
#
# BLAST RADIUS (read before running): a Cloud Run JOB resolves its image tag at EVERY EXECUTION
# (docs/solutions/conventions/cloud-run-job-image-tag-resolution.md). Retagging mlb-betting:latest therefore changes what every
# scheduled MLB job runs on its NEXT execution -- including merged-but-undeployed code. The service is NOT affected until you
# redeploy it (`gcloud run services update mlb-betting --image=gcr.io/<project>/mlb-betting@<digest>`; the digest is printed
# at the end). SKIP_PROD=1 refreshes only the three sport-feed images.
#
# Guards (this script bakes whatever is in the working tree into production images):
#   - refuses a dirty tree (git status --porcelain, untracked files included: the Docker context is the directory) unless ALLOW_DIRTY=1
#   - refuses HEAD != origin/main unless ALLOW_NON_MAIN=1 (build from a clean checkout of main; never a shared/feature tree)
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash deploy/build_all_images.sh
#   DRY_RUN=1 ...   print the plan, build and tag nothing      SKIP_PROD=1 ...   leave mlb-betting:latest alone
#   ALLOW_DIRTY=1 / ALLOW_NON_MAIN=1 ...   explicit overrides of the two guards
# Idempotent: re-running on the same commit reuses the layer cache, resolves the same digest and re-adds the same tags.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-concrete-crow-445205-m4}"
cd "$(dirname "$0")/.."

# Build target: the feeds repo (never scheduled MLB code), so production's tag is only touched after the guards and the build pass.
BUILD_REPO="gcr.io/${PROJECT_ID}/mlb-betting-feeds"
TARGETS=(
  "gcr.io/${PROJECT_ID}/mlb-betting-feeds:latest"
  "gcr.io/${PROJECT_ID}/mlb-betting-ev:shadow"
  "gcr.io/${PROJECT_ID}/mlb-betting-bp-crawl:latest"
)
if [ "${SKIP_PROD:-0}" != "1" ]; then
  TARGETS=("gcr.io/${PROJECT_ID}/mlb-betting:latest" "${TARGETS[@]}")
fi

# ---- guards -------------------------------------------------------------------------------------------------------------
if [ -n "$(git status --porcelain)" ] && [ "${ALLOW_DIRTY:-0}" != "1" ]; then
  echo "REFUSING: the working tree is not clean (git status --porcelain):" >&2
  git status --porcelain | head -20 >&2
  echo "Commit/stash/remove these, build from a clean checkout of origin/main, or pass ALLOW_DIRTY=1 to bake them into production images." >&2
  exit 1
fi
git fetch origin main --quiet || echo "WARNING: could not fetch origin/main; comparing against the last fetched copy." >&2
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ] && [ "${ALLOW_NON_MAIN:-0}" != "1" ]; then
  echo "REFUSING: HEAD ($(git rev-parse --short HEAD)) is not origin/main ($(git rev-parse --short origin/main))." >&2
  echo "Check out a clean origin/main, or pass ALLOW_NON_MAIN=1 on purpose." >&2
  exit 1
fi

COMMIT="$(git rev-parse --short HEAD)"
echo "==> Building ONE image from ${COMMIT} into ${BUILD_REPO}:latest, then tagging its digest onto:"
for T in "${TARGETS[@]}"; do echo "      ${T}"; done
if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "DRY_RUN=1: nothing built or tagged."
  exit 0
fi

# ---- build once (cloudbuild.yaml: --cache-from the previous :latest, see docs/solutions/conventions/cloud-build-layer-caching.md)
gcloud builds submit --config=cloudbuild.yaml --substitutions="_IMAGE=${BUILD_REPO}" --project="$PROJECT_ID" .

DIGEST="$(gcloud container images describe "${BUILD_REPO}:latest" --project="$PROJECT_ID" --format='value(image_summary.digest)')"
case "$DIGEST" in
  sha256:*) ;;
  *) echo "ERROR: could not resolve the built digest (got '${DIGEST}')." >&2; exit 1 ;;
esac

# ---- one digest, every repo -----------------------------------------------------------------------------------------------
for T in "${TARGETS[@]}"; do
  if [ "$T" = "${BUILD_REPO}:latest" ]; then continue; fi          # the build already produced this tag
  gcloud container images add-tag "${BUILD_REPO}@${DIGEST}" "$T" --quiet --project="$PROJECT_ID"
  echo "tagged ${T}"
done

echo
echo "Done: commit ${COMMIT} -> ${DIGEST}"
for T in "${TARGETS[@]}"; do echo "  ${T}"; done
echo "Verify:  gcloud container images list-tags ${BUILD_REPO} --filter=\"digest=${DIGEST}\""
echo "Service (not changed by this script):  gcloud run services update mlb-betting --region=us-central1 --image=gcr.io/${PROJECT_ID}/mlb-betting@${DIGEST} --add-cloudsql-instances=${PROJECT_ID}:us-central1:mlb-betting-db"
