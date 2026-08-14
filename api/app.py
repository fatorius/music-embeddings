"""Recommendation API driven by embedding similarity.

Loads the album embeddings (Phase 1) and the serving index into memory, then exposes
search + recommendation. Nada aqui pede credenciais do Spotify: os links apontam para
https://open.spotify.com/album/{album_id} e as capas vêm do oEmbed público (api.covers).

    .venv/bin/uvicorn api.app:app --reload
"""

from __future__ import annotations

import os
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

import numpy as np
import polars as pl
import torch
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.artist_photos import ArtistPhotoCache, valid_artist_id
from api.covers import CoverCache, valid_album_id
from api.pipeline import ParamsFile, Recommender

REPO = Path(__file__).resolve().parent.parent
EMB_PATH = Path(os.environ.get("EMB_PATH", REPO / "data/embeddings/runs/album_final/album_emb.npy"))
ARTIST_EMB_PATH = Path(
    os.environ.get("ARTIST_EMB_PATH", REPO / "data/embeddings/runs/artist_final/artist_emb.npy")
)
INDEX_PATH = Path(os.environ.get("INDEX_PATH", REPO / "data/serve/album_index.parquet"))
ARTIST_INDEX_PATH = Path(
    os.environ.get("ARTIST_INDEX_PATH", REPO / "data/serve/artist_index.parquet")
)
CONFIG_PATH = Path(os.environ.get("SCORING_CONFIG", REPO / "config/scoring.toml"))
COVERS_PATH = Path(os.environ.get("COVERS_PATH", REPO / "data/serve/covers.sqlite"))
ARTIST_PHOTOS_PATH = Path(
    os.environ.get("ARTIST_PHOTOS_PATH", REPO / "data/serve/artist_photos.sqlite")
)
# The UI is the React + Vite app in front/. In development it runs on `npm run dev`,
# which proxies /api here; in production it is either served from the build output
# below, or built separately and hosted on GitHub Pages, calling this API cross-origin.
FRONT_DIST = Path(os.environ.get("FRONT_DIST", REPO / "front/dist"))
# Comma-separated origins allowed to call the API cross-origin, e.g. the Pages URL
# "https://<user>.github.io". No cookies/auth are involved, so "*" (the default) is
# safe — tighten it only if that matters for another reason.
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]

params = ParamsFile(CONFIG_PATH)


