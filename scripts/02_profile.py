#!/usr/bin/env python3
"""Profile the extracted corpus (Stage 0, §5.3 of the plan).

Answers, without a single API call:
  1. real cardinalities and referential integrity
  2. playlist size distribution      (decides whether to normalize by size)
  3. album identity fragmentation    (decides the dedup strategy, §5.4)
  4. tracks per album and albums per playlist
  5. long tail: how many albums carry enough signal to train on

Usage:
    python scripts/02_profile.py
    python scripts/02_profile.py --parquet data/parquet --mem 6GB
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default="data/parquet")
    ap.add_argument("--mem", default="6GB")
    args = ap.parse_args()

    p = Path(args.parquet)
    for t in ("album", "artist", "track", "playlist", "track_artist1", "track_playlist1"):
        if not (p / f"{t}.parquet").exists():
            print(f"faltando: {p / f'{t}.parquet'}\nRode scripts/01_extract.py antes.")
            return 1

    c = duckdb.connect()
    c.execute(f"SET memory_limit='{args.mem}'")
    c.execute("SET preserve_insertion_order=false")

    # Named views, already filtering out the dump's sentinel row (empty id).
    for t in ("album", "artist", "track", "playlist"):
        c.execute(f"CREATE VIEW {t} AS SELECT * FROM '{p}/{t}.parquet' WHERE id <> ''")
    c.execute(f"CREATE VIEW track_artist AS SELECT * FROM '{p}/track_artist1.parquet' "
              "WHERE track_id <> '' AND artist_id <> ''")
    c.execute(f"CREATE VIEW track_playlist AS SELECT * FROM '{p}/track_playlist1.parquet' "
              "WHERE track_id <> '' AND playlist_id <> ''")

    rule("1. Cardinalidades")
    print(c.sql("""
        SELECT (SELECT count(*) FROM album)          AS albuns,
               (SELECT count(*) FROM artist)         AS artistas,
               (SELECT count(*) FROM track)          AS faixas,
               (SELECT count(*) FROM playlist)       AS playlists,
               (SELECT count(*) FROM track_playlist) AS ocorrencias
    """))

    rule("2. Tamanho de playlist (em faixas) — define normalização por tamanho")
    print(c.sql("""
        WITH n AS (SELECT playlist_id, count(*) AS len FROM track_playlist GROUP BY 1)
        SELECT count(*) AS playlists, round(avg(len), 1) AS media,
               quantile_cont(len, 0.50) AS p50, quantile_cont(len, 0.90) AS p90,
               quantile_cont(len, 0.99) AS p99, quantile_cont(len, 0.999) AS p999,
               max(len) AS maximo
        FROM n
    """))
    print("Concentração do sinal — quanto do corpus vem das playlists gigantes:")
    print(c.sql("""
        WITH n AS (SELECT playlist_id, count(*) AS len FROM track_playlist GROUP BY 1),
        b AS (SELECT CASE WHEN len <=   50 THEN 'a. <=50'
                          WHEN len <=  250 THEN 'b. 51-250   (limite do MPD)'
                          WHEN len <= 1000 THEN 'c. 251-1000'
                          WHEN len <= 5000 THEN 'd. 1001-5000'
                          ELSE                  'e. >5000    (biblioteca)'
                     END AS faixa, len
             FROM n)
        SELECT faixa, count(*) AS playlists, sum(len) AS ocorrencias,
               round(100.0 * sum(len) / sum(sum(len)) OVER (), 2) AS pct_ocorrencias
        FROM b GROUP BY 1 ORDER BY 1
    """))

    rule("3. Fragmentação de identidade de álbum (§5.4)")
    # An album's main artist = the most frequent artist across its tracks.
    c.execute("""
        CREATE TABLE album_artist AS
        WITH pair AS (
            SELECT t.album_id, ta.artist_id, count(*) AS n
            FROM track t JOIN track_artist ta ON ta.track_id = t.id
            WHERE t.album_id IS NOT NULL AND t.album_id <> ''
            GROUP BY 1, 2
        ), ranked AS (
            SELECT album_id, artist_id,
                   row_number() OVER (PARTITION BY album_id ORDER BY n DESC, artist_id) AS rk
            FROM pair
        )
        SELECT album_id, artist_id FROM ranked WHERE rk = 1
    """)

    # Normalized title: strips edition suffixes, punctuation and accents.
    #
    # The final character class is Unicode-aware (\p{L}\p{N}), NOT [a-z0-9]. Under
    # [a-z0-9], CJK/Cyrillic/etc titles collapse to the empty string and an artist's
    # whole discography merges into a single group — a false merge (e.g. Andy Lau,
    # 88 albums).
    #
    # Fallback: if normalization empties the title anyway, fall back to the original
    # lowercased title, so nothing is ever grouped under an empty key.
    c.execute(r"""
        CREATE TABLE album_norm AS
        WITH n AS (
            SELECT a.id AS album_id, aa.artist_id, ar.name AS artist_name, a.name AS title,
                   trim(regexp_replace(
                     regexp_replace(
                       regexp_replace(lower(strip_accents(a.name)),
                         '\s*[\(\[][^\)\]]*(deluxe|remaster|expanded|anniversary|edition|version|bonus|reissue|mono|stereo|explicit|clean|special|collector)[^\)\]]*[\)\]]', '', 'g'),
                       '\s*[-–:]\s*(deluxe|remastered|remaster|expanded|anniversary|special)\b.*$', '', 'g'),
                     '[^\p{L}\p{N} ]', '', 'g')) AS t_norm
            FROM album a
            JOIN album_artist aa ON aa.album_id = a.id
            JOIN artist ar ON ar.id = aa.artist_id
            WHERE a.name IS NOT NULL AND a.name <> ''
        )
        SELECT album_id, artist_id, artist_name, title,
               CASE
                 -- Mojibake (§5.3): título cujo conteúdo virou '?' na origem não
                 -- carrega identidade. Agrupar por ele funde álbuns distintos
                 -- (Jay Chou '???' = 27 álbuns diferentes). Chave única = não agrupa.
                 WHEN length(title) - length(replace(title, '?', '')) >= 2
                   THEN 'ID:' || album_id
                 -- Normalização esvaziou o título: idem, não há chave confiável.
                 WHEN t_norm = '' THEN 'ID:' || album_id
                 ELSE t_norm
               END AS title_norm
        FROM n
    """)

    print(c.sql("""
        WITH g AS (SELECT artist_id, title_norm, count(*) AS variantes
                   FROM album_norm GROUP BY 1, 2)
        SELECT count(*) AS grupos, sum(variantes) AS albuns,
               round(avg(variantes), 3) AS media_variantes,
               quantile_cont(variantes, 0.50) AS p50,
               quantile_cont(variantes, 0.99) AS p99,
               max(variantes) AS maximo,
               sum(CASE WHEN variantes > 1 THEN variantes - 1 ELSE 0 END) AS albuns_redundantes
        FROM g
    """))
    print("Maiores grupos (inspeção qualitativa — checar se são merges legítimos):")
    print(c.sql("""
        WITH g AS (SELECT artist_id, title_norm, count(*) AS variantes
                   FROM album_norm GROUP BY 1, 2)
        SELECT any_value(n.artist_name) AS artista, g.title_norm, g.variantes
        FROM g JOIN album_norm n USING (artist_id, title_norm)
        GROUP BY g.artist_id, g.title_norm, g.variantes
        ORDER BY g.variantes DESC LIMIT 15
    """))

    rule("4. Álbuns por playlist e faixas por álbum")
    c.execute("""
        CREATE TABLE playlist_album AS
        SELECT tp.playlist_id, t.album_id, count(DISTINCT t.id) AS n_tracks
        FROM track_playlist tp
        JOIN track t ON t.id = tp.track_id
        WHERE t.album_id IS NOT NULL AND t.album_id <> ''
        GROUP BY 1, 2
    """)
    print(c.sql("""
        SELECT count(*) AS pares_playlist_album,
               count(DISTINCT album_id) AS albuns_com_sinal,
               round(avg(n_tracks), 2) AS media_faixas_por_par,
               quantile_cont(n_tracks, 0.50) AS p50,
               quantile_cont(n_tracks, 0.90) AS p90,
               max(n_tracks) AS maximo
        FROM playlist_album
    """))
    print("Distribuição de n_tracks (o sinal de confiança/homogeneidade, §6.1):")
    print(c.sql("""
        SELECT CASE WHEN n_tracks = 1 THEN '1 faixa'
                    WHEN n_tracks = 2 THEN '2 faixas'
                    WHEN n_tracks <= 5 THEN '3-5'
                    WHEN n_tracks <= 10 THEN '6-10'
                    ELSE '>10' END AS bucket,
               count(*) AS pares,
               round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct
        FROM playlist_album GROUP BY 1 ORDER BY min(n_tracks)
    """))

    rule("5. Cauda longa — quantos álbuns têm sinal suficiente")
    print(c.sql("""
        WITH f AS (SELECT album_id, count(*) AS playlists FROM playlist_album GROUP BY 1)
        SELECT sum(CASE WHEN playlists >=  1 THEN 1 ELSE 0 END) AS ge1,
               sum(CASE WHEN playlists >=  3 THEN 1 ELSE 0 END) AS ge3,
               sum(CASE WHEN playlists >=  5 THEN 1 ELSE 0 END) AS ge5,
               sum(CASE WHEN playlists >= 10 THEN 1 ELSE 0 END) AS ge10,
               sum(CASE WHEN playlists >= 20 THEN 1 ELSE 0 END) AS ge20,
               sum(CASE WHEN playlists >= 50 THEN 1 ELSE 0 END) AS ge50
        FROM f
    """))

    print("\nTabelas materializadas nesta sessão: album_artist, album_norm, playlist_album")
    print("(em memória — o agregado durável sai em scripts/03_aggregate.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
