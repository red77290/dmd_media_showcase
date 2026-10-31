FROM python:3.12-slim

LABEL org.opencontainers.image.title="dmd-media-showcase" \
      org.opencontainers.image.description="DMD Media Showcase & Real-Time UDP/MQTT Video Streaming Server for HUB75 LED Matrices (ArcadeMatrix ESP32 / RPi)" \
      org.opencontainers.image.url="https://github.com/red77290/dmd_media_showcase" \
      org.opencontainers.image.source="https://github.com/red77290/dmd_media_showcase" \
      org.opencontainers.image.documentation="https://github.com/red77290/dmd_media_showcase/blob/main/docs/QUICKSTART.md" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8890 \
    MQTT_PORT=1883 \
    DMD_DATA_DIR=/app/data \
    CACHE_DIR=/tmp/dmd_media_showcase_cache

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        curl \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY docs/asyncapi.yaml ./docs/asyncapi.yaml
COPY scripts/compile_prod_vault.py ./scripts/compile_prod_vault.py

# Resolved commit SHA of dmd_gif_converter@latest (injected by CI to invalidate layer cache on new releases)
ARG DMD_GIF_CONVERTER_REV=latest

RUN --mount=type=secret,id=VAULT_MASTER_SECRET,required=false \
    --mount=type=secret,id=TMDB_API_KEY,required=false \
    --mount=type=secret,id=TVDB_API_KEY,required=false \
    --mount=type=secret,id=SCREENSCRAPER_DEV_ID,required=false \
    --mount=type=secret,id=SCREENSCRAPER_DEV_PASS,required=false \
    --mount=type=secret,id=IGDB_CLIENT_ID,required=false \
    --mount=type=secret,id=IGDB_CLIENT_SECRET,required=false \
    apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev python3-dev && \
    pip install --upgrade pip setuptools && \
    python3 scripts/compile_prod_vault.py && \
    rm -rf scripts build && \
    sed -i "s|dmd_gif_converter.git@latest|dmd_gif_converter.git@${DMD_GIF_CONVERTER_REV}|g" pyproject.toml && \
    pip install . && \
    python3 -c "import dmd_gif_converter as c; print('Bundled dmd_gif_converter@latest:', c.__version__)" && \
    apt-get purge -y --auto-remove gcc libc6-dev python3-dev && \
    rm -rf /var/lib/apt/lists/* && \
    python3 -c "from src.plugins.detectors.detector import _ensure_yolo_model; _ensure_yolo_model()" && \
    mkdir -p /app/data /tmp/dmd_media_showcase_cache

EXPOSE 8890 1883 5353/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8890/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8890", "--proxy-headers", "--forwarded-allow-ips", "*"]
