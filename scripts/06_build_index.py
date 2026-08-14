"""Build the serving index: a single parquet holding everything the API needs.

Joins album_lookup + artist_lookup + popularity (number of distinct playlists, derived
from pairs.parquet) into data/serve/album_index.parquet.
"""

import argparse
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parent.parent
TRAIN = REPO / "data" / "train"
OUT = REPO / "data" / "serve"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-dir", type=Path, default=TRAIN)
    ap.add_argument("--out-dir", type=Path, default=OUT)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    dest = args.out_dir / "album_index.parquet"

    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
            SELECT
                al.album_ix,
                al.album_id,
                al.name          AS album,
                ar.artist_ix,
                ar.name          AS artist,
                al.n_faixas,
                al.minutos,
                coalesce(p.pop, 0)::INTEGER AS pop
            FROM read_parquet('{args.train_dir / "album_lookup.parquet"}') al
            LEFT JOIN read_parquet('{args.train_dir / "artist_lookup.parquet"}') ar
                   ON ar.artist_id = al.artist_id
            LEFT JOIN (
                SELECT album_ix, count(DISTINCT playlist_ix) AS pop
                FROM read_parquet('{args.train_dir / "pairs.parquet"}')
                GROUP BY 1
            ) p ON p.album_ix = al.album_ix
            ORDER BY al.album_ix
        ) TO '{dest}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    n, npop, nart = con.execute(
        f"""SELECT count(*), count(*) FILTER (pop > 0), count(*) FILTER (artist_ix IS NOT NULL)
            FROM read_parquet('{dest}')"""
    ).fetchone()
    print(f"{dest}: {n:,} álbuns  |  com popularidade: {npop:,}  |  com artist_ix: {nart:,}")


if __name__ == "__main__":
    main()
