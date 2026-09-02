---
title: Always build the service image via cloudbuild.yaml (--cache-from), never bare `gcloud builds submit --tag`
module: deploy/deploy_service.sh, cloudbuild.yaml, Dockerfile
tags: [cloud-build, docker, artifact-registry, deploy, gcr, cost, layer-cache]
problem_type: convention
category: conventions
date: 2026-09-01
---

## Context

Every deploy built the image with plain `gcloud builds submit --tag="$IMAGE"`
(`deploy/deploy_service.sh`). Cloud Build's managed workers start from a
clean environment on every invocation -- there is no Docker layer cache
carried over from the previous build unless you explicitly wire one up.
The Dockerfile already orders `COPY requirements.txt` + `RUN pip install`
*before* copying source (correct, cache-friendly ordering in principle),
but that only helps if there's a cache to hit in the first place.

Combined with pip installs not being byte-reproducible (embedded
timestamps, `.dist-info` metadata, compiled bytecode differ run to run even
for identical package versions), every single deploy re-ran `pip install`
from scratch and re-uploaded a full ~1.5-2GB dependency layer to Artifact
Registry as a **brand-new, distinct blob** -- even when `requirements.txt`
hadn't changed at all. 281 deploys over ~4 months (2026-05-07 to 08-27)
ballooned `gcr.io` to ~200GB, ~$20/mo and growing ~$5/mo every additional
month, found during a GCP cost review
([[project_gcp_cost_review_2026-09-01]]). A cleanup policy + one-time purge
fixed the symptom (capped at 30 kept versions, auto-deletes untagged images
>30 days old); this is the actual disease fix.

## Guidance

**Build the service image through `cloudbuild.yaml` at the repo root, not a
bare `--tag` build.** `deploy/deploy_service.sh` already does this:

```bash
gcloud builds submit --config=cloudbuild.yaml \
  --substitutions="_IMAGE=gcr.io/${PROJECT_ID}/${SERVICE}" \
  --project="$PROJECT_ID" .
```

`cloudbuild.yaml` pulls the previous `:latest` image first (best-effort --
`|| echo ...` so a first-ever build doesn't fail) and builds with
`--cache-from` against it, forcing the classic (non-BuildKit) builder via
`DOCKER_BUILDKIT=0` so plain `--cache-from` against a locally-pulled image
works without needing inline-cache build args on either side:

```yaml
docker pull ${_IMAGE}:latest || echo "no previous image to cache from (first build)"
docker build --cache-from ${_IMAGE}:latest -t ${_IMAGE}:latest .
```

When `requirements.txt` (and everything above it in the Dockerfile) is
unchanged, Docker reuses the **exact prior layer digest** for the
dependency install instead of re-running pip and re-uploading a new blob --
so a routine code-only deploy should push a small new top layer (just the
changed source), not a multi-GB one.

If you ever need a one-off manual build outside `deploy_service.sh` (e.g. to
refresh `:latest` for `mlb-bakeoff` per `deploy/setup_bakeoff_job.sh`'s own
comment), use the same config:

```bash
gcloud builds submit --config=cloudbuild.yaml \
  --substitutions=_IMAGE=gcr.io/concrete-crow-445205-m4/mlb-betting .
```

not `gcloud builds submit --tag=...` directly -- that reintroduces the
uncached, ever-growing-registry behavior this convention exists to prevent.

## Why This Matters

This isn't just a storage-cost issue -- an uncached build also means every
deploy pays the full wall-clock cost of reinstalling pandas/numpy/xgboost/
scikit-learn/pyarrow/pybaseball from PyPI, every time, regardless of what
actually changed. A cache hit turns that into a no-op.

## When to Apply

Any time the `mlb-betting` service image gets built -- routine deploys via
`deploy_service.sh`, or a manual rebuild to refresh `:latest` for a Cloud
Run Job that shares the image (`mlb-bakeoff`, etc.). `deploy/deploy.sh` is
already stale/guarded (finding B2.5) and out of scope; if it's ever revived
it should adopt this same config, not the historical bare `--tag` command
still preserved in its unreachable reference section.
