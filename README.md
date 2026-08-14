# music-embeddings

> **The trained embeddings and serving index are not in this repo.** They're published as
> a [GitHub Release](../../releases) asset and downloaded by the API container at start
> (see step 10, "Publish the data and run the API"). Everything under `scripts/` is the
> pipeline that *produces* them from the raw playlist dump — it exists so the embeddings
> are reproducible, not because you need to run it to use the API.

Album recommendation by *vibe* similarity, derived from playlist co-occurrence. Given a
seed album, it returns similar albums without relying on declared genre, reviews, or any
descriptive text.

---

## How it works

An embedding table trained with **BPR** (Bayesian Personalized Ranking) over 49 million
`(playlist, album)` pairs. For each real occurrence, the model learns to score the album
that is present above negatives sampled ∝ freq^0.75. Scoring is a dot product over a
shared table — no MLP on top, deliberately: only then is the exported space metric, and
vector proximity means something.

Two independent models, same procedure: **album** (831,827 items) and **artist**
(248,709). Both feed into the score at inference time.

---

## Requirements

- Python 3.14. Training uses MPS when available and falls back to CPU automatically —
  there is no device flag, and CUDA is not covered
- Node 20+, for the web UI only
- ~60 GB free disk during extraction; ~5 GB in the final state
- ~16 GB RAM

```bash
python3 -m venv .venv
.venv/bin/pip install torch numpy polars pyarrow duckdb scipy fastapi "uvicorn[standard]"
```

`requirements.txt` pins the same set (minus torch, installed separately there as a
CPU-only wheel) for the [Dockerfile](Dockerfile), which serves the API in production —
see step 10.

Every command below runs from the repository root. Nothing under `data/` is versioned.

---

## Step by step

### 0. Download the dump — ~11 GB, a few hours

```bash
tmux new -s mpd          # Zenodo drops long connections; use tmux
./scripts/00_download.sh
```

