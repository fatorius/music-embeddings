"""Fotos de artista via oEmbed do Spotify, com cache em SQLite.

Mesmo endpoint e mesmo formato de hash de api/covers.py (ver o docstring lá),
trocando /album/{id} por /artist/{id} na URL. Confirmado manualmente que o
oEmbed também responde para artistas e devolve uma thumbnail_url com o
mesmo padrão de 40 hex (16 de tamanho + 24 de hash).

Sem título para corrigir aqui: o problema do latin-1 que api/covers.py
resolve é específico do dump de álbuns; nomes de artista não passam por essa
segunda fase.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

OEMBED = "https://open.spotify.com/oembed?url=https://open.spotify.com/artist/{}"
TIMEOUT = 4.0
# Mesmo limite medido para álbuns (api/covers.py) — é o mesmo host/endpoint.
MAX_CONCURRENCY = 16
SIZE_PREFIX_LEN = 16
HASH_RE = re.compile(r"^[0-9a-f]{24}$")
ARTIST_ID_RE = re.compile(r"^[0-9A-Za-z]{22}$")

MISSING = ""
RETRY_MISSING_AFTER = 30 * 86_400

SCHEMA = """
CREATE TABLE IF NOT EXISTS artist_photos (
    artist_id  TEXT PRIMARY KEY,
    photo_hash TEXT NOT NULL,
    fetched_at INTEGER NOT NULL
)
"""


def valid_artist_id(s: str) -> bool:
    """Base62 de 22 chars. Filtra lixo antes de virar requisição ao Spotify."""
    return bool(ARTIST_ID_RE.match(s))


def _oembed(artist_id: str) -> str | None:
    """Hash da foto do artista, ou None se a falha for transitória.

    Bloqueante — roda em thread. None significa "não cacheie": a diferença
    entre um artista sem foto (cacheável) e a rede fora do ar importa.
    """
    try:
        with urllib.request.urlopen(OEMBED.format(artist_id), timeout=TIMEOUT) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        # 404 = id inexistente, 400 = url recusada. Ambos definitivos.
        # 429 (rate limit) cai no None: transitório, tenta de novo depois.
        return MISSING if e.code in (400, 404) else None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    thumb = str(payload.get("thumbnail_url") or "")
    if not thumb:
        return MISSING  # artista existe, mas está sem foto

    photo = thumb.rsplit("/", 1)[-1][SIZE_PREFIX_LEN:]
    if not HASH_RE.match(photo):
        log.warning("thumbnail_url em formato inesperado: %s", thumb)
        return None
    return photo


class ArtistPhotoCache:
    """Resolve artist_id → foto, consultando o Spotify só no cache miss."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(SCHEMA)
        self.db.commit()
        self._lock = threading.Lock()
        self._sem = asyncio.Semaphore(MAX_CONCURRENCY)
        self._inflight: dict[str, asyncio.Task[str | None]] = {}

    def close(self) -> None:
        self.db.close()

    def _cached(self, ids: list[str]) -> dict[str, str]:
        qs = ",".join("?" * len(ids))
        rows = self.db.execute(
            f"SELECT artist_id, photo_hash, fetched_at FROM artist_photos WHERE artist_id IN ({qs})",
            ids,
        ).fetchall()
        now = time.time()
        return {
            aid: photo
            for aid, photo, ts in rows
            if photo or now - ts < RETRY_MISSING_AFTER
        }

    def save_many(self, rows: list[tuple[str, str]]) -> None:
        """Grava (artist_id, hash) em lote — um commit por artista não escala."""
        now = int(time.time())
        with self._lock:
            self.db.executemany(
                "INSERT OR REPLACE INTO artist_photos (artist_id, photo_hash, fetched_at)"
                " VALUES (?, ?, ?)",
                [(aid, photo, now) for aid, photo in rows],
            )
            self.db.commit()

    def known_ids(self) -> set[str]:
        """Ids já resolvidos — o backfill usa para retomar de onde parou."""
        return {r[0] for r in self.db.execute("SELECT artist_id FROM artist_photos")}

    async def _fetch(self, artist_id: str) -> str | None:
        async with self._sem:
            found = await asyncio.to_thread(_oembed, artist_id)
        if found is not None:
            self.save_many([(artist_id, found)])
        return found

    async def _fetch_once(self, artist_id: str) -> str | None:
        task = self._inflight.get(artist_id)
        if task is None:
            task = asyncio.create_task(self._fetch(artist_id))
            self._inflight[artist_id] = task
            task.add_done_callback(lambda _, k=artist_id: self._inflight.pop(k, None))
        return await asyncio.shield(task)

    async def resolve(self, ids: list[str]) -> dict[str, str]:
        """Fotos conhecidas para `ids`. Artistas sem foto saem de fora do resultado."""
        found = self._cached(ids)
        missing = [i for i in dict.fromkeys(ids) if i not in found]
        if missing:
            fetched = await asyncio.gather(*(self._fetch_once(i) for i in missing))
            found |= {aid: got for aid, got in zip(missing, fetched) if got is not None}
        return {aid: photo for aid, photo in found.items() if photo}

    def stats(self) -> dict[str, int]:
        (total, with_photo) = self.db.execute(
            "SELECT count(*), count(nullif(photo_hash, '')) FROM artist_photos"
        ).fetchone()
        return {"cached": total, "with_photo": with_photo}
