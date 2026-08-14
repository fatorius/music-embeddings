#!/usr/bin/env python3
"""Stratified evaluation of the embeddings (§7 of the plan).

Aggregate Recall hides what matters here. A model can do very well at the head of the
catalog and badly in the tail while the global number barely moves — because the head
dominates the occurrences. Since the stated goal is precisely niche, we measure apart.

What it answers:
  A. Recall@K by SEED popularity     — "given an obscure album, do I recommend well?"
  B. Recall@K by TARGET popularity   — "do I ever surface obscure albums at all?"
  C. Mean popularity of what is returned — popularity bias in the output
  D. Share of same-artist neighbors  — the degenerate case (§7)
  E. Qualitative neighbors, matching artist+title

Usage:
    python scripts/05_eval.py --run album_lr0.001
    python scripts/05_eval.py --run album_lr0.001 --k 50
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import polars as pl
import torch

# Popularity bands by playlist count in the training set. Stage 0 showed that 1.13M
# albums have >=5 playlists and only 232K have >=50 — the tail is most of the catalog.
STRATA = [
    ("cauda   (<10 pl)", 0, 10),
    ("baixa   (10-49)", 10, 50),
    ("média   (50-199)", 50, 200),
    ("alta    (200-999)", 200, 1000),
    ("topo    (>=1000)", 1000, 10**9),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="nome da run em data/embeddings/runs/")
    ap.add_argument("--train", default="data/train")
    ap.add_argument("--emb-root", default="data/embeddings/runs")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--holdout", type=int, default=20_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--playlists", type=int, default=8000, help="playlists avaliadas")
    args = ap.parse_args()

    emb_path = Path(args.emb_root) / args.run / "album_emb.npy"
    if not emb_path.exists():
        print(f"embeddings não encontrados: {emb_path}")
        return 1

    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    rng = np.random.default_rng(args.seed)
    tr = Path(args.train)

    df = pl.read_parquet(tr / "pairs.parquet")
    playlist_ix = df["playlist_ix"].to_numpy().astype(np.int64)
    album_ix = df["album_ix"].to_numpy().astype(np.int64)
    n_items = int(album_ix.max()) + 1
    n_pl = int(playlist_ix.max()) + 1

    # The SAME split as training: same rng seed, same first operation.
    perm = rng.permutation(n_pl)
    held = np.zeros(n_pl, dtype=bool)
    held[perm[: args.holdout]] = True
    is_tr = ~held[playlist_ix]

    freq = np.bincount(album_ix[is_tr], minlength=n_items)   # popularity in training

    # holdout CSR
    hp, ha = playlist_ix[~is_tr], album_ix[~is_tr]
    order = np.argsort(hp, kind="stable")
    hp, ha = hp[order], ha[order].astype(np.int64)
    counts = np.bincount(hp, minlength=n_pl)
    indptr = np.zeros(n_pl + 1, dtype=np.int64)
    np.cumsum(counts, out=indptr[1:])

    W = torch.from_numpy(np.load(emb_path)).to(dev)
    W = torch.nn.functional.normalize(W, dim=1)
    print(f"run: {args.run}   embeddings: {tuple(W.shape)}   device: {dev}")

    lk = pl.read_parquet(tr / "album_lookup.parquet")
    art = pl.read_parquet(tr / "artist_lookup.parquet").rename({"name": "artist"})
    lk = lk.join(art, on="artist_id", how="left")
    artist_of = np.full(n_items, -1, dtype=np.int64)
    ax = lk["album_ix"].to_numpy()
    aix = lk["artist_ix"].fill_null(-1).to_numpy()
    artist_of[ax] = aix
    names = {int(a): (b, c) for a, b, c in
             zip(lk["album_ix"].to_list(), lk["artist"].to_list(), lk["name"].to_list())}

    cand = np.flatnonzero(counts >= 2)
    rng.shuffle(cand)
    cand = cand[: args.playlists]
    K = args.k

    # popularity rank: 0 = most popular
    pop_rank = np.empty(n_items, dtype=np.int64)
    pop_rank[np.argsort(-freq)] = np.arange(n_items)

    def bucket(f: int) -> int:
        for i, (_, lo, hi) in enumerate(STRATA):
            if lo <= f < hi:
                return i
        return len(STRATA) - 1

    seed_hits = np.zeros(len(STRATA)); seed_tot = np.zeros(len(STRATA))
    tgt_hits = np.zeros(len(STRATA)); tgt_tot = np.zeros(len(STRATA))
    ret_rank_sum = 0.0; ret_n = 0
    same_artist = 0; same_artist_tot = 0

    for s in range(0, len(cand), 256):
        chunk = cand[s : s + 256]
        seeds, targets = [], []
        for pid in chunk:
            it = ha[indptr[pid] : indptr[pid + 1]]
            seeds.append(int(it[0])); targets.append(set(it[1:].tolist()))
        sv = np.array(seeds, dtype=np.int64)
        sc = W[torch.from_numpy(sv).to(dev)] @ W.T
        sc.scatter_(1, torch.from_numpy(sv).to(dev).unsqueeze(1), -1e9)
        top = sc.topk(K, dim=1).indices.cpu().numpy()

        for r, tg in enumerate(targets):
            if not tg:
                continue
            hit = tg & set(top[r].tolist())
            b = bucket(int(freq[sv[r]]))
            seed_hits[b] += len(hit); seed_tot[b] += len(tg)
            for t in tg:
                bt = bucket(int(freq[t]))
                tgt_tot[bt] += 1
                if t in hit:
                    tgt_hits[bt] += 1
            ret_rank_sum += float(pop_rank[top[r]].mean()); ret_n += 1
            sa = artist_of[sv[r]]
            if sa >= 0:
                same_artist += int((artist_of[top[r]] == sa).sum())
                same_artist_tot += K

    print(f"\n{'='*70}\nA. Recall@{K} por popularidade do SEED\n{'='*70}")
    print(f"{'faixa':<20} {'recall':>9} {'alvos':>10}")
    for i, (lbl, _, _) in enumerate(STRATA):
        if seed_tot[i]:
            print(f"{lbl:<20} {seed_hits[i]/seed_tot[i]:>9.4f} {int(seed_tot[i]):>10,}")

    print(f"\n{'='*70}\nB. Recall@{K} por popularidade do ALVO\n{'='*70}")
    print(f"{'faixa':<20} {'recall':>9} {'alvos':>10}")
    for i, (lbl, _, _) in enumerate(STRATA):
        if tgt_tot[i]:
            print(f"{lbl:<20} {tgt_hits[i]/tgt_tot[i]:>9.4f} {int(tgt_tot[i]):>10,}")

    print(f"\n{'='*70}\nC. Viés de popularidade na saída\n{'='*70}")
    mean_rank = ret_rank_sum / max(ret_n, 1)
    print(f"rank médio de popularidade dos vizinhos devolvidos: "
          f"{mean_rank:,.0f} de {n_items:,}")
    print(f"  (percentil {100*mean_rank/n_items:.1f} — 50 seria neutro, "
          f"perto de 0 significa devolver só populares)")

    print(f"\n{'='*70}\nD. Vizinhos do mesmo artista (caso degenerado)\n{'='*70}")
    print(f"fração do top-{K} com o mesmo artista principal do seed: "
          f"{same_artist/max(same_artist_tot,1):.4f}")

    print(f"\n{'='*70}\nE. Vizinhos qualitativos\n{'='*70}")
    probes = [("Slint", "Spiderland"), ("Nick Drake", "Pink Moon"),
              ("Talk Talk", "Laughing Stock"), ("Bark Psychosis", "Hex"),
              ("Vashti Bunyan", "Just Another Diamond Day"),
              ("Godspeed You! Black Emperor", "Lift Your Skinny Fists Like Antennas to Heaven")]
    for artist, title in probes:
        # match artist AND title — searching by title alone picked the wrong album
        m = lk.filter((pl.col("artist") == artist) & (pl.col("name") == title))
        if m.is_empty():
            m = lk.filter((pl.col("artist") == artist)
                          & pl.col("name").str.starts_with(title))
        if m.is_empty():
            print(f"\n  [{artist} — {title}]  não encontrado")
            continue
        ix = int(m.sort("n_faixas", descending=True)["album_ix"][0])
        sims = (W @ W[ix]).cpu().numpy()
        sims[ix] = -1e9
        print(f"\n  [{artist} — {title}]   pop={freq[ix]} playlists")
        for r in np.argsort(-sims)[:8]:
            na, nt = names.get(int(r), ("?", "?"))
            print(f"      {sims[r]:.3f}  pop={freq[r]:<6} {na} — {nt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
