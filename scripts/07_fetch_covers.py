"""Pré-popula o cache de capas (data/serve/covers.sqlite) e conserta os nomes de álbum.

O dump de origem foi gravado em latin-1, então tudo que estava fora desse conjunto
virou "?": "宇宙 日本 世田谷", do Fishmans, está no índice como "?? ?? ???". Isso
atinge 1,9% do catálogo e quebra o autocomplete, que casa por substring.

O título que o oEmbed devolve vem em UTF-8 correto, então a segunda fase deste
script reescreve a coluna `album` do índice com ele. A troca só acontece quando o
nome local é *exatamente* o oficial passado por latin-1 — assim "Morning Glory?"
e "¿Dónde Jugarán Los Niños?", que têm "?" ou acento legítimos, ficam intactos, e
nomes que apenas divergem de edição ("OK Computer" vs "OK Computer (Collector's
Edition)") não são sobrescritos.

Reaplicar é seguro e idempotente — e *necessário* depois de rodar 06_build_index.py,
que regenera o índice a partir do dump e traz os "?" de volta.

O oEmbed do Spotify não tem chamada em lote, então é uma requisição por álbum.
Medido neste host: 16 conexões sustentam ~15 req/s sem nenhum 429; acima disso o
Spotify começa a recusar e a vazão *cai*. Para os 831 mil álbuns do índice isso dá
~15 h, então o script é retomável — pode rodar em pedaços, e o que já está no
SQLite nunca é buscado de novo.

Os álbuns são visitados por popularidade decrescente, então parar no meio ainda
deixa coberto o que os usuários mais pesquisam:

    .venv/bin/python scripts/07_fetch_covers.py --limit 100000
    .venv/bin/python scripts/07_fetch_covers.py              # catálogo inteiro
    .venv/bin/python scripts/07_fetch_covers.py --apply-only # só reescreve os nomes
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.covers import CoverCache, _oembed, valid_album_id  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
# Acima de 16 o Spotify devolve 429 e a vazão cai — ver docstring.
DEFAULT_CONCURRENCY = 16
COMMIT_EVERY = 500
# Recuo quando o rate limit aparece: sem isso o script gira em falso gastando
# requisições que já nascem recusadas.
BACKOFF_START = 2.0
BACKOFF_MAX = 60.0


def mangle(s: str) -> str:
    """O nome como o dump o teria gravado: latin-1, o resto vira '?'."""
    return s.encode("latin-1", "replace").decode("latin-1")


def apply_titles(index_path: Path, db: CoverCache) -> None:
    """Reescreve a coluna `album` do índice com o título oficial, onde o local
    for comprovadamente a versão estropiada dele.

    Mexe só nessa coluna: a ordem das linhas e o album_ix indexam os embeddings,
    e qualquer reordenação aqui desalinharia as recomendações.
    """
    df = pl.read_parquet(index_path)
    titles = db.titles()

    album, fixed = df["album"].to_list(), 0
    for i, (aid, name) in enumerate(zip(df["album_id"].to_list(), album)):
        official = titles.get(aid)
        if official and official != name and mangle(official) == name:
            album[i] = official
            fixed += 1

    # Nomes ainda com "?" que não deram para consertar: ou o álbum não está no
    # cache, ou o oficial diverge do local por mais que o latin-1.
    broken = sum(1 for n in album if "?" in n)

    if not fixed:
        print(f"nenhum nome a corrigir ({broken:,} ainda com '?')")
        return

    # Escrita atômica: um Ctrl-C no meio do write_parquet deixaria o índice
    # truncado, e ele é o que a API carrega no boot.
    tmp = index_path.with_suffix(".parquet.tmp")
    df.with_columns(pl.Series("album", album)).write_parquet(tmp)
    tmp.replace(index_path)
    print(f"nomes corrigidos: {fixed:,} · ainda com '?': {broken:,}")


def pending(index_path: Path, db: CoverCache, limit: int | None) -> list[str]:
    df = (
        pl.read_parquet(index_path, columns=["album_id", "pop"])
        .filter(pl.col("album_id").is_not_null())
        .sort("pop", descending=True)
    )
    ids = [i for i in df["album_id"].to_list() if valid_album_id(i)]
    if limit:
        ids = ids[:limit]
    known = db.known_ids()
    return [i for i in ids if i not in known]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", type=Path, default=REPO / "data/serve/album_index.parquet")
    ap.add_argument("--db", type=Path, default=REPO / "data/serve/covers.sqlite")
    ap.add_argument("--limit", type=int, default=None, help="só os N mais populares")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument(
        "--apply-only",
        action="store_true",
        help="não busca nada; só reescreve os nomes com o que já está em cache",
    )
    ap.add_argument("--no-apply", action="store_true", help="busca as capas sem tocar no índice")
    args = ap.parse_args()

    db = CoverCache(args.db)
    if args.apply_only:
        apply_titles(args.index, db)
        db.close()
        return 0

    todo = pending(args.index, db, args.limit)
    done_before = db.stats()["cached"]
    print(f"cache tem {done_before:,} álbuns · faltam {len(todo):,}", flush=True)
    if not todo:
        if not args.no_apply:
            apply_titles(args.index, db)
        db.close()
        return 0

    buf: list[tuple[str, str, str]] = []
    done = failed = 0
    backoff = 0.0
    nxt = 0
    t0 = time.perf_counter()

    # Workers puxando de um índice compartilhado: criar uma corrotina por álbum
    # significaria 831 mil tasks vivas ao mesmo tempo.
    async def worker() -> None:
        nonlocal done, failed, backoff, nxt
        while nxt < len(todo):
            album_id = todo[nxt]
            nxt += 1

            if backoff:
                await asyncio.sleep(backoff)
            got = await asyncio.to_thread(_oembed, album_id)

            if got is None:
                # Transitório (429 ou rede). Não cacheia: fica para a próxima rodada.
                failed += 1
                backoff = min(max(backoff * 2, BACKOFF_START), BACKOFF_MAX)
                continue

            backoff = 0.0
            buf.append((album_id, *got))
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
            f"\ncache: {s['cached']:,} álbuns, {s['with_cover']:,} com capa"
            f" (+{s['cached'] - done_before:,} nesta rodada, {failed:,} adiados)"
        )
        if not args.no_apply:
            apply_titles(args.index, db)
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