Source: [Zenodo 5002584](https://zenodo.org/records/5002584), a MySQL dump of Spotify data
holding 1,054,935 playlists. **This is not the official MPD** — its largest playlist has
10,923 tracks, against the MPD's 250 cap. The script is resumable and verifies md5: Zenodo
cuts transfers with curl error 18 on large files, so it retries until the file closes.
Running it again after a drop picks up where it stopped.

Output: `data/raw/spotifydbdumpshare.sql` (10.7 GB) plus the schema (5 KB).

### 1. Extract to Parquet — ~5 min

```bash
.venv/bin/python scripts/01_extract.py
```

The dump is MySQL with extended `INSERT`, not PostgreSQL with `COPY` — extraction is a
streaming SQL-literal tokenizer running at ~40 MB/s, never holding the file in memory.

Output: `data/parquet/` (~4 GB) with `album`, `artist`, `track`, `playlist`,
`track_artist`, `track_playlist`.

### 2. Profile the corpus — ~3 min, optional

```bash
.venv/bin/python scripts/02_profile.py
```

Diagnostic only; nothing downstream consumes it. This is what revealed that 47.6% of the
"albums" are single-track singles, that title fragmentation has a median of 1 (making
Jaccard deduplication unnecessary), and that non-Latin alphabets carry irrecoverable
mojibake.

### 3. Build the training dataset — ~4 min

```bash
.venv/bin/python scripts/03_aggregate.py --min-minutes 20
```

The only cut applied to the corpus is **duration ≥ 20 minutes**. Cutting by track count
feels more natural but eliminates legitimate targets — `n_faixas >= 5` dropped Godspeed You!
Black Emperor's *Lift Your Skinny Fists*, which has 4 tracks and 87 minutes.

Output in `data/train/`:

| file | contents |
|---|---|
| `pairs.parquet` | 49,054,098 rows: `playlist_ix, album_ix, artist_ix, n_tracks, playlist_len` |
| `album_lookup.parquet` | `album_ix, album_id, name, artist_id, n_faixas, minutos, cluster_ix` |
| `artist_lookup.parquet` | `artist_ix, artist_id, name` |
| `playlist_lookup.parquet` | `playlist_ix, playlist_id, name, playlist_len` |

### 4. Train — ~6 h per model on M-series

```bash
.venv/bin/python scripts/04_train.py --level album  --run album_final  \
    --dim 128 --neg 20 --lr 0.001 --epochs 120 --patience 5
.venv/bin/python scripts/04_train.py --level artist --run artist_final \
    --dim 128 --neg 20 --lr 0.001 --epochs 120 --patience 5
```

One *epoch* = each `(playlist, item)` pair serves as an anchor exactly once. Every run
writes to `data/embeddings/runs/<run>/`: `checkpoint.pt` (resumable via `--resume <run>`),
`best.pt`, `history.json` and `<level>_emb.npy`.

Two gotchas worth the trouble:

- **`--lr 0.05` diverges.** The script's default is still 0.05 for historical reasons; pass
  `--lr 0.001`.
- Writing to `data/embeddings/<level>_emb.npy` requires an explicit `--promote`. Without the
  flag no run overwrites another — the serving pipeline reads from `runs/<run>/` anyway.

### 5. Evaluate — ~2 min

```bash
.venv/bin/python scripts/05_eval.py --run album_final
```

Recall@K stratified by seed and target popularity, popularity bias in the output, share of
same-artist neighbors, and a qualitative sample.

Reference results for the trained models:

| | album_final | artist_final |
|---|---|---|
| items | 831,827 | 248,709 |
| Recall@10 | 0.0313 | 0.0631 |
| lift over popularity | **9.38x** | 5.70x |
| Recall@100 | 0.1264 | 0.2325 |
| convergence | early stop at ep. 72 (best: 67) | ~ep. 56 |

Recall@K is the secondary metric, and the reason is recorded in the plan: it is biased
toward the head of the distribution. Under a tail intervention, semantic coherence improved
63% while Recall rose ~13% — the two diverge, and semantics is what matches the goal.

### 6. Build the serving index — ~1 min

```bash
.venv/bin/python scripts/06_build_index.py
```

Joins the lookups with popularity (distinct playlists per album, derived from
`pairs.parquet`) into a single `data/serve/album_index.parquet`.

### 7. Pre-fetch cover art — optional, ~15 h for the full catalog

```bash
.venv/bin/python scripts/07_fetch_covers.py --limit 100000
.venv/bin/python scripts/07_fetch_covers.py              # whole catalog
```

Covers come from Spotify's oEmbed endpoint (no credentials needed) and are cached in
`data/serve/covers.sqlite` on first request anyway — the API resolves misses live, so this
step is a warm-up, not a requirement. It also fixes album names: the source dump is
latin-1, so anything outside that set (e.g. Fishmans' 宇宙 日本 世田谷) was recorded as
`?`, affecting 1.9% of the catalog. The official title comes back correct from oEmbed and
overwrites the local one — but only when it's provably the same string mangled through
latin-1, so legitimate `?` and accents are left alone. Albums are visited by popularity
first, so stopping partway still covers what gets searched most. Rerunning is safe and
idempotent, and is required again after `06_build_index.py`, which regenerates the index
from the dump and brings the `?` back.

### 8. Rebuild the artist lookup — needed for artist photos

```bash
.venv/bin/python scripts/01_extract.py --preset artist-lookup
.venv/bin/python scripts/08_artist_lookup.py
```

`data/train/` is wiped after training (the raw dump and `pairs.parquet` are huge and
disposable once the embeddings are promoted), taking `artist_id` — the Spotify id needed
for artist photos — with it. This replays the relevant parts of step 3 against a fresh,
partial re-extraction (5 tables instead of all 6, via `--preset artist-lookup`) to
regenerate `artist_lookup.parquet` and `data/serve/artist_index.parquet` with `artist_ix`
lined up exactly against the already-trained `artist_emb.npy` — deterministic given the
same dump and the same `--min-minutes` threshold. Popularity is summed from
`album_index.parquet` instead of recomputed, since `pairs.parquet` is gone.

### 9. Pre-fetch artist photos — optional, same trade-off as step 7

```bash
.venv/bin/python scripts/09_fetch_artist_photos.py --limit 50000
.venv/bin/python scripts/09_fetch_artist_photos.py              # whole catalog
```

Same oEmbed strategy and rate limit as step 7, over `/artist/{id}` instead of
`/album/{id}`, cached in `data/serve/artist_photos.sqlite`. Requires
`data/serve/artist_index.parquet` from step 8.

### 10. Publish the data and run the API

The API and the UI are deployed separately: the API runs from the [Dockerfile](Dockerfile)
at the repo root, and the UI is a static build hosted on GitHub Pages, calling the API
cross-origin (CORS is on by default — see `CORS_ORIGINS` below).

`data/embeddings` and `data/serve` (the outputs of steps 4-9) are too large for the git
repo, so they're published as a **GitHub Release asset** instead, and the container
downloads them on first start:

```bash
tar -czf data.tar.gz data/embeddings data/serve   # run from the repo root
gh release create data-v1 data.tar.gz --title "Serving data v1" --notes "album_final + artist_final"
```

The archive must be rooted at `data/` (i.e. `data/embeddings/...`, `data/serve/...`), since
it's extracted relative to the container's working directory. It doesn't need
`data/serve/covers.sqlite` / `artist_photos.sqlite` — those are optional caches the API
rebuilds on demand (see steps 7 and 9) — but including them avoids the oEmbed warm-up cost
on a fresh deploy.

Copy [.env.example](.env.example) to `.env` and set `DATA_URL` to that asset's download
URL (`gh release list` / the release page has it), then:

```bash
docker build -t album-api .
docker run -p 8000:8000 --env-file .env -v album-data:/app/data album-api   # http://127.0.0.1:8000
```

`docker-entrypoint.sh` checks for `data/serve/album_index.parquet` on start; if it's
missing, it downloads `DATA_URL` and extracts it before handing off to uvicorn. `-v
album-data:/app/data` is a named volume, so the download only happens once — subsequent
restarts reuse it. (A bind mount to a local `data/` you already populated via steps 0-9
works too, and skips the download entirely.) `CORS_ORIGINS` restricts which origins may
call the API (comma-separated, e.g. `https://<user>.github.io`); it defaults to `*`, which
is safe here since the API takes no cookies or auth headers.

For local iteration without Docker, run uvicorn directly against the venv from step "Requirements":

```bash
.venv/bin/uvicorn api.app:app --reload   # http://127.0.0.1:8000
```

To build and preview the UI locally against a running API:

```bash
cd front && npm install && npm run dev     # http://localhost:5173, proxies /api to :8000
```

### Deploying the UI to GitHub Pages

[.github/workflows/deploy-pages.yml](.github/workflows/deploy-pages.yml) builds `front/`
and publishes `front/dist` on every push to `main` that touches `front/**` (or manually,
via "Run workflow"). One-time setup:

1. Repo Settings → Pages → **Source: GitHub Actions**.
2. Repo Settings → Secrets and variables → Actions → **Variables** → add `VITE_API_BASE`,
   set to the deployed API's URL (e.g. `https://api.example.com`, no trailing slash). A
   *Variable*, not a *Secret*: it lands verbatim in the public JS bundle either way, so
   there's nothing to protect — see the CORS note above for the matching setting on the
   API side.

The workflow then builds with that value baked in — equivalent to running locally:

```bash
cd front && VITE_API_BASE=https://<api-host> npm run build
```

Two things only matter for the Pages deployment, not local dev:

- `front/vite.config.ts` sets `base: '/album-recommendation-system/'` for `build` (not
  `dev`) — Pages serves a project site under `/<repo>/`, not `/`, and every asset URL
  needs that prefix. `main.tsx` reads it back via `import.meta.env.BASE_URL` as the
  router's `basename`, so client-side links resolve correctly. Renaming the repo means
  updating that one string.
- The workflow copies `dist/index.html` to `dist/404.html` after building. Pages has no
  server-side rewrite for client-side routes, so a direct load or refresh on e.g.
  `/album2vec` would otherwise 404; serving the same shell keeps the requested URL and
  lets react-router take it from there — the same problem `api/app.py`'s `spa_fallback`
  solves for the Docker deployment.

`front/dist` is then the folder to publish to Pages (e.g. via `actions/deploy-pages` in a
workflow, or `gh-pages -d dist`). Left unset, `VITE_API_BASE` defaults to relative `/api`
paths, which only resolve correctly when the UI and API share an origin (e.g. local dev
through the Vite proxy above).

---

## The recommendation

```
seeds S = {s1..sm}
   |-> for EACH seed: top-N by album cosine              N = candidatos_por_seed
              v  union, minus the seeds and the filtered
       score(c) = w_album  * z( sum_s cos(c, s) )
                + w_artist * z( sum_s cos(art(c), art(s)) - dup_penalty * occ(c) )
                + w_pop    * hump( pop(c) )
              v  top-K
```

- `z(.)` is a **per-query** z-score over the candidate set. Without it the weights lie: the
  two cosines have similar means (~0.64) but the artist one has 3.18x the spread, so a
  nominal weight of 0.25 on the artist would carry ~51% of the real influence.
- `occ(c)` counts how many times the artist of `c` already appeared above in the ranking,
  with seed artists counting as one initial occurrence — without it the seed's own
  discography dominates the result, since it scores `cos_artist` = exactly 1.000.
- `hump(p) = exp( -(ln p - ln pico)^2 / (2*largura^2) )` rewards the **middle tail**. A
  monotonic `1/log(pop)` would hand the top to essentially untrained vectors: 31.76% of the
  catalog has fewer than 5 playlists and carries 1.19% of the signal.

The weights live in [config/scoring.toml](config/scoring.toml) and are reloaded when the
file changes, with no server restart. They are not request parameters — the API accepts only
the seeds and the filters.

### Endpoints

| route | purpose |
|---|---|
| `GET /` | web UI — the `front/dist` build, mounted as static files |
| `GET /api/search?q=&limit=` | search albums by artist/title substring, accent-folded |
| `GET /api/artists/search?q=&limit=` | search artists by name substring |
| `POST /api/recommend` | `{seeds[], min_pop, max_pop, exclude_same_artist}` |
| `GET /api/album/{ix}` | one album's record |
| `GET /api/artist/{ix}` | one artist's record |
| `GET /api/covers?ids=` | cover + official title for up to 60 album ids, cached |
| `GET /api/covers/stats` | cache coverage |
| `GET /api/artist_photos?ids=` | photo for up to 60 artist ids, cached |
| `GET /api/artist_photos/stats` | cache coverage |
| `POST /api/album2vec/album` | vector arithmetic (`A - B + C`) over album embeddings |
| `POST /api/artist2vec/artist` | same, over artist embeddings |
| `GET /api/config` | active weights |

Exact search by matmul, no ANN index: 7 ms per query. In batches of 256 the CPU does 0.67
ms/query and beats MPS by 3.3x. Covers and artist photos come from Spotify's public oEmbed
endpoint — no credentials required — resolved on first request and cached in SQLite
thereafter; scripts 07/09 only pre-warm that cache (see steps 7 and 9 above).

---

## Layout

```
scripts/     00 to 09, in the execution order above
api/         app.py (HTTP) · pipeline.py (scoring) · covers.py / artist_photos.py (oEmbed caches)
front/       React + Vite UI, deployed to GitHub Pages — see front/README.md
config/      scoring.toml — weights, hot-reloaded
data/        generated, not versioned; published as a Release asset for step 10
Dockerfile   API image — see step 10
docker-entrypoint.sh   downloads + extracts DATA_URL before uvicorn starts
.env.example           template for DATA_URL / CORS_ORIGINS — copy to .env
.github/workflows/deploy-pages.yml   builds front/ and publishes it to GitHub Pages
```

## Known limitations

- Irrecoverable mojibake at the source affects non-Latin alphabets; name search cannot reach
  them
- `album_lookup` stores only the main artist — credits to collectives do not match by name
- Reissues and remasters are not deduplicated at serving time: `cluster_ix` exists but is
  never applied, so the same album shows up more than once
- Vectors of albums with very few playlists are barely trained and end up
  **indiscriminately close to everything** (mean cosine +0.0240 against random albums,
  versus −0.0146 for popular ones). Multi-seed queries whose seeds sit far apart land in
  that noise; `min_pop` is the correction
- Release year is not displayed: it isn't in the source dump, and Spotify's batch album
  endpoint requires a Premium account — cover art and artist photos avoid this by going
  through the public oEmbed endpoint instead (steps 7 and 9 above)
- The weights in `scoring.toml` were chosen by qualitative inspection, not by a measured
  sweep

---
