#!/usr/bin/env python3
"""Build the album-level training dataset (§5.6 of the plan).

The only cut applied to the corpus: albums whose total duration is >= MIN_MINUTES
(default 20). Keeps EPs; drops singles and the remix releases that trail them.

Why duration and not track count: track count errs in both directions precisely in
the target genres. Godspeed You! Black Emperor's "Lift Your Skinny Fists" — 4 tracks,
87 minutes, 917 playlists — was ELIMINATED by `n_faixas >= 5`. In the other direction,
Martin Garrix's "Animals (The Remixes, Pt. 2)" has 5 tracks and 19 minutes, and passed.
Post-rock, drone, ambient and classical all build albums out of few long tracks.

Both rules keep roughly the same volume (831,827 vs 825,151 albums) but different sets:
duration trades short remix EPs for long albums with few tracks. The `duration` field
is 100% populated (zero nulls across 13.28M tracks), so no fallback rule is needed.

There is NO playlist-size cut — Stage 0 showed that cutting by size is in practice a
popularity filter: obscure albums lose 95-100% of their signal against ~65% for popular
ones. Personal-taste bias is left to weighting (1/log(len)) during training, not to
discarding here. That is why `playlist_len` ships as a column: it allows weighting or
filtering later without reprocessing anything.

Outputs in data/train/:
    pairs.parquet          playlist_ix, album_ix, artist_ix, n_tracks, playlist_len
    album_lookup.parquet   album_ix, album_id, name, artist_id, n_faixas, cluster_ix
    artist_lookup.parquet  artist_ix, artist_id, name
    playlist_lookup.parquet playlist_ix, playlist_id, name, playlist_len

`cluster_ix` is the deduplication mapping (§5.4) — shipped as a COLUMN, not applied.
Training with or without dedup becomes a query choice, and stays reversible.

Usage:
    python scripts/03_aggregate.py
    python scripts/03_aggregate.py --min-tracks 5
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

# Title normalization (§5.4), with the two fixes the coverage gate exposed:
#   - '&' -> 'and'  (corpus has "Liege And Lief", the seed had "Liege & Lief")
#   - Unicode-aware [\p{L}\p{N}], not [a-z0-9] (else CJK collapses to empty)
TITLE_NORM = r"""
    trim(regexp_replace(
      regexp_replace(
        regexp_replace(
          regexp_replace(lower(strip_accents({col})), '&', 'and', 'g'),
          '\s*[\(\[][^\)\]]*(deluxe|remaster|expanded|anniversary|edition|version|bonus|reissue|mono|stereo|explicit|clean|special|collector)[^\)\]]*[\)\]]', '', 'g'),
        '\s*[-–:]\s*(deluxe|remastered|remaster|expanded|anniversary|special)\b.*$', '', 'g'),
      '[^\p{L}\p{N} ]', '', 'g'))
