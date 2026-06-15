FROM python:3.11-slim

# No system build deps needed: pg8000 is pure-Python (no libpq),
# all pip packages install from prebuilt manylinux wheels.

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY mlb_core/      ./mlb_core/
COPY nba/           ./nba/
COPY NRFI_Pro_System/ ./NRFI_Pro_System/
COPY HR_Pro/          ./HR_Pro/
COPY F5_Pro_System/   ./F5_Pro_System/
COPY K_Pro_System/    ./K_Pro_System/
COPY OUTS_Pro_System/ ./OUTS_Pro_System/
COPY BATTER_HITS_System/ ./BATTER_HITS_System/
COPY BATTER_TB_System/   ./BATTER_TB_System/
COPY GAME_Pro_System/    ./GAME_Pro_System/
COPY runners/         ./runners/
COPY training/        ./training/
COPY main.py          .
COPY tweet_drafter.py .
COPY setup.py         .

# Install mlb_core as a package (eliminates all sys.path hacks)
# Uninstall NCCL here (after all pip steps) so it's only downloaded once.
RUN pip install --no-cache-dir -e . \
    && pip uninstall -y nvidia-nccl-cu12 2>/dev/null || true

# Cloud Run listens on $PORT (default 8080)
ENV PORT=8080
EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "3600", "main:app"]
