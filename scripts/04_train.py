#!/usr/bin/env python3
"""Phase 1 — Proxy training of the embeddings (§6 of the plan).

Two models, same architecture, different levels:
    python scripts/04_train.py --level album  --epochs 10
    python scripts/04_train.py --level artist --epochs 10

Architecture: a SINGLE shared embedding table, scored by DOT PRODUCT.

Why dot product and not an MLP over the concatenation: the embeddings get extracted
and queried by cosine similarity in pgvector. If the training score is an MLP, nothing
forces the space to be metric — the MLP can compose coordinates non-linearly and leave
similar items far apart in cosine. With dot product, the training objective IS the
geometry used at inference.

EPOCH: each (playlist, item) occurrence serves as an anchor exactly once per epoch,
with one positive sampled among the remaining items of the same playlist. That is
49.05M anchors per epoch.

This replaces the previous version's uniform per-playlist sampling, which carried a
severe bias: the chance of an occurrence being drawn was 2/L, so items in 5,000-track
playlists were seen ~0.16 times per 393M samples, against ~39 times for items in
playlists of 20. Since Stage 0 showed the niche tail lives precisely in the large
playlists, that scheme diluted exactly what the project wants to learn. Iterating per
occurrence equalizes exposure.

Pairs are NOT materialized: the corpus holds 5.74 billion album pairs. The positive is
sampled on the fly from the playlist -> items CSR.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import polars as pl
import torch


def build_csr(playlist_ix: np.ndarray, item_ix: np.ndarray, n_playlists: int):
    """Playlist -> items CSR. Returns (indptr, indices, playlist_of_each_slot)."""
    order = np.argsort(playlist_ix, kind="stable")
    pl_sorted = playlist_ix[order]
    indices = item_ix[order].astype(np.int32)
    counts = np.bincount(pl_sorted, minlength=n_playlists)
    indptr = np.zeros(n_playlists + 1, dtype=np.int64)
    np.cumsum(counts, out=indptr[1:])
    occ_pl = np.repeat(np.arange(n_playlists, dtype=np.int32), counts)
    return indptr, indices, occ_pl


def make_neg_table(freq: np.ndarray, rng, size: int = 20_000_000) -> np.ndarray:
    """Negative sampling table ∝ freq^0.75 (word2vec-style)."""
    p = np.power(freq.astype(np.float64), 0.75)
    p /= p.sum()
    return np.searchsorted(np.cumsum(p), rng.random(size)).astype(np.int32)


def make_batches(anchors_pos, indptr, indices, occ_pl, lens, neg_table, rng, B, neg):
    """Build (anchor, positive, negatives) for the given occurrence slots."""
    p = occ_pl[anchors_pos].astype(np.int64)
    base = indptr[p]
    L = lens[p]
    within = anchors_pos - base                       # offset inside the playlist
    off = 1 + (rng.random(len(p)) * (L - 1)).astype(np.int64)
    j = (within + off) % L                            # guarantees a distinct item
    anc = indices[anchors_pos].astype(np.int64)
    pos = indices[base + j].astype(np.int64)
    ng = neg_table[rng.integers(0, len(neg_table), size=len(p) * neg)] \
            .astype(np.int64).reshape(len(p), neg)
    return anc, pos, ng


def bpr_loss(emb, a, pv, nv):
    ea, ep, en = emb(a), emb(pv), emb(nv)
    s_pos = (ea * ep).sum(-1, keepdim=True)
    s_neg = torch.bmm(en, ea.unsqueeze(-1)).squeeze(-1)
    return -torch.nn.functional.logsigmoid(s_pos - s_neg).mean()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", choices=("album", "artist"), default="album")
    ap.add_argument("--train", default="data/train")
    ap.add_argument("--out", default="data/embeddings")
    ap.add_argument("--dim", type=int, default=128)
    ap.add_argument("--epochs", type=int, default=10)
    # batch 32768: MPS benchmark gave 194K pairs/s against 78K at 8192.
    # 131072 reaches 335K but cuts optimizer updates; 524288 collapses.
    ap.add_argument("--batch", type=int, default=32_768)
    ap.add_argument("--lr", type=float, default=0.05)
    ap.add_argument("--neg", type=int, default=5)
    ap.add_argument("--holdout", type=int, default=20_000)
    ap.add_argument("--patience", type=int, default=3,
                    help="epochs sem melhora da métrica de seleção antes de parar")
    ap.add_argument("--val-batches", type=int, default=200)
    # BPR val_loss is NOT a usable stopping criterion. Measured: lr=0.005 gives the
    # best val_loss (0.1441) but lr=0.001 gives the best Recall@10 (0.0210 vs 0.0187).
    # BPR measures "separate the positive from 5 sampled negatives"; the real task is
    # "rank among 831,827 candidates". BPR can be driven down by inflating norms
    # without improving retrieval. We select on Recall instead.
    ap.add_argument("--select", choices=("recall", "val_loss"), default="recall",
                    help="métrica para best.pt e early stopping")
    ap.add_argument("--eval-k", type=int, default=10)
    ap.add_argument("--eval-playlists", type=int, default=2000,
                    help="playlists por avaliação de Recall durante o treino")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--run", default=None)
    ap.add_argument("--resume", default=None)
    ap.add_argument("--promote", action="store_true",
                    help="copia os embeddings da run para data/embeddings/")
    args = ap.parse_args()

    run = args.run or f"{args.level}_d{args.dim}_b{args.batch//1024}k_lr{args.lr}_n{args.neg}"
    run_dir = Path(args.out) / "runs" / run
    run_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = run_dir / "checkpoint.pt"
    best_path = run_dir / "best.pt"
    print(f"run: {run}   dir: {run_dir}")

    rng = np.random.default_rng(args.seed)
    torch.manual_seed(args.seed)
    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    print(f"device: {dev}   level: {args.level}")

    tr = Path(args.train)
    df = pl.read_parquet(tr / "pairs.parquet")
    if args.level == "artist":
        df = (df.group_by(["playlist_ix", "artist_ix"])
                .agg(pl.col("n_tracks").sum())
                .rename({"artist_ix": "item_ix"}))
    else:
        df = df.rename({"album_ix": "item_ix"})

    playlist_ix = df["playlist_ix"].to_numpy().astype(np.int64)
    item_ix = df["item_ix"].to_numpy().astype(np.int64)
    n_items = int(item_ix.max()) + 1
    n_playlists = int(playlist_ix.max()) + 1
    print(f"ocorrências: {len(df):,}   itens: {n_items:,}   playlists: {n_playlists:,}")

    perm = rng.permutation(n_playlists)
    held = np.zeros(n_playlists, dtype=bool)
    held[perm[: args.holdout]] = True
    is_train = ~held[playlist_ix]

    indptr, indices, occ_pl = build_csr(
        playlist_ix[is_train], item_ix[is_train], n_playlists)
    v_indptr, v_indices, v_occ_pl = build_csr(
        playlist_ix[~is_train], item_ix[~is_train], n_playlists)
    lens = np.diff(indptr)
    v_lens = np.diff(v_indptr)

    # Valid anchors: occurrences whose playlist holds >=2 items.
    train_anchors = np.flatnonzero(lens[occ_pl] >= 2)
    val_anchors = np.flatnonzero(v_lens[v_occ_pl] >= 2)
    steps_per_epoch = int(np.ceil(len(train_anchors) / args.batch))
    print(f"âncoras treino: {len(train_anchors):,}   validação: {len(val_anchors):,}")
    print(f"steps por epoch: {steps_per_epoch:,}")

    freq = np.bincount(indices, minlength=n_items).astype(np.float64)
    freq[freq == 0] = 1.0
    neg_table = make_neg_table(freq, rng)

    emb = torch.nn.Embedding(n_items, args.dim, sparse=True).to(dev)
    torch.nn.init.normal_(emb.weight, std=0.1 / args.dim**0.5)
    opt = torch.optim.SparseAdam(emb.parameters(), lr=args.lr)

    def weights() -> torch.Tensor:
        return emb.weight.detach()

    start_epoch, best_val, bad = 1, float("inf"), 0
    best_score = -float("inf")
    history: list[dict] = []
    if args.resume:
        ck = torch.load(args.resume, map_location=dev)
        if ck["n_items"] != n_items or ck["dim"] != args.dim:
            print(f"ERRO: checkpoint incompatível (n_items={ck['n_items']}, dim={ck['dim']})")
            return 1
        emb.load_state_dict(ck["emb"]); opt.load_state_dict(ck["opt"])
        start_epoch = ck["epoch"] + 1
        if start_epoch > args.epochs:
            print(f"ERRO: checkpoint está no epoch {ck['epoch']} e --epochs="
                  f"{args.epochs}. Nenhum epoch rodaria. Passe --epochs maior.")
            return 1
        prev_neg = ck.get("args", {}).get("neg")
        if prev_neg is not None and prev_neg != args.neg:
            print(f"AVISO: retomando com --neg {args.neg} sobre um checkpoint "
                  f"treinado com --neg {prev_neg}. O resultado é híbrido e não "
                  f"isola o efeito de --neg numa comparação.")
        best_val = ck.get("best_val", float("inf"))
        best_score = ck.get("best_score", -float("inf"))
        history = ck.get("history", [])
        print(f"retomando de {args.resume} no epoch {start_epoch}")

    def save(path: Path, epoch: int) -> None:
        tmp = path.with_suffix(".tmp")
        torch.save({"emb": emb.state_dict(), "opt": opt.state_dict(),
                    "epoch": epoch, "best_val": best_val, "best_score": best_score,
                    "history": history,
                    "n_items": n_items, "dim": args.dim, "level": args.level,
                    "args": vars(args)}, tmp)
        tmp.replace(path)

    @torch.no_grad()
    def validate() -> float:
        sel = rng.choice(val_anchors,
                         size=min(len(val_anchors), args.val_batches * args.batch),
                         replace=False)
        tot, nb = 0.0, 0
        for s in range(0, len(sel), args.batch):
            q = np.sort(sel[s : s + args.batch])
            anc, pos, ng = make_batches(q, v_indptr, v_indices, v_occ_pl,
                                        v_lens, neg_table, rng, args.batch, args.neg)
            tot += bpr_loss(emb,
                            torch.from_numpy(anc).to(dev),
                            torch.from_numpy(pos).to(dev),
                            torch.from_numpy(ng).to(dev)).item()
            nb += 1
        return tot / max(nb, 1)

    eval_pool = np.flatnonzero(v_lens >= 2)

    @torch.no_grad()
    def recall_at_k(k: int, n_playlists_eval: int) -> float:
        Wn = torch.nn.functional.normalize(weights(), dim=1)
        sel = eval_pool[: n_playlists_eval]
        hits = tot = 0
        for s in range(0, len(sel), 256):
            seeds, targets = [], []
            for pid in sel[s : s + 256]:
                it = v_indices[v_indptr[pid] : v_indptr[pid + 1]]
                seeds.append(it[0]); targets.append(set(it[1:].tolist()))
            sv = np.array(seeds, dtype=np.int64)
            sc = Wn[torch.from_numpy(sv).to(dev)] @ Wn.T
            sc.scatter_(1, torch.from_numpy(sv).to(dev).unsqueeze(1), -1e9)
            top = sc.topk(k, dim=1).indices.cpu().numpy()
            for r, tg in enumerate(targets):
                if tg:
                    hits += len(tg & set(top[r].tolist()))
                    tot += len(tg)
        return hits / max(tot, 1)

    print(f"\nval_loss inicial (referência aleatória = {np.log(2):.4f}): "
          f"{validate():.4f}\n")

    started = time.time()
    for epoch in range(start_epoch, args.epochs + 1):
        order = rng.permutation(train_anchors)
        ep_start, run_loss, nb = time.time(), 0.0, 0

        for s in range(0, len(order), args.batch):
            q = np.sort(order[s : s + args.batch])
            anc, pos, ng = make_batches(q, indptr, indices, occ_pl, lens,
                                        neg_table, rng, args.batch, args.neg)
            loss = bpr_loss(emb,
                            torch.from_numpy(anc).to(dev),
                            torch.from_numpy(pos).to(dev),
                            torch.from_numpy(ng).to(dev))
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            run_loss += loss.item()
            nb += 1

        tr_loss = run_loss / nb
        va_loss = validate()
        rec = recall_at_k(args.eval_k, args.eval_playlists)
        el = time.time() - ep_start
        mark = ""
        history.append({"epoch": epoch, "train_loss": tr_loss, "val_loss": va_loss,
                        f"recall@{args.eval_k}": rec, "min": round(el / 60, 2)})

        # score: HIGHER is better. val_loss enters negated so both agree in sign.
        score = rec if args.select == "recall" else -va_loss
        if score > best_score + 1e-6:
            best_score, best_val, bad = score, va_loss, 0
            save(best_path, epoch)
            mark = "  <- melhor"
        else:
            bad += 1
        save(ckpt_path, epoch)

        print(f"epoch {epoch:>3}/{args.epochs}  train {tr_loss:.4f}  "
              f"val {va_loss:.4f}  R@{args.eval_k} {rec:.4f}  "
              f"{el/60:.1f} min{mark}", flush=True)

        if bad >= args.patience:
            print(f"early stop: {bad} epochs sem melhora (melhor val {best_val:.4f})")
            break

    # Restore the best state before exporting — the last epoch may have regressed.
    if best_path.exists():
        emb.load_state_dict(torch.load(best_path, map_location=dev)["emb"])
        print(f"\nrestaurado o melhor checkpoint ({args.select} score {best_score:.4f})")

    W = torch.nn.functional.normalize(weights(), dim=1)
    print("\navaliando Recall em holdout…")
    # A DEDICATED, fixed rng: the main `rng` advances with the number of epochs, so
    # runs with different epoch counts drew different samples and their final Recall
    # values were not comparable to each other.
    cand = np.flatnonzero(v_lens >= 2)
    np.random.default_rng(12345).shuffle(cand)
    cand = cand[:5000]
    pop_top = np.argsort(-freq)[:100]
    for K in (10, 50, 100):
        hits = pop_hits = tot = 0
        pop_set = set(pop_top[:K].tolist())
        for s in range(0, len(cand), 256):
            chunk = cand[s : s + 256]
            seeds, targets = [], []
            for pid in chunk:
                it = v_indices[v_indptr[pid] : v_indptr[pid + 1]]
                seeds.append(it[0]); targets.append(set(it[1:].tolist()))
            sv = np.array(seeds, dtype=np.int64)
            q = W[torch.from_numpy(sv).to(dev)]
            sc = q @ W.T
            sc.scatter_(1, torch.from_numpy(sv).to(dev).unsqueeze(1), -1e9)
            top = sc.topk(K, dim=1).indices.cpu().numpy()
            for r, tg in enumerate(targets):
                if tg:
                    hits += len(tg & set(top[r].tolist()))
                    pop_hits += len(tg & pop_set)
                    tot += len(tg)
        print(f"  Recall@{K:<4} modelo {hits/max(tot,1):.4f}   "
              f"popularidade {pop_hits/max(tot,1):.4f}   "
              f"lift {hits/max(pop_hits,1):.2f}x")

    vec = weights().cpu().numpy().astype(np.float32)
    np.save(run_dir / f"{args.level}_emb.npy", vec)
    (run_dir / "history.json").write_text(json.dumps(
        {"run": run, "history": history, "best_val": best_val, "best_score": best_score,
         "args": vars(args)}, indent=2))
    if args.promote:
        out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
        np.save(out / f"{args.level}_emb.npy", vec)
        print(f"promovido para {out / f'{args.level}_emb.npy'}")
    print(f"\nsalvo: {run_dir / f'{args.level}_emb.npy'}  shape={vec.shape}")
    print(f"tempo total: {(time.time()-started)/60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
