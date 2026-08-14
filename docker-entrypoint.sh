#!/bin/sh
# Fetches data/embeddings + data/serve (too large for the git repo) from a GitHub
# Release asset before the API starts. See README, "Run the API", for how the
# archive is built and published.
set -eu

MARKER="data/serve/album_index.parquet"

if [ ! -f "$MARKER" ]; then
  if [ -z "${DATA_URL:-}" ]; then
    echo "error: $MARKER not found and DATA_URL is not set." >&2
    echo "       either mount a populated data/ volume, or set DATA_URL to a" >&2
    echo "       data.tar.gz release asset — see README, 'Run the API'." >&2
    exit 1
  fi

  echo "data/ not found — downloading from $DATA_URL"
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  curl -fSL "$DATA_URL" -o "$tmp"
  echo "extracting…"
  # Archive is rooted at "data/" (i.e. built as `tar -czf data.tar.gz data/`), so it
  # unpacks relative to the working directory, not straight into it.
  tar -xzf "$tmp" -C .

  if [ ! -f "$MARKER" ]; then
    echo "error: extracted archive but $MARKER is still missing — check the archive layout" >&2
    exit 1
  fi
  echo "done"
fi

exec "$@"
