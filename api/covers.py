"""Capas e títulos oficiais via oEmbed do Spotify, com cache em SQLite.

O endpoint https://open.spotify.com/oembed?url=... não pede credenciais e devolve
`title` e uma `thumbnail_url` de 300px. O nome do arquivo tem 40 hex: os 16
primeiros codificam o tamanho e os 24 restantes são o hash da capa, igual em
todas as resoluções:

    .../image/ab67616d00001e02db216ca805faf5fe35df4ee6
              |--- 300px ---||------ hash da capa ----|

Guardamos só o hash — o front monta a URL no tamanho que cada contexto precisa
(64/300/640/1500). O oEmbed não tem chamada em lote, então cada álbum custa uma
requisição; o cache existe para que isso aconteça uma única vez por álbum.
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

OEMBED = "https://open.spotify.com/oembed?url=https://open.spotify.com/album/{}"
TIMEOUT = 4.0
# Medido contra o oEmbed: 16 conexões rendem ~15 req/s sem nenhum 429; a 32 já
# vem 40% de 429 e a 64 o host recusa quase tudo. Acima de 16 a vazão piora.
MAX_CONCURRENCY = 16
SIZE_PREFIX_LEN = 16
HASH_RE = re.compile(r"^[0-9a-f]{24}$")
ALBUM_ID_RE = re.compile(r"^[0-9A-Za-z]{22}$")

# Álbum sem capa no Spotify: cacheado como hash vazio para não repetir a busca.
# Depois de um mês tentamos de novo — capas às vezes aparecem em reedições.
MISSING = ""
RETRY_MISSING_AFTER = 30 * 86_400

SCHEMA = """
CREATE TABLE IF NOT EXISTS covers (
    album_id   TEXT PRIMARY KEY,
    cover_hash TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    fetched_at INTEGER NOT NULL
)
"""


def valid_album_id(s: str) -> bool:
    """Base62 de 22 chars. Filtra lixo antes de virar requisição ao Spotify."""
    return bool(ALBUM_ID_RE.match(s))


def _oembed(album_id: str) -> tuple[str, str] | None:
    """(hash, título) para o álbum, ou None se a falha for transitória.

    Bloqueante — roda em thread. Devolver None significa "não cacheie": a
    diferença entre um álbum sem capa (cacheável) e a rede fora do ar importa.
    """
    try:
        with urllib.request.urlopen(OEMBED.format(album_id), timeout=TIMEOUT) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        # 404 = id inexistente, 400 = url recusada. Ambos são definitivos.
        # 429 (rate limit) cai no None: transitório, tenta de novo depois.
        return (MISSING, "") if e.code in (400, 404) else None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    title = str(payload.get("title") or "")
    thumb = str(payload.get("thumbnail_url") or "")
    if not thumb:
        return (MISSING, title)  # álbum existe, mas está sem capa

    cover = thumb.rsplit("/", 1)[-1][SIZE_PREFIX_LEN:]
    if not HASH_RE.match(cover):
        # A URL mudou de formato. Cachear aqui gravaria um "sem capa" errado em
        # cima do catálogo inteiro, então preferimos não cachear nada.
        log.warning("thumbnail_url em formato inesperado: %s", thumb)
        return None
    return (cover, title)


class CoverCache:
    """Resolve album_id → capa+título, consultando o Spotify só no cache miss."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")  # leitura concorrente com a escrita
        self.db.execute(SCHEMA)
        self.db.commit()
        self._lock = threading.Lock()
        self._sem = asyncio.Semaphore(MAX_CONCURRENCY)
        # Duas listas na tela podem pedir o mesmo álbum ao mesmo tempo; sem isso,
        # cada uma abriria a sua própria requisição para o mesmo id.
        self._inflight: dict[str, asyncio.Task[tuple[str, str] | None]] = {}

    def close(self) -> None:
        self.db.close()

    def _cached(self, ids: list[str]) -> dict[str, tuple[str, str]]:
        qs = ",".join("?" * len(ids))
        rows = self.db.execute(
            f"SELECT album_id, cover_hash, title, fetched_at FROM covers WHERE album_id IN ({qs})",
            ids,
        ).fetchall()
        now = time.time()
        return {
            aid: (cover, title)
            for aid, cover, title, ts in rows
            if cover or now - ts < RETRY_MISSING_AFTER
        }

    def _save(self, album_id: str, cover: str, title: str) -> None:
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO covers (album_id, cover_hash, title, fetched_at)"
                " VALUES (?, ?, ?, ?)",
                (album_id, cover, title, int(time.time())),
            )
            self.db.commit()

    def save_many(self, rows: list[tuple[str, str, str]]) -> None:
        """Grava (album_id, hash, título) em lote — um commit por álbum não escala
        para o backfill do catálogo inteiro."""
        now = int(time.time())
        with self._lock:
            self.db.executemany(
                "INSERT OR REPLACE INTO covers (album_id, cover_hash, title, fetched_at)"
                " VALUES (?, ?, ?, ?)",
                [(aid, cover, title, now) for aid, cover, title in rows],
            )
            self.db.commit()

    def known_ids(self) -> set[str]:
        """Ids já resolvidos — o backfill usa para retomar de onde parou."""
        return {r[0] for r in self.db.execute("SELECT album_id FROM covers")}

    def titles(self) -> dict[str, str]:
        """album_id → título oficial, para os que têm um."""
        return dict(self.db.execute("SELECT album_id, title FROM covers WHERE title != ''"))

    async def _fetch(self, album_id: str) -> tuple[str, str] | None:
        async with self._sem:
            found = await asyncio.to_thread(_oembed, album_id)
        if found is not None:
            self._save(album_id, *found)
        return found

    async def _fetch_once(self, album_id: str) -> tuple[str, str] | None:
        task = self._inflight.get(album_id)
        if task is None:
            task = asyncio.create_task(self._fetch(album_id))
            self._inflight[album_id] = task
            task.add_done_callback(lambda _, k=album_id: self._inflight.pop(k, None))
        # shield: se esta requisição for abortada (o usuário digitou de novo), a
        # busca continua para quem mais estiver esperando por ela.
        return await asyncio.shield(task)

    async def resolve(self, ids: list[str]) -> dict[str, dict[str, str]]:
        """Capas conhecidas para `ids`. Álbuns sem capa saem de fora do resultado."""
        found = self._cached(ids)
        missing = [i for i in dict.fromkeys(ids) if i not in found]
        if missing:
            fetched = await asyncio.gather(*(self._fetch_once(i) for i in missing))
            found |= {aid: got for aid, got in zip(missing, fetched) if got is not None}
        return {
            aid: {"cover": cover, "title": title} for aid, (cover, title) in found.items() if cover
        }

    def stats(self) -> dict[str, int]:
        (total, with_cover) = self.db.execute(
            "SELECT count(*), count(nullif(cover_hash, '')) FROM covers"
        ).fetchone()
        return {"cached": total, "with_cover": with_cover}
