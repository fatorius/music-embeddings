"""Two-stage inference pipeline with re-ranking (§8-bis).

    seed(s)
      |- 1. candidates: top-N by ALBUM cosine FOR EACH SEED, then unioned
         2. score: sum of cosines to every seed (album and artist) + popularity hump
      |- top-K

Generating per seed and then unioning is what sets this apart from querying the
centroid: it guarantees every seed contributes its own candidates, instead of letting
a seed that sits far away in the space get diluted into the average.

The summed cosines of both spaces are z-scored PER QUERY over the candidate set before
being combined. Without that the weights lie: the two have similar means (~0.64) but
the artist one has 3.18x the spread, so a nominal weight of 0.25 on the artist would
carry ~51% of the real influence.

The album term docks the SPREAD of the per-seed cosines (w_consistency), so that an
album close to every seed beats one carried by a single seed. Any aggregation linear in
those cosines cannot do this: the sum is proportional to the cosine to the centroid, and
so is the mean — dividing by a per-query constant that the z-score then undoes.

The defaults (w_artist=0, w_pop=0, w_consistency=0) reproduce the plain album-cosine
ranking.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

NEG = -1e9


@dataclass(frozen=True)
class Params:
    """Scoring weights. Sourced from config/scoring.toml, never from the request."""

    k: int = 5
    n_candidates: int = 1000       # per seed, before the union
    w_album: float = 0.6
    w_artist: float = 0.15
    w_pop: float = 0.25
    w_consistency: float = 0.5     # docked from cos_album per unit of per-seed spread
    dup_penalty: float = 0.1       # docked from cos_artist per artist repetition
    pop_peak: float = 30.0
    pop_width: float = 1.0
    # filters — not weights, these arrive per request
    min_pop: int = 0
    max_pop: int | None = None
    exclude_same_artist: bool = False

    @classmethod
    def from_toml(cls, path: Path) -> Params:
        with path.open("rb") as fh:
            cfg = tomllib.load(fh)
        w, pop, ret = cfg["weights"], cfg["popularity"], cfg["retrieval"]
        return cls(
            k=int(ret["resultados"]),
            n_candidates=int(ret["candidatos_por_seed"]),
            w_album=float(w["w_album"]),
            w_artist=float(w["w_artist"]),
            w_pop=float(w["w_pop"]),
            # opcional: um scoring.toml anterior a este termo continua carregando
            w_consistency=float(w.get("consistencia", cls.w_consistency)),
            dup_penalty=float(w["dup_penalty"]),
            pop_peak=float(pop["pico"]),
            pop_width=float(pop["largura"]),
        )


class ParamsFile:
    """Params read from disk, reloaded whenever the file changes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime = -1.0
        self._params = Params()
        self.get()

    def get(self) -> Params:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return self._params
        if mtime != self._mtime:
            self._params = Params.from_toml(self.path)
            self._mtime = mtime
        return self._params

    def with_filters(self, **filters) -> Params:
        return replace(self.get(), **filters)


def hump(pop: np.ndarray, peak: float, width: float) -> np.ndarray:
    """Hump over log popularity: rewards the MIDDLE tail, not pop=1.

    A gaussian centered on `peak` playlists, `width` measured in natural log. A plain
    1/log(pop) would hand the top to essentially untrained vectors — 31.76% of the
    catalog has <5 playlists and carries 1.19% of the signal.
    """
    x = np.log(np.maximum(pop, 1))
    return np.exp(-((x - np.log(peak)) ** 2) / (2 * width**2))


def zscore(x: np.ndarray) -> np.ndarray:
    s = x.std()
    return (x - x.mean()) / s if s > 1e-8 else np.zeros_like(x)


