FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY mlb_core/      ./mlb_core/
COPY NRFI_Pro_System/ ./NRFI_Pro_System/
COPY HR_Pro/          ./HR_Pro/
COPY F5_Pro_System/   ./F5_Pro_System/
COPY K_Pro_System/    ./K_Pro_System/
COPY runners/         ./runners/
COPY training/        ./training/
COPY main.py          .
COPY tweet_drafter.py .
COPY setup.py         .

# Install mlb_core as a package (eliminates all sys.path hacks)
RUN pip install --no-cache-dir -e .

# Cloud Run listens on $PORT (default 8080)
ENV PORT=8080
EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "3600", "main:app"]
