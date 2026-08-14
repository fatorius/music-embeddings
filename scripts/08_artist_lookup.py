#!/usr/bin/env python3
"""Rebuild artist_lookup.parquet and data/serve/artist_index.parquet.

data/train/ was wiped after training (raw dumps and pairs.parquet are huge and
disposable once the embeddings are promoted), so artist_id — the Spotify id
needed to fetch artist photos — is gone. This script regenerates ONLY the
artist_ix -> artist_id -> name mapping, not the full training set.

It matters that artist_ix lines up exactly with the artist_ix already baked
into data/embeddings/runs/artist_final/artist_emb.npy and into the artist_ix
column of data/serve/album_index.parquet. Both come from 03_aggregate.py,
where artist_ix = row_number() OVER (ORDER BY artist_id) restricted to
artists that survive the album -> pa (playlist x album) join under
--min-minutes 20.0 (the default, unchanged). Given the same source dump
(checksummed by 00_download.sh) and the same threshold, that computation is
deterministic, so replaying steps 1/2/4/5 of 03_aggregate.py here reproduces
the identical artist_ix assignment without re-deriving pairs.parquet (5.74B
rows) or playlist_lookup.parquet, neither of which this needs.

Popularity is NOT recomputed from pairs.parquet (gone). It's summed straight
from data/serve/album_index.parquet's existing `pop` column, grouped by
artist_ix — that file survived the cleanup and already encodes it per album.

Requires the tables extracted with:
    scripts/01_extract.py --preset artist-lookup

Usage:
    python scripts/08_artist_lookup.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parquet", type=Path, default=REPO / "data/parquet")
    ap.add_argument("--train-out", type=Path, default=REPO / "data/train")
    ap.add_argument("--serve-out", type=Path, default=REPO / "data/serve")
    ap.add_argument("--min-minutes", type=float, default=20.0,
                     help="tem que ser igual ao valor usado em 03_aggregate.py")
    ap.add_argument("--mem", default="6GB")
    args = ap.parse_args()

    args.train_out.mkdir(parents=True, exist_ok=True)
    args.serve_out.mkdir(parents=True, exist_ok=True)
    album_index = args.serve_out / "album_index.parquet"
    if not album_index.exists():
        print(f"faltando {album_index} (precisa da pop por álbum)")
        return 1

    p = args.parquet
    for t in ("album", "artist", "track", "track_artist1", "track_playlist1"):
        f = p / f"{t}.parquet"
        if not f.exists():
            print(f"faltando {f}\nRode: scripts/01_extract.py --preset artist-lookup")
            return 1

    c = duckdb.connect()
    c.execute(f"SET memory_limit='{args.mem}'")
    c.execute("SET preserve_insertion_order=false")

    for t in ("album", "artist", "track"):
        c.execute(f"CREATE VIEW {t} AS SELECT * FROM '{p / f'{t}.parquet'}' WHERE id <> ''")
    c.execute(f"CREATE VIEW track_artist AS SELECT * FROM '{p / 'track_artist1.parquet'}' "
              "WHERE track_id <> '' AND artist_id <> ''")
    c.execute(f"CREATE VIEW track_playlist AS SELECT * FROM '{p / 'track_playlist1.parquet'}' "
              "WHERE track_id <> '' AND playlist_id <> ''")

    print("1/4  duração dos álbuns e aplicação do corte (mesmo filtro do treino)…")
    c.execute("""
        CREATE TABLE album_size AS
        SELECT album_id, sum(duration) / 60000.0 AS minutos
        FROM track WHERE album_id IS NOT NULL AND album_id <> ''
        GROUP BY 1
    """)
    c.execute(f"""
        CREATE TABLE album_keep AS
        SELECT album_id FROM album_size WHERE minutos >= {args.min_minutes}
    """)

    print("2/4  artista principal por álbum…")
    c.execute("""
        CREATE TABLE album_artist AS
        WITH pair AS (
            SELECT t.album_id, ta.artist_id, count(*) AS n
            FROM track t
            JOIN album_keep k ON k.album_id = t.album_id
            JOIN track_artist ta ON ta.track_id = t.id
            GROUP BY 1, 2
        ), ranked AS (
            SELECT album_id, artist_id,
                   row_number() OVER (PARTITION BY album_id ORDER BY n DESC, artist_id) AS rk
            FROM pair
        )
        SELECT album_id, artist_id FROM ranked WHERE rk = 1
    """)

    print("3/4  álbuns que aparecem em pelo menos uma playlist…")
    c.execute("""
        CREATE TABLE pa_albums AS
        SELECT DISTINCT t.album_id
        FROM track_playlist tp
        JOIN track t ON t.id = tp.track_id
        JOIN album_keep k ON k.album_id = t.album_id
    """)
    c.execute("""
        CREATE TABLE album_lookup AS
        SELECT (row_number() OVER (ORDER BY a.album_id)) - 1 AS album_ix,
               a.album_id, aa.artist_id
        FROM pa_albums a
        LEFT JOIN album_artist aa ON aa.album_id = a.album_id
    """)

    print("4/4  artist_ix determinístico + escrita…")
    c.execute("""
        CREATE TABLE artist_lookup AS
        SELECT (row_number() OVER (ORDER BY ar.id)) - 1 AS artist_ix,
               ar.id AS artist_id, ar.name
        FROM (SELECT DISTINCT artist_id AS id FROM album_lookup WHERE artist_id IS NOT NULL) x
        JOIN artist ar ON ar.id = x.id
    """)
    train_dest = args.train_out / "artist_lookup.parquet"
    c.execute(f"COPY artist_lookup TO '{train_dest}' (FORMAT PARQUET, COMPRESSION ZSTD)")

    serve_dest = args.serve_out / "artist_index.parquet"
    c.execute(f"""
        COPY (
            SELECT al.artist_ix, al.artist_id, al.name AS artist,
                   coalesce(p.pop, 0)::INTEGER AS pop
            FROM artist_lookup al
            LEFT JOIN (
                SELECT artist_ix, sum(pop) AS pop
                FROM read_parquet('{album_index}')
                WHERE artist_ix IS NOT NULL
                GROUP BY 1
            ) p ON p.artist_ix = al.artist_ix
            ORDER BY al.artist_ix
        ) TO '{serve_dest}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    n = c.execute(f"SELECT count(*) FROM read_parquet('{serve_dest}')").fetchone()[0]
    print(f"\n{train_dest}\n{serve_dest}\n{n:,} artistas")
    print("confira contra data/embeddings/runs/artist_final/artist_emb.npy: "
          "as linhas têm que bater (248,709 na promoção original).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
