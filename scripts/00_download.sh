#!/usr/bin/env bash
# Download the Zenodo 5002584 dump (Playlist2vec / reconstructed Spotify MPD).
# Resumable and resilient: Zenodo drops the connection on large files
# (curl 18: "end of response with N bytes missing"), so the download runs in a
# loop that resumes where it left off until it completes.
#
# Usage (tmux recommended), from any directory:
#   tmux new -s mpd
#   ./scripts/00_download.sh
#   Ctrl-b d          # detach; the download keeps going
#
set -euo pipefail

# Paths resolved from the repo root, not from the cwd.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${DATA_DIR:-$REPO_ROOT/data/raw}"
BASE="https://zenodo.org/api/records/5002584/files"

MAX_ATTEMPTS="${MAX_ATTEMPTS:-100}"

# name:md5:expected_bytes  (from the Zenodo API, 2026-08-04)
FILES=(
  "spotifydbdumpschemashare.sql:015c03a86fd2d2c92426db68e83a1862:5040"
  "spotifydbdumpshare.sql:3549b42e207a76ba5c20e650f1cd044e:10660363127"
)

mkdir -p "$DATA_DIR"

md5_of() {
  if command -v md5 >/dev/null 2>&1; then md5 -q "$1"
  else md5sum "$1" | cut -d' ' -f1
  fi
}

size_of() {
  if stat -f%z "$1" >/dev/null 2>&1; then stat -f%z "$1"   # BSD/macOS
  else stat -c%s "$1"                                       # GNU
  fi
}

for entry in "${FILES[@]}"; do
  IFS=':' read -r name want_md5 want_size <<< "$entry"
  dest="$DATA_DIR/$name"

  # Já completo?
  if [[ -f "$dest" && "$(size_of "$dest")" == "$want_size" ]]; then
    echo "==> $name já tem o tamanho esperado, verificando md5…"
    if [[ "$(md5_of "$dest")" == "$want_md5" ]]; then
      echo "    ok, íntegro. Pulando."
      continue
    fi
    echo "    md5 não confere — apagando e rebaixando do zero."
    rm -f "$dest"
  fi

  echo "==> Baixando $name ($(( want_size / 1000000 )) MB)"
  attempt=0
  while :; do
    have=0
    [[ -f "$dest" ]] && have="$(size_of "$dest")"

    if [[ "$have" -ge "$want_size" ]]; then
      break
    fi

    attempt=$((attempt + 1))
    if [[ "$attempt" -gt "$MAX_ATTEMPTS" ]]; then
      echo "ERRO: $MAX_ATTEMPTS tentativas sem completar $name" >&2
      exit 1
    fi

    pct=$(( have * 100 / want_size ))
    echo "--- tentativa $attempt — retomando em ${pct}% ($(( have / 1000000 )) MB)"

    # `|| true`: erro 18 (conexão cortada) é esperado; o laço retoma.
    # --speed-limit/-time abortam conexão travada em vez de pendurar.
    curl -L --fail --progress-bar \
         -C - \
         --retry 5 --retry-delay 5 --retry-connrefused \
         --speed-limit 10240 --speed-time 60 \
         -o "$dest" \
         "$BASE/$name/content" || true

    sleep 3
  done

  echo "==> Verificando md5 de $name"
  got="$(md5_of "$dest")"
  if [[ "$got" != "$want_md5" ]]; then
    echo "ERRO: md5 não confere para $name" >&2
    echo "  esperado: $want_md5" >&2
    echo "  obtido:   $got" >&2
    echo "Apague $dest e rode de novo." >&2
    exit 1
  fi
  echo "    ok."
done

echo
echo "Download completo em $DATA_DIR"
ls -lh "$DATA_DIR"