"""


def norm(tpl: str, col: str) -> str:
    """Substitute the placeholder without str.format — the SQL contains \\p{L}."""
    return tpl.replace("{col}", col)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default="data/parquet")
    ap.add_argument("--out", default="data/train")
    ap.add_argument("--min-minutes", type=float, default=20.0,
                    help="duração total mínima do álbum, em minutos")
    ap.add_argument("--mem", default="6GB")
    args = ap.parse_args()

    p = Path(args.parquet)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    c = duckdb.connect()
    c.execute(f"SET memory_limit='{args.mem}'")
    c.execute("SET preserve_insertion_order=false")

    for t in ("album", "artist", "track", "playlist"):
        c.execute(f"CREATE VIEW {t} AS SELECT * FROM '{p}/{t}.parquet' WHERE id <> ''")
    c.execute(f"CREATE VIEW track_artist AS SELECT * FROM '{p}/track_artist1.parquet' "
              "WHERE track_id <> '' AND artist_id <> ''")
    c.execute(f"CREATE VIEW track_playlist AS SELECT * FROM '{p}/track_playlist1.parquet' "
              "WHERE track_id <> '' AND playlist_id <> ''")

    print("1/6  duração dos álbuns e aplicação do corte…")
    c.execute("""
        CREATE TABLE album_size AS
        SELECT album_id, count(*) AS n_faixas,
               sum(duration) / 60000.0 AS minutos
        FROM track WHERE album_id IS NOT NULL AND album_id <> ''
        GROUP BY 1
    """)
    print(c.sql(f"""
        SELECT count(*) AS albuns_total,
               sum(CASE WHEN minutos >= {args.min_minutes} THEN 1 ELSE 0 END) AS mantidos,
               round(100.0 * sum(CASE WHEN minutos >= {args.min_minutes} THEN 1 ELSE 0 END)
                     / count(*), 2) AS pct_mantido
        FROM album_size
    """))
    c.execute(f"""
        CREATE TABLE album_keep AS
        SELECT album_id, n_faixas, minutos
        FROM album_size WHERE minutos >= {args.min_minutes}
    """)

    print("2/6  artista principal por álbum…")
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

    print("3/6  chave de deduplicação (mapeamento, não aplicado)…")
    c.execute(f"""
        CREATE TABLE album_group AS
        SELECT a.id AS album_id, aa.artist_id,
               CASE
                 WHEN length(a.name) - length(replace(a.name, '?', '')) >= 2
                   THEN 'ID:' || a.id
                 WHEN {norm(TITLE_NORM, 'a.name')} = '' THEN 'ID:' || a.id
                 ELSE aa.artist_id || '|' || {norm(TITLE_NORM, 'a.name')}
               END AS group_key
        FROM album a
        JOIN album_keep k ON k.album_id = a.id
        JOIN album_artist aa ON aa.album_id = a.id
        WHERE a.name IS NOT NULL AND a.name <> ''
    """)

    print("4/6  pares playlist × álbum…")
    c.execute("""
        CREATE TABLE playlist_len AS
        SELECT playlist_id, count(*) AS len FROM track_playlist GROUP BY 1
    """)
    c.execute("""
        CREATE TABLE pa AS
        SELECT tp.playlist_id, t.album_id, count(DISTINCT t.id) AS n_tracks
        FROM track_playlist tp
        JOIN track t ON t.id = tp.track_id
        JOIN album_keep k ON k.album_id = t.album_id
        GROUP BY 1, 2
    """)
    print(c.sql("SELECT count(*) AS pares, count(DISTINCT album_id) AS albuns, "
                "count(DISTINCT playlist_id) AS playlists FROM pa"))

    print("5/6  remapeando IDs para índices contíguos…")
    # nn.Embedding needs 0..N-1 indices; the dump's IDs are base62 varchars.
    c.execute("""
        CREATE TABLE album_lookup AS
        SELECT (row_number() OVER (ORDER BY a.id)) - 1 AS album_ix,
               a.id AS album_id, al.name, aa.artist_id,
               k.n_faixas, round(k.minutos, 2) AS minutos,
               dense_rank() OVER (ORDER BY g.group_key) - 1 AS cluster_ix
        FROM (SELECT DISTINCT album_id AS id FROM pa) a
        JOIN album_keep k ON k.album_id = a.id
        JOIN album al ON al.id = a.id
        LEFT JOIN album_artist aa ON aa.album_id = a.id
        LEFT JOIN album_group g ON g.album_id = a.id
    """)
    c.execute("""
        CREATE TABLE artist_lookup AS
        SELECT (row_number() OVER (ORDER BY ar.id)) - 1 AS artist_ix,
               ar.id AS artist_id, ar.name
        FROM (SELECT DISTINCT artist_id AS id FROM album_lookup WHERE artist_id IS NOT NULL) x
        JOIN artist ar ON ar.id = x.id
    """)
    c.execute("""
        CREATE TABLE playlist_lookup AS
        SELECT (row_number() OVER (ORDER BY pl.id)) - 1 AS playlist_ix,
               pl.id AS playlist_id, pl.name, l.len AS playlist_len
        FROM (SELECT DISTINCT playlist_id AS id FROM pa) x
        JOIN playlist pl ON pl.id = x.id
        JOIN playlist_len l ON l.playlist_id = x.id
    """)

    print("6/6  gravando…")
    c.execute(f"""
        COPY (
            SELECT pll.playlist_ix, alk.album_ix, arl.artist_ix,
                   pa.n_tracks::SMALLINT AS n_tracks,
                   pll.playlist_len::INTEGER AS playlist_len
            FROM pa
            JOIN album_lookup alk    ON alk.album_id = pa.album_id
            JOIN playlist_lookup pll ON pll.playlist_id = pa.playlist_id
            LEFT JOIN artist_lookup arl ON arl.artist_id = alk.artist_id
        ) TO '{out}/pairs.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    for t in ("album_lookup", "artist_lookup", "playlist_lookup"):
        c.execute(f"COPY {t} TO '{out}/{t}.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")

    print("\nResumo:")
    print(c.sql(f"""
        SELECT (SELECT count(*) FROM '{out}/pairs.parquet')           AS pares,
               (SELECT count(*) FROM '{out}/album_lookup.parquet')    AS albuns,
               (SELECT count(*) FROM '{out}/artist_lookup.parquet')   AS artistas,
               (SELECT count(*) FROM '{out}/playlist_lookup.parquet') AS playlists,
               (SELECT count(DISTINCT cluster_ix) FROM '{out}/album_lookup.parquet')
                                                                      AS clusters_dedup
    """))
    for f in sorted(out.glob("*.parquet")):
        print(f"  {f.name:<24} {f.stat().st_size / 1e6:>8.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
