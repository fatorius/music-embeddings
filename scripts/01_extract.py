#!/usr/bin/env python3
"""Extract the tables of the Zenodo 5002584 MySQL dump into Parquet.

The dump is `mysqldump` with extended INSERT — `INSERT INTO `t` VALUES (..),(..),..;` —
not Postgres COPY/TSV. Parsing therefore goes through a SQL literal tokenizer rather
than a TSV split.

Strategy: streaming read, a single sequential pass. The dump is never loaded into
memory; the peak is one batch of rows (~100 MB).

Usage:
    python scripts/01_extract.py                      # every table
    python scripts/01_extract.py --tables album track # a subset
    python scripts/01_extract.py --preset artist-lookup # see PRESETS below
    python scripts/01_extract.py --limit-mb 200       # smoke test on a prefix
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

# Schema taken from spotifydbdumpschemashare.sql.
# 's' = string, 'i' = int64, 'f' = float64
TABLES: dict[str, list[tuple[str, str]]] = {
    "album": [("id", "s"), ("name", "s"), ("uri", "s")],
    "artist": [("id", "s"), ("name", "s"), ("uri", "s")],
    "playlist": [
        ("id", "s"), ("name", "s"), ("followers", "i"),
        ("uri", "s"), ("total_tracks", "i"),
    ],
    "track": [
        ("id", "s"), ("name", "s"), ("duration", "i"), ("popularity", "f"),
        ("explicit", "s"), ("preview_url", "s"), ("uri", "s"), ("album_id", "s"),
    ],
    "track_artist1": [("track_id", "s"), ("artist_id", "s")],
    "track_playlist1": [("track_id", "s"), ("playlist_id", "s")],
}

_ARROW_TYPE = {"s": pa.string(), "i": pa.int64(), "f": pa.float64()}

# Wide tables (free text) use a smaller batch; pure-ID ones take more.
_BATCH_ROWS = {"track_playlist1": 4_000_000, "track_artist1": 4_000_000}
_DEFAULT_BATCH = 1_000_000

# "artist-lookup": the tables 03_aggregate.py / 08_artist_lookup.py need to
# rebuild artist_lookup.parquet with artist_ix aligned to the trained
# embeddings. Not just `artist`: artist_ix is a row_number() over the artists
# that survive the album->pa (playlist x album) join, so track and
# track_artist1 are required to know which albums qualify, and
# track_playlist1 is required to know which albums appear in any playlist at
# all. `playlist` itself is never touched by that computation.
PRESETS: dict[str, list[str]] = {
    "artist-lookup": ["album", "artist", "track", "track_artist1", "track_playlist1"],
}

_INSERT_RE = re.compile(r"^INSERT INTO `([^`]+)` VALUES ", re.ASCII)
_SPECIAL_RE = re.compile(r"[\\']")

# Escapes emitted by mysqldump. Any other `\X` becomes X itself.
_UNESCAPE = {
    "0": "\0", "b": "\b", "n": "\n", "r": "\r",
    "t": "\t", "Z": "\x1a", "\\": "\\", "'": "'", '"': '"',
}


def parse_values(payload: str):
    """Walk a VALUES payload, yielding each tuple as a list of str|None.

    Jumps via regex instead of scanning character by character: escape-free strings
    (the common case — base62 IDs) resolve in one search plus one slice.
    """
    i, n = 0, len(payload)
    while True:
        i = payload.find("(", i)
        if i == -1:
            return
        i += 1
        row: list[str | None] = []
        while i < n:
            c = payload[i]
            if c == "'":
                i += 1
                parts: list[str] = []
                while True:
                    m = _SPECIAL_RE.search(payload, i)
                    if m is None:  # truncated dump
                        return
                    j = m.start()
                    if payload[j] == "'":
                        parts.append(payload[i:j])
                        i = j + 1
                        break
                    # backslash: consume the escape pair
                    parts.append(payload[i:j])
                    esc = payload[j + 1]
                    parts.append(_UNESCAPE.get(esc, esc))
                    i = j + 2
                row.append("".join(parts))
            else:
                # unquoted literal: NULL or number
                j = i
                while j < n and payload[j] not in ",)":
                    j += 1
                tok = payload[i:j].strip()
                row.append(None if tok == "NULL" else tok)
                i = j

            if i >= n:
                return
            if payload[i] == ",":
                i += 1
                continue
            if payload[i] == ")":
                i += 1
                yield row
                break


def _cast(col_values: list[str | None], kind: str):
    if kind == "s":
        return col_values
    conv = int if kind == "i" else float
    out: list[object] = []
    for v in col_values:
        if v is None or v == "":
            out.append(None)
        else:
            try:
                out.append(conv(v))
            except ValueError:
                out.append(None)
    return out


class TableWriter:
    """Buffer rows and flush to Parquet once per batch."""

    def __init__(self, name: str, cols: list[tuple[str, str]], out_dir: Path):
        self.name = name
        self.cols = cols
        self.schema = pa.schema([(c, _ARROW_TYPE[k]) for c, k in cols])
        self.path = out_dir / f"{name}.parquet"
        self.writer = pq.ParquetWriter(self.path, self.schema, compression="zstd")
        self.buf: list[list[str | None]] = [[] for _ in cols]
        self.buffered = 0
        self.total = 0
        self.batch_size = _BATCH_ROWS.get(name, _DEFAULT_BATCH)
        self.bad_arity = 0

    def add(self, row: list[str | None]) -> None:
        if len(row) != len(self.cols):
            self.bad_arity += 1
            return
        for k, v in enumerate(row):
            self.buf[k].append(v)
        self.buffered += 1
        if self.buffered >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.buffered:
            return
        arrays = [
            pa.array(_cast(self.buf[k], kind), type=_ARROW_TYPE[kind])
            for k, (_, kind) in enumerate(self.cols)
        ]
        self.writer.write_table(pa.Table.from_arrays(arrays, schema=self.schema))
        self.total += self.buffered
        self.buf = [[] for _ in self.cols]
        self.buffered = 0

    def close(self) -> None:
        self.flush()
        self.writer.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="data/raw/spotifydbdumpshare.sql")
    ap.add_argument("--out", default="data/parquet")
    ap.add_argument("--tables", nargs="*", default=None)
    ap.add_argument("--preset", choices=sorted(PRESETS),
                    help="atalho para um subconjunto de --tables; ver PRESETS no topo")
    ap.add_argument("--limit-mb", type=float, default=None,
                    help="process only the first N MB (smoke test)")
    args = ap.parse_args()

    if args.tables and args.preset:
        print("use --tables OU --preset, não os dois", file=sys.stderr)
        return 1
    args.tables = args.tables or (PRESETS[args.preset] if args.preset else sorted(TABLES))

    dump = Path(args.dump)
    if not dump.exists():
        print(f"dump não encontrado: {dump}\nRode scripts/00_download.sh antes.",
              file=sys.stderr)
        return 1

    unknown = set(args.tables) - set(TABLES)
    if unknown:
        print(f"tabelas desconhecidas: {sorted(unknown)}", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    total_bytes = dump.stat().st_size
    limit = int(args.limit_mb * 1024 * 1024) if args.limit_mb else total_bytes

    writers = {t: TableWriter(t, TABLES[t], out_dir) for t in args.tables}
    wanted = set(args.tables)

    read = 0
    started = time.time()
    last_report = 0.0
    buf = b""
    CHUNK = 64 * 1024 * 1024

    print(f"dump: {dump}  ({total_bytes / 1e9:.2f} GB)")
    print(f"tabelas: {', '.join(args.tables)}")
    if args.limit_mb:
        print(f"LIMITE: primeiros {args.limit_mb} MB (smoke test)")
    print()

    with dump.open("rb") as fh:
        while read < limit:
            chunk = fh.read(min(CHUNK, limit - read))
            if not chunk:
                break
            read += len(chunk)
            buf += chunk

            # mysqldump escapes newlines inside strings, so b";\n" only ever
            # marks a statement end — the split is safe.
            parts = buf.split(b";\n")
            buf = parts.pop()

            for raw in parts:
                stmt = raw.lstrip(b"\r\n")
                if not stmt.startswith(b"INSERT INTO "):
                    continue
                text = stmt.decode("utf-8", errors="replace")
                m = _INSERT_RE.match(text)
                if not m:
                    continue
                table = m.group(1)
                if table not in wanted:
                    continue
                w = writers[table]
                for row in parse_values(text[m.end():]):
                    w.add(row)

            now = time.time()
            if now - last_report > 5:
                pct = 100 * read / limit
                rate = read / 1e6 / max(now - started, 1e-9)
                done = sum(x.total + x.buffered for x in writers.values())
                print(f"  {pct:5.1f}%  {read/1e9:5.2f} GB  "
                      f"{rate:6.1f} MB/s  {done:,} linhas", flush=True)
                last_report = now

    print("\nfechando writers…")
    for w in writers.values():
        w.close()

    elapsed = time.time() - started
    print(f"\nconcluído em {elapsed/60:.1f} min\n")
    print(f"{'tabela':<18} {'linhas':>14} {'arquivo':>12}")
    print("-" * 46)
    for name in args.tables:
        w = writers[name]
        size = w.path.stat().st_size
        print(f"{name:<18} {w.total:>14,} {size/1e6:>10.1f} MB")
        if w.bad_arity:
            print(f"  {'':<16} AVISO: {w.bad_arity:,} linhas com aridade errada")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
