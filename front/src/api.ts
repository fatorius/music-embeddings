/** Client for the FastAPI backend.
 *
 * In dev, Vite proxies /api to 127.0.0.1:8000. In production the UI is built and
 * hosted separately (GitHub Pages) from the API (the Dockerfile at the repo root),
 * so requests need an absolute base URL — set at build time via VITE_API_BASE, e.g.
 * `VITE_API_BASE=https://api.example.com npm run build`. Left empty, paths stay
 * relative, which is only correct when the API shares the UI's origin.
 */
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export type Album = {
  album_ix: number
  album: string
  artist: string
  album_id: string
  pop: number
  n_faixas: number
  minutos: number
  spotify_url: string
}

/** An Album plus the score breakdown the re-ranking produced. */
export type Result = Album & {
  score: number
  cos_album: number
  cos_artist: number
  /** Desvio-padrão dos cossenos por seed, e o quanto ele custou no score. */
  spread: number
  cons: number
  /** Cosseno ao seed mais distante — o pior caso que o resultado atende. */
  worst: number
  dup: number
  pop_boost: number
  rank_album: number
}

export type Artist = {
  artist_ix: number
  artist: string
  artist_id: string
  pop: number
  spotify_url: string
}

export type Config = {
  k: number
  n_candidates: number
  w_album: number
  w_artist: number
  w_pop: number
  w_consistency: number
  dup_penalty: number
  pop_peak: number
  pop_width: number
  source: string
}

export type Filters = {
  min_pop: number
  max_pop: number | null
  exclude_same_artist: boolean
}

export type PopFilter = {
  min_pop: number
  max_pop: number | null
}

export type RecoResponse = {
  seeds: Album[]
  n_candidates: number
  results: Result[]
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(API_BASE + url, init)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json() as Promise<T>
}

export function search(q: string, limit = 15, signal?: AbortSignal, pop?: PopFilter) {
  let qs = `q=${encodeURIComponent(q)}&limit=${limit}`
  if (pop?.min_pop) qs += `&min_pop=${pop.min_pop}`
  if (pop?.max_pop != null) qs += `&max_pop=${pop.max_pop}`
  return json<{ results: Album[] }>(`/api/search?${qs}`, { signal })
}

export function searchArtists(q: string, limit = 15, signal?: AbortSignal, pop?: PopFilter) {
  let qs = `q=${encodeURIComponent(q)}&limit=${limit}`
  if (pop?.min_pop) qs += `&min_pop=${pop.min_pop}`
  if (pop?.max_pop != null) qs += `&max_pop=${pop.max_pop}`
  return json<{ results: Artist[] }>(`/api/artists/search?${qs}`, { signal })
}

/** Capa e nome oficial vindos do oEmbed do Spotify. `cover` é o hash, não a URL. */
export type Cover = { cover: string; title: string }

export function getCovers(ids: string[], signal?: AbortSignal) {
  const qs = `ids=${encodeURIComponent(ids.join(','))}`
  return json<{ covers: Record<string, Cover> }>(`/api/covers?${qs}`, { signal })
}

/** Hash da foto vinda do oEmbed do Spotify — sem título, ao contrário de getCovers. */
export function getArtistPhotos(ids: string[], signal?: AbortSignal) {
  const qs = `ids=${encodeURIComponent(ids.join(','))}`
  return json<{ photos: Record<string, string> }>(`/api/artist_photos?${qs}`, { signal })
}

export function getAlbum(ix: number) {
  return json<Album>(`/api/album/${ix}`)
}

export function getArtist(ix: number) {
  return json<Artist>(`/api/artist/${ix}`)
}

export function getConfig() {
  return json<Config>('/api/config')
}

export function recommend(seeds: number[], filters: Filters) {
  return json<RecoResponse>('/api/recommend', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ seeds, ...filters }),
  })
}

/** A + B - C * D style vector arithmetic on album embeddings — same trick as king - man + woman. */
export const MAX_ALBUM2VEC_TERMS = 4

export type Album2VecOp = '+' | '-' | '*'

export type Album2VecTerm = { album_ix: number; op: Album2VecOp }

export type Album2VecResponse = {
  terms: (Album & { op: Album2VecOp })[]
  result: (Album & { score: number }) | null
}

export function album2vec(terms: Album2VecTerm[], pop?: PopFilter) {
  return json<Album2VecResponse>('/api/album2vec/album', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ terms, ...pop }),
  })
}

/** Same trick as album2vec, one level up: vector arithmetic on artist embeddings. */
export const MAX_ARTIST2VEC_TERMS = 4

export type Artist2VecOp = '+' | '-' | '*'

export type Artist2VecTerm = { artist_ix: number; op: Artist2VecOp }

export type Artist2VecResponse = {
  terms: (Artist & { op: Artist2VecOp })[]
  result: (Artist & { score: number }) | null
}

export function artist2vec(terms: Artist2VecTerm[], pop?: PopFilter) {
  return json<Artist2VecResponse>('/api/artist2vec/artist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ terms, ...pop }),
  })
}
