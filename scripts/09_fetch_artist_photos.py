"""Pré-popula o cache de fotos de artista (data/serve/artist_photos.sqlite).

Mesma estratégia de scripts/07_fetch_covers.py, trocando álbum por artista:
o oEmbed do Spotify não tem chamada em lote, então é uma requisição por
artista. Mesmo limite medido (16 conexões / ~15 req/s sem 429), então o
script também é retomável — o que já está no SQLite nunca é buscado de novo.

Artistas são visitados por popularidade decrescente (soma da pop dos álbuns,
ver scripts/08_artist_lookup.py), então parar no meio ainda deixa coberto o
que os usuários mais veem primeiro.

Requer data/serve/artist_index.parquet (scripts/08_artist_lookup.py).

Uso:
    .venv/bin/python scripts/09_fetch_artist_photos.py --limit 50000
    .venv/bin/python scripts/09_fetch_artist_photos.py              # catálogo inteiro
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.artist_photos import ArtistPhotoCache, _oembed, valid_artist_id  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CONCURRENCY = 16
COMMIT_EVERY = 500
BACKOFF_START = 2.0
BACKOFF_MAX = 60.0


def pending(index_path: Path, db: ArtistPhotoCache, limit: int | None) -> list[str]:
    df = (
        pl.read_parquet(index_path, columns=["artist_id", "pop"])
        .filter(pl.col("artist_id").is_not_null())
        .sort("pop", descending=True)
    )
    ids = [i for i in df["artist_id"].to_list() if valid_artist_id(i)]
    if limit:
        ids = ids[:limit]
    known = db.known_ids()
    return [i for i in ids if i not in known]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", type=Path, default=REPO / "data/serve/artist_index.parquet")
    ap.add_argument("--db", type=Path, default=REPO / "data/serve/artist_photos.sqlite")
    ap.add_argument("--limit", type=int, default=None, help="só os N mais populares")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    args = ap.parse_args()

    if not args.index.exists():
        print(f"faltando {args.index}\nRode: scripts/08_artist_lookup.py")
        return 1

    db = ArtistPhotoCache(args.db)
    todo = pending(args.index, db, args.limit)
    done_before = db.stats()["cached"]
    print(f"cache tem {done_before:,} artistas · faltam {len(todo):,}", flush=True)
    if not todo:
        db.close()
        return 0

    buf: list[tuple[str, str]] = []
    done = failed = 0
    backoff = 0.0
    nxt = 0
    t0 = time.perf_counter()

    async def worker() -> None:
        nonlocal done, failed, backoff, nxt
        while nxt < len(todo):
            artist_id = todo[nxt]
            nxt += 1

            if backoff:
                await asyncio.sleep(backoff)
            got = await asyncio.to_thread(_oembed, artist_id)

            if got is None:
                # Transitório (429 ou rede). Não cacheia: fica para a próxima rodada.
                failed += 1
                backoff = min(max(backoff * 2, BACKOFF_START), BACKOFF_MAX)
                continue

            backoff = 0.0
            buf.append((artist_id, got))
            done += 1
            if len(buf) >= COMMIT_EVERY:
                db.save_many(buf)
                buf.clear()
                rate = done / (time.perf_counter() - t0)
                eta = (len(todo) - done) / rate / 3600 if rate else 0
                print(
                    f"  {done:,}/{len(todo):,} · {rate:.1f} req/s · faltam ~{eta:.1f} h"
                    f" · {failed:,} adiados",
                    flush=True,
                )

    try:
        await asyncio.gather(*(worker() for _ in range(args.concurrency)))
    except KeyboardInterrupt:
        print("\ninterrompido — o progresso já gravado continua válido", flush=True)
    finally:
        if buf:
            db.save_many(buf)
        s = db.stats()
        print(
            f"\ncache: {s['cached']:,} artistas, {s['with_photo']:,} com foto"
            f" (+{s['cached'] - done_before:,} nesta rodada, {failed:,} adiados)"
        )
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
