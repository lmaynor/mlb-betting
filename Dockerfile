# Pinned to a specific digest, not the floating `python:3.11-slim` tag --
# see docs/solutions/conventions/cloud-build-layer-caching.md. A floating
# tag gets silently re-pointed by upstream (a Debian security rebuild, most
# often) between deploys, which invalidates Docker's layer cache for EVERY
# instruction after this FROM regardless of caching mechanism -- confirmed
# live 2026-09-01 (a `--cache-from` build still re-pulled a newer base and
# fully rebuilt). Pinning trades "silent automatic base patches" for a
# stable, cacheable base -- bump this digest deliberately every few months
# (or sooner for a known CVE): `docker pull python:3.11-slim` then
# `docker inspect --format='{{index .RepoDigests 0}}' python:3.11-slim`.
FROM python:3.11-slim@sha256:d1e9ca7c4e78d1e8ecadb5d44bfc8e956e7a65b659a9950f569f243d72b326d0

# No system build deps needed: pg8000 is pure-Python (no libpq),
# all pip packages install from prebuilt manylinux wheels.

WORKDIR /app

# Install Python deps first (layer cache).
# Uninstall nvidia-nccl-cu12 (300MB, pulled transitively by xgboost) in the SAME
# RUN step so it is never committed to a layer -- removing it in a later RUN would
# not shrink the image, since layers are additive.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y nvidia-nccl-cu12 2>/dev/null || true

# Copy source
COPY mlb_core/      ./mlb_core/
COPY nba/           ./nba/
# mlb/ pillar: runners/, training/, systems/{HR_Pro, NRFI_Pro_System, ...}
COPY mlb/           ./mlb/
COPY main.py          .
COPY tweet_drafter.py .
COPY setup.py         .

# Install mlb_core as a package (eliminates all sys.path hacks). --no-deps is REQUIRED:
# setup.py reads install_requires from requirements.txt (already installed above), so
# skipping dependency resolution here avoids re-pulling the 300MB nvidia-nccl-cu12 that
# we uninstalled in the requirements layer. Without --no-deps, `-e .` re-installs nccl
# and it ends up baked into the final image.
RUN pip install --no-cache-dir -e . --no-deps

# Cloud Run listens on $PORT (default 8080)
ENV PORT=8080
EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "3600", "main:app"]
