# Serves the recommendation API only — the UI is built separately and hosted on
# GitHub Pages (see front/README.md). `data/embeddings` and `data/serve` are too large
# for the git repo; the entrypoint downloads them from a GitHub Release asset (DATA_URL)
# on first start, into a volume — never baked into the image.
FROM python:3.14-slim

WORKDIR /app

# curl+ca-certificates fetch DATA_URL over HTTPS; tar (already in the base image) or
# unzip extracts it, depending on the asset's format. sha256sum (coreutils) is already
# present, for DATA_SHA256 verification.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates unzip \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# CPU-only wheel, installed from its own index: the default PyPI linux wheel drags in
# the CUDA runtime (nvidia-*), multiple GB nothing here uses — inference runs on CPU
# in serving too (see README, "beats MPS by 3.3x" in batches).
RUN pip install --no-cache-dir torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt

COPY api/ ./api
COPY config/ ./config
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Pre-created and owned by the runtime user so a fresh named volume mounted here
# (or the entrypoint's download-and-extract) doesn't hit a root-owned mount point.
RUN mkdir -p data && useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8000

# Required for Coolify/Traefik: without a Docker health status, the platform never
# adds the container to the routable backend pool ("no available server") — it isn't
# optional there, unlike on platforms that just watch for the process staying up.
# start-period covers a cold volume's first-boot download; a warm volume (the normal
# case — see README, pre-warming) reports healthy in seconds regardless.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10m \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/config', timeout=3)" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
