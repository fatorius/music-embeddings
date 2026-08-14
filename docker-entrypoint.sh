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
    echo "       release asset (.zip or .tar.gz) — see README, 'Run the API'." >&2
    exit 1
  fi

  echo "data/ not found — downloading from $DATA_URL"
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  curl -fSL "$DATA_URL" -o "$tmp"

  if [ -n "${DATA_SHA256:-}" ]; then
    echo "verifying checksum…"
    want="${DATA_SHA256#sha256:}"
    got="$(sha256sum "$tmp" | cut -d' ' -f1)"
    if [ "$got" != "$want" ]; then
      echo "error: checksum mismatch for $DATA_URL" >&2
      echo "       expected $want" >&2
      echo "       got      $got" >&2
      exit 1
    fi
  fi

  echo "extracting…"
  # Archive is rooted at "data/" (e.g. `zip -r data.zip data/` or
  # `tar -czf data.tar.gz data/`), so it unpacks relative to the working directory,
  # not straight into it.
  case "$DATA_URL" in
    *.zip) unzip -q "$tmp" -x '__MACOSX/*' -d . ;;
    *) tar -xzf "$tmp" -C . ;;
  esac

  if [ ! -f "$MARKER" ]; then
    echo "error: extracted archive but $MARKER is still missing — check the archive layout" >&2
    exit 1
  fi
  echo "done"
fi

exec "$@"