class Recommender:
    def __init__(self, W_album: np.ndarray, W_artist: np.ndarray,
                 artist_ix: np.ndarray, pop: np.ndarray) -> None:
        self.A = W_album                  # (n_alb, d) normalized
        self.R = W_artist                 # (n_art, d) normalized
        self.artist_ix = artist_ix
        self.pop = pop

    def _mask(self, seeds: list[int], p: Params) -> np.ndarray:
        """Additive bias vector: 0 for a valid candidate, NEG for a discarded one."""
        m = np.zeros(self.A.shape[0])
        m[np.array(seeds)] = NEG
        if p.min_pop > 0:
            m[self.pop < p.min_pop] = NEG
        if p.max_pop is not None:
            m[self.pop > p.max_pop] = NEG
        if p.exclude_same_artist:
            seed_artists = list({int(self.artist_ix[i]) for i in seeds})
            m[np.isin(self.artist_ix, seed_artists)] = NEG
        return m

    def __call__(self, seeds: list[int], p: Params) -> tuple[list[dict], int]:
        n = self.A.shape[0]
        mask = self._mask(seeds, p)
        n_cand = min(p.n_candidates, n)

        # ---- 1. candidates: top-N from EACH seed, then unioned ------------------
        S = self.A[np.array(seeds)]                       # (n_seeds, d)
        pool: set[int] = set()
        for i in range(len(seeds)):
            sc = self.A @ S[i] + mask
            # partition instead of a full sort: we only need the top n_cand, unordered
            top = np.argpartition(-sc, n_cand - 1)[:n_cand] if n_cand < n else np.arange(n)
            pool.update(top[sc[top] > NEG / 2].tolist())
        if not pool:
            return [], 0
        cand = np.fromiter(sorted(pool), dtype=np.int64)

        # ---- 2. sum of cosines to ALL seeds, minus their spread ----------------
        # C is kept whole (n_cand, n_seeds): the spread is what separates an album
        # that sits near every seed from one a single seed dragged in.
        C = self.A[cand] @ S.T
        sum_album = C.sum(1)
        # The std is scaled by n_seeds because the sum grows with the number of seeds
        # and the std does not — without it the penalty would fade as seeds are added.
        spread = C.std(1)
        cos_album = sum_album - p.w_consistency * spread * len(seeds)

        seed_art = [int(self.artist_ix[i]) for i in seeds]
        cos_artist_raw = (self.R[self.artist_ix[cand]] @ self.R[seed_art].T).sum(1)

        # ---- 3. repeated-artist penalty ---------------------------------------
        # -dup_penalty PER earlier occurrence of the artist in the ranking, so that the
        # 2nd and 3rd album of one discography do not drop as a block. Seed artists
        # start with one occurrence already counted: in the artist space a seed's whole
        # discography scores the maximum possible (exactly 1.0 per seed).
        pop_boost = hump(self.pop[cand], p.pop_peak, p.pop_width)

        def combine(ca: np.ndarray) -> np.ndarray:
            return (
                p.w_album * zscore(cos_album)
                + p.w_artist * zscore(ca)
                + p.w_pop * pop_boost
            )

        cos_artist = cos_artist_raw
        if p.dup_penalty > 0 and p.w_artist > 0:
            cand_art = self.artist_ix[cand]
            seen = {a: 1 for a in seed_art}
            occ = np.empty(len(cand), dtype=np.float64)
            for i in np.argsort(-combine(cos_artist_raw)):
                a = int(cand_art[i])
                occ[i] = seen.get(a, 0)
                seen[a] = occ[i] + 1
            cos_artist = cos_artist_raw - p.dup_penalty * occ

        score = combine(cos_artist)

        # Ranking by the RAW sum: this column exists to show how far the re-ranking
        # moved an album, and the consistency dock is part of that re-ranking.
        rank_album = np.empty(len(cand), dtype=np.int64)
        rank_album[np.argsort(-sum_album)] = np.arange(1, len(cand) + 1)

        ns = len(seeds)
        return [

            {
                "album_ix": int(cand[i]),
                "score": float(score[i]),
                "cos_album": float(sum_album[i]) / ns,      # mean, for readability
                "cos_artist": float(cos_artist[i]) / ns,
                # spread of the per-seed cosines and what it cost, both per seed
                "spread": float(spread[i]),
                "cons": float(p.w_consistency * spread[i]),
                "worst": float(C[i].min()),
                "dup": float(cos_artist_raw[i] - cos_artist[i]),
                "pop_boost": float(pop_boost[i]),
                "rank_album": int(rank_album[i]),
            }
            for i in np.argsort(-score)[: p.k]
        ], len(cand)