def fold(s: str) -> str:
    """Lowercase, accent-free — applied to both the index and the query."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


class Store:
    def __init__(
        self, emb_path: Path, index_path: Path, artist_emb_path: Path, artist_index_path: Path
    ) -> None:
        W = np.load(emb_path).astype(np.float32)
        W /= np.linalg.norm(W, axis=1, keepdims=True) + 1e-9
        self.W = torch.from_numpy(W)

        R = np.load(artist_emb_path).astype(np.float32)
        R /= np.linalg.norm(R, axis=1, keepdims=True) + 1e-9
        self.R = R
        self.RT = torch.from_numpy(R)

        df = pl.read_parquet(index_path).sort("album_ix")
        if df.height != W.shape[0]:
            raise RuntimeError(f"índice tem {df.height} linhas, embeddings têm {W.shape[0]}")
        if df["album_ix"].to_numpy()[-1] != df.height - 1:
            raise RuntimeError("album_ix não é 0..N-1 contíguo")

        self.df = df.with_columns(
            (
                pl.col("artist").fill_null("") + pl.lit("  ") + pl.col("album").fill_null("")
            ).map_elements(fold, return_dtype=pl.String).alias("hay")
        )
        self.album = df["album"].to_list()
        self.artist = df["artist"].to_list()
        self.album_id = df["album_id"].to_list()
        self.pop = df["pop"].to_numpy()
        self.artist_ix = df["artist_ix"].to_numpy()
        self.n_faixas = df["n_faixas"].to_numpy()
        self.minutos = df["minutos"].to_numpy()

        self.reco = Recommender(self.W, self.R, self.artist_ix, self.pop)

        adf = pl.read_parquet(artist_index_path).sort("artist_ix")
        if adf.height != R.shape[0]:
            raise RuntimeError(f"índice de artista tem {adf.height} linhas, embeddings têm {R.shape[0]}")
        if adf["artist_ix"].to_numpy()[-1] != adf.height - 1:
            raise RuntimeError("artist_ix não é 0..N-1 contíguo")

        self.adf = adf.with_columns(
            pl.col("artist").fill_null("").map_elements(fold, return_dtype=pl.String).alias("hay")
        )
        self.artist_name = adf["artist"].to_list()
        self.artist_id = adf["artist_id"].to_list()
        self.artist_pop = adf["pop"].to_numpy()

    def card(self, ix: int, score: float | None = None) -> dict:
        d = {
            "album_ix": int(ix),
            "album": self.album[ix],
            "artist": self.artist[ix],
            "album_id": self.album_id[ix],
            "pop": int(self.pop[ix]),
            "n_faixas": int(self.n_faixas[ix]),
            "minutos": round(float(self.minutos[ix]), 1),
            "spotify_url": f"https://open.spotify.com/album/{self.album_id[ix]}",
        }
        if score is not None:
            d["score"] = round(score, 4)
        return d

    def acard(self, ix: int, score: float | None = None) -> dict:
        d = {
            "artist_ix": int(ix),
            "artist": self.artist_name[ix],
            "artist_id": self.artist_id[ix],
            "pop": int(self.artist_pop[ix]),
            "spotify_url": f"https://open.spotify.com/artist/{self.artist_id[ix]}",
        }
        if score is not None:
            d["score"] = round(score, 4)
        return d


store: Store | None = None
covers_cache: CoverCache | None = None
artist_photos_cache: ArtistPhotoCache | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store, covers_cache, artist_photos_cache
    store = Store(EMB_PATH, INDEX_PATH, ARTIST_EMB_PATH, ARTIST_INDEX_PATH)
    covers_cache = CoverCache(COVERS_PATH)
    artist_photos_cache = ArtistPhotoCache(ARTIST_PHOTOS_PATH)
    try:
        yield
    finally:
        covers_cache.close()
        artist_photos_cache.close()


app = FastAPI(title="Recomendação de álbuns por similaridade", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def _store() -> Store:
    if store is None:
        raise HTTPException(503, "índice ainda carregando")
    return store


@app.get("/api/search")
def search(
    q: str = Query(min_length=2),
    limit: int = Query(20, ge=1, le=100),
    min_pop: int = Query(0, ge=0),
    max_pop: int | None = Query(None, ge=0),
) -> dict:
    """Search by artist and/or title substring. Ordered by popularity."""
    s = _store()
    terms = [t for t in fold(q).split() if t]
    if not terms:
        return {"results": []}

    mask = pl.lit(True)
    for t in terms:
        mask = mask & pl.col("hay").str.contains(t, literal=True)
    if min_pop > 0:
        mask = mask & (pl.col("pop") >= min_pop)
    if max_pop is not None:
        mask = mask & (pl.col("pop") <= max_pop)

    hits = (
        s.df.lazy()
        .filter(mask)
        .sort("pop", descending=True)
        .head(limit)
        .select("album_ix")
        .collect()["album_ix"]
        .to_list()
    )
    return {"results": [s.card(ix) for ix in hits]}


@app.get("/api/artists/search")
def search_artists(
    q: str = Query(min_length=2),
    limit: int = Query(20, ge=1, le=100),
    min_pop: int = Query(0, ge=0),
    max_pop: int | None = Query(None, ge=0),
) -> dict:
    """Search artists by name substring. Ordered by popularity."""
    s = _store()
    terms = [t for t in fold(q).split() if t]
    if not terms:
        return {"results": []}

    mask = pl.lit(True)
    for t in terms:
        mask = mask & pl.col("hay").str.contains(t, literal=True)
    if min_pop > 0:
        mask = mask & (pl.col("pop") >= min_pop)
    if max_pop is not None:
        mask = mask & (pl.col("pop") <= max_pop)

    hits = (
        s.adf.lazy()
        .filter(mask)
        .sort("pop", descending=True)
        .head(limit)
        .select("artist_ix")
        .collect()["artist_ix"]
        .to_list()
    )
    return {"results": [s.acard(ix) for ix in hits]}


MAX_COVER_IDS = 60


def _covers() -> CoverCache:
    if covers_cache is None:
        raise HTTPException(503, "cache de capas ainda carregando")
    return covers_cache


@app.get("/api/covers")
async def covers(ids: str = Query(description="album_ids separados por vírgula")) -> dict:
    """Capa + título oficial para vários álbuns de uma vez.

    Consulta em lote porque o front pinta listas inteiras; o custo por álbum é
    pago uma vez só, no primeiro miss. Ids desconhecidos saem de fora da resposta.
    """
    wanted = [i for i in (x.strip() for x in ids.split(",")) if valid_album_id(i)]
    if not wanted:
        return {"covers": {}}
    return {"covers": await _covers().resolve(wanted[:MAX_COVER_IDS])}


@app.get("/api/covers/stats")
def covers_stats() -> dict:
    """Cobertura do cache — útil para decidir se vale pré-popular o índice."""
    return _covers().stats()


MAX_ARTIST_PHOTO_IDS = 60


def _artist_photos() -> ArtistPhotoCache:
    if artist_photos_cache is None:
        raise HTTPException(503, "cache de fotos de artista ainda carregando")
    return artist_photos_cache


@app.get("/api/artist_photos")
async def artist_photos(ids: str = Query(description="artist_ids separados por vírgula")) -> dict:
    """Foto para vários artistas de uma vez — mesma lógica de /api/covers."""
    wanted = [i for i in (x.strip() for x in ids.split(",")) if valid_artist_id(i)]
    if not wanted:
        return {"photos": {}}
    return {"photos": await _artist_photos().resolve(wanted[:MAX_ARTIST_PHOTO_IDS])}


@app.get("/api/artist_photos/stats")
def artist_photos_stats() -> dict:
    """Cobertura do cache — útil para decidir se vale pré-popular o índice."""
    return _artist_photos().stats()


class RecoRequest(BaseModel):
    """Weights do NOT belong here — they come from config/scoring.toml. Filters only."""

    seeds: list[int] = Field(min_length=1, max_length=20)
    exclude_same_artist: bool = False
    min_pop: int = Field(0, ge=0)
    max_pop: int | None = None


@app.get("/api/config")
def config() -> dict:
    """Active weights — the UI displays them read-only."""
    return asdict(params.get()) | {"source": str(CONFIG_PATH)}


@app.post("/api/recommend")
def recommend(req: RecoRequest) -> dict:
    s = _store()
    n = s.W.shape[0]
    for ix in req.seeds:
        if not 0 <= ix < n:
            raise HTTPException(400, f"album_ix fora do intervalo: {ix}")

    p = params.with_filters(**req.model_dump(exclude={"seeds"}))
    hits, n_cand = s.reco(req.seeds, p)

    return {
        "seeds": [s.card(i) for i in req.seeds],
        "n_candidates": n_cand,
        "results": [
            s.card(h["album_ix"], h["score"])
            | {
                "cos_album": round(h["cos_album"], 4),
                "cos_artist": round(h["cos_artist"], 4),
                "spread": round(h["spread"], 4),
                "cons": round(h["cons"], 4),
                "worst": round(h["worst"], 4),
                "dup": round(h["dup"], 3),
                "pop_boost": round(h["pop_boost"], 4),
                "rank_album": h["rank_album"],
            }
            for h in hits
        ],
    }


class Album2VecTerm(BaseModel):
    """One side of the equation: an album with the op that combines it with the
    running total, e.g. `+king`, `-man`, `*mood`. Ignored on the first term — there's
    nothing before it to combine with, it's just the starting vector.
    """

    album_ix: int
    op: Literal["+", "-", "*"] = "+"


MAX_ALBUM2VEC_TERMS = 4


class Album2VecRequest(BaseModel):
    """A - B + C * D style vector arithmetic on album embeddings.

    Terms are folded left to right — start at the first album's vector, then apply
    each subsequent term's op (add, subtract, or elementwise multiply) in sequence.
    There's no operator precedence, same as a plain calculator. The result is
    renormalized and matched against every album by cosine similarity; the single
    closest match is returned.
    """

    terms: list[Album2VecTerm] = Field(min_length=1, max_length=MAX_ALBUM2VEC_TERMS)
    exclude_input: bool = True
    # filters the result only — the equation terms themselves are never dropped by this.
    # Default keeps the match from landing on a near-untrained, low-signal album; the
    # UI doesn't expose this as a control, so it's a fixed floor rather than a request field.
    min_pop: int = Field(500, ge=0)
    max_pop: int | None = None


@app.post("/api/album2vec/album")
def album2vec_album(req: Album2VecRequest) -> dict:
    s = _store()
    n = s.W.shape[0]
    for t in req.terms:
        if not 0 <= t.album_ix < n:
            raise HTTPException(400, f"album_ix out of range: {t.album_ix}")

    vec = s.W[req.terms[0].album_ix].clone()
    for t in req.terms[1:]:
        v = s.W[t.album_ix]
        if t.op == "+":
            vec = vec + v
        elif t.op == "-":
            vec = vec - v
        else:
            vec = vec * v
    norm = vec.norm()
    if norm < 1e-9:
        raise HTTPException(400, "resulting vector is zero (the terms cancel out)")
    vec = vec / norm

    scores = (s.W @ vec).numpy()
    exclude = {t.album_ix for t in req.terms} if req.exclude_input else set()
    in_pop_range = s.pop >= req.min_pop
    if req.max_pop is not None:
        in_pop_range &= s.pop <= req.max_pop

    best = None
    for ix in np.argsort(-scores):
        ix = int(ix)
        if ix in exclude or not in_pop_range[ix]:
            continue
        best = s.card(ix, float(scores[ix]))
        break

    return {
        "terms": [s.card(t.album_ix) | {"op": t.op} for t in req.terms],
        "result": best,
    }


class Artist2VecTerm(BaseModel):
    """One side of the equation: an artist with the op that combines it with the
    running total, e.g. `+king`, `-man`, `*mood`. Ignored on the first term — there's
    nothing before it to combine with, it's just the starting vector.
    """

    artist_ix: int
    op: Literal["+", "-", "*"] = "+"


MAX_ARTIST2VEC_TERMS = 4


class Artist2VecRequest(BaseModel):
    """A - B + C * D style vector arithmetic on artist embeddings.

    Same fold as /api/album2vec/album, just over the artist embedding space.
    """

    terms: list[Artist2VecTerm] = Field(min_length=1, max_length=MAX_ARTIST2VEC_TERMS)
    exclude_input: bool = True
    min_pop: int = Field(500, ge=0)
    max_pop: int | None = None


@app.post("/api/artist2vec/artist")
def artist2vec_artist(req: Artist2VecRequest) -> dict:
    s = _store()
    n = s.RT.shape[0]
    for t in req.terms:
        if not 0 <= t.artist_ix < n:
            raise HTTPException(400, f"artist_ix out of range: {t.artist_ix}")

    vec = s.RT[req.terms[0].artist_ix].clone()
    for t in req.terms[1:]:
        v = s.RT[t.artist_ix]
        if t.op == "+":
            vec = vec + v
        elif t.op == "-":
            vec = vec - v
        else:
            vec = vec * v
    norm = vec.norm()
    if norm < 1e-9:
        raise HTTPException(400, "resulting vector is zero (the terms cancel out)")
    vec = vec / norm

    scores = (s.RT @ vec).numpy()
    exclude = {t.artist_ix for t in req.terms} if req.exclude_input else set()
    in_pop_range = s.artist_pop >= req.min_pop
    if req.max_pop is not None:
        in_pop_range &= s.artist_pop <= req.max_pop

    best = None
    for ix in np.argsort(-scores):
        ix = int(ix)
        if ix in exclude or not in_pop_range[ix]:
            continue
        best = s.acard(ix, float(scores[ix]))
        break

    return {
        "terms": [s.acard(t.artist_ix) | {"op": t.op} for t in req.terms],
        "result": best,
    }


@app.get("/api/album/{album_ix}")
def album(album_ix: int) -> dict:
    s = _store()
    if not 0 <= album_ix < s.W.shape[0]:
        raise HTTPException(404, "álbum não encontrado")
    return s.card(album_ix)


@app.get("/api/artist/{artist_ix}")
def artist(artist_ix: int) -> dict:
    s = _store()
    if not 0 <= artist_ix < s.RT.shape[0]:
        raise HTTPException(404, "artista não encontrado")
    return s.acard(artist_ix)


@app.get("/", response_model=None)
def index() -> FileResponse | JSONResponse:
    """Serves the bundled UI if one is mounted (FRONT_DIST) — e.g. local single-process
    dev. In the Docker deployment there is no front/dist by design (the UI is built and
    hosted separately, on GitHub Pages), so this is a plain banner, not an error: hitting
    `/` on the API host directly is not part of the intended flow.
    """
    entry = FRONT_DIST / "index.html"
    if not entry.exists():
        return JSONResponse(
            {
                "service": "Recomendação de álbuns por similaridade",
                "docs": "/docs",
                "note": "this deployment serves /api only — the UI is hosted separately",
            }
        )
    return FileResponse(entry)


@app.exception_handler(StarletteHTTPException)
async def spa_fallback(request: Request, exc: StarletteHTTPException) -> FileResponse | JSONResponse:
    """Unmatched GETs fall back to index.html — react-router owns everything past that.

    Without this, a direct load or refresh on a client-side route like /analogy 404s:
    StaticFiles only serves index.html for /, never for its other paths.
    """
    entry = FRONT_DIST / "index.html"
    if exc.status_code == 404 and not request.url.path.startswith("/api") and entry.exists():
        return FileResponse(entry)
    # Re-raising here would leave this same handler on the stack to catch it again,
    # which Starlette treats as an unhandled error (500) instead of redispatching.
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


# Mounted last so it never shadows /api. Absent in development: the Vite dev server
# serves the assets itself and only forwards /api to this process.
if FRONT_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONT_DIST, html=True), name="front")
