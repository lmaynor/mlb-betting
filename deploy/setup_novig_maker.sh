#!/usr/bin/env bash
# setup_novig_maker.sh -- provision the NOV-03 maker study launch runner as a Cloud Run Job.
#
#   job  novig-maker-study   python -m ev.novig.maker_runner --max-runtime-sec 1500
#
# PAPER ONLY. Study window 2026-11-14..28 (pre-registered, ev.novig.maker_study). Real-money
# stays refused: the only clients the runner builds are the Novig PAPER sandbox (trading key)
# and the public read-only catalog. NO scheduler is provisioned here -- scheduling the study's
# firings is a separate decision; run it manually:
#   gcloud run jobs execute novig-maker-study --region=us-central1 --project=$PROJECT_ID
#
# WHY THE API: the job spec is the NBA paper executor's spec (novig-paper-executor-nba) with the
# image and args swapped. Its paper trading key arrives as a SECRET VOLUME MOUNT
# (/secrets/novig/paper_trading.pem) plus the key id as a secret env var, and
# `gcloud run jobs create --add-volume type=secret` rejects that shape ("Volume type secret not
# supported" on gcloud 592). So this script creates/updates the job through the Cloud Run Admin
# v2 API, reading the NBA job's live spec as the template (env, volumes, mounts, service account
# are inherited verbatim, never re-typed here).
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 IMAGE=gcr.io/concrete-crow-445205-m4/mlb-betting-feeds:nov03-maker-20261114 \
#     bash deploy/setup_novig_maker.sh
# IMAGE defaults to the feeds :latest resolution used by the other Novig jobs; pass a branch tag
# to run unmerged code (the NOV-03 launch does exactly that).
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
JOB="novig-maker-study"
TEMPLATE_JOB="${TEMPLATE_JOB:-novig-paper-executor-nba}"
IMAGE="${IMAGE:-gcr.io/${PROJECT_ID}/mlb-betting-feeds}"
MAX_RUNTIME_SEC="${MAX_RUNTIME_SEC:-1500}"

command -v gcloud >/dev/null || { echo "ERROR: gcloud not on PATH"; exit 1; }
gcloud run jobs describe "$TEMPLATE_JOB" --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: template job $TEMPLATE_JOB not found (its spec carries the paper-key wiring)."; exit 1; }
gcloud container images describe "$IMAGE" --quiet >/dev/null 2>&1 \
  || { echo "ERROR: image $IMAGE not found (build it first; see cloudbuild.yaml branch-tag notes)."; exit 1; }

export MAKER_PROJECT="$PROJECT_ID" MAKER_REGION="$REGION" MAKER_JOB="$JOB" \
       MAKER_TEMPLATE_JOB="$TEMPLATE_JOB" MAKER_IMAGE="$IMAGE" MAKER_MAX_RUNTIME="$MAX_RUNTIME_SEC"
python3 - <<'PYEOF'
import json, os, subprocess, sys

import google.auth
from google.auth.transport.requests import Request as GReq
import requests

P, R = os.environ["MAKER_PROJECT"], os.environ["MAKER_REGION"]
JOB, TPL = os.environ["MAKER_JOB"], os.environ["MAKER_TEMPLATE_JOB"]
IMG, MAXRT = os.environ["MAKER_IMAGE"], os.environ["MAKER_MAX_RUNTIME"]

raw = subprocess.run(["gcloud", "run", "jobs", "describe", TPL, "--region", R,
                      "--project", P, "--format=json"],
                     capture_output=True, text=True, check=True).stdout
spec = json.loads(raw)["spec"]["template"]["spec"]
cont = spec["template"]["spec"]["containers"][0]
cont["image"] = IMG
cont["args"] = ["-m", "ev.novig.maker_runner", "--max-runtime-sec", MAXRT]
env = []
for e in cont["env"]:
    if "valueFrom" in e:
        ref = e["valueFrom"]["secretKeyRef"]
        env.append({"name": e["name"],
                    "valueSource": {"secretKeyRef": {"secret": ref["name"], "version": ref["key"]}}})
    else:
        env.append({"name": e["name"], "value": e["value"]})
cont["env"] = env
vols = [{"name": v["name"],
         "secret": {"secret": v["secret"]["secretName"],
                    "items": [{"path": it["path"], "version": it["key"]}
                              for it in v["secret"].get("items", [])]}}
        for v in spec["template"]["spec"].get("volumes", [])]
body = {"template": {"taskCount": 1, "template": {
    "serviceAccount": spec["template"]["spec"]["serviceAccountName"],
    "timeout": "1800s", "maxRetries": 0, "containers": [cont], "volumes": vols}}}

creds, _ = google.auth.default()
creds.refresh(GReq())
hdr = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}
base = f"https://run.googleapis.com/v2/projects/{P}/locations/{R}/jobs"
r = requests.get(f"{base}/{JOB}", headers=hdr, timeout=60)
if r.status_code == 200:
    r = requests.patch(f"{base}/{JOB}", headers=hdr, json=body, timeout=60)
    verb = "updated"
else:
    r = requests.post(base, params={"job_id": JOB}, headers=hdr, json=body, timeout=60)
    verb = "created"
if r.status_code >= 300:
    print(f"ERROR: job write failed: {r.status_code} {r.text[:400]}", file=sys.stderr)
    sys.exit(1)
print(f"job {JOB} {verb} (image {IMG})")
PYEOF

echo "Job $JOB configured successfully (NO scheduler provisioned)."
echo "To execute manually:"
echo "  gcloud run jobs execute $JOB --region=$REGION --project=$PROJECT_ID"
