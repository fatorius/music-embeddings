import type { RecoResponse, Result } from '../api'
import { CoverArt, useCovers } from '../covers'
import './ResultsTable.css'

const nf = new Intl.NumberFormat('pt-BR')

/** How far the re-ranking moved an album from its raw album-cosine position. */
function RankDelta({ r, place }: { r: Result; place: number }) {
  const delta = r.rank_album - place
  if (delta === 0) return null
  return delta > 0 ? (
    <span className="up">↑{delta}</span>
  ) : (
    <span className="down">↓{-delta}</span>
  )
}

export function ResultsTable({
  data,
  perSeed,
  ms,
  onAddSeed,
}: {
  data: RecoResponse
  perSeed: number
  ms: number
  onAddSeed: (ix: number) => void
}) {
  const covers = useCovers(data.results)
  return (
    <div className="resultsTable">
      <table>
        <thead>
          <tr>
            <th style={{ width: 44 }}>#</th>
            <th>álbum</th>
            <th className="num">score</th>
            <th className="num">cos.álb</th>
            <th className="num">cos.art</th>
            <th className="num">corcova</th>
            <th className="num">rank cru</th>
            <th className="num">playlists</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {data.results.map((a, i) => (
            <tr key={a.album_ix}>
              <td className="num">{i + 1}</td>
              <td>
                <div className="albumCell">
                  <a href={a.spotify_url} target="_blank" rel="noopener">
                    <CoverArt hash={covers[a.album_id]?.cover} size="md" px={48} alt={a.album} />
                  </a>
                  <div>
                    <a
                      href={a.spotify_url}
                      target="_blank"
                      rel="noopener"
                      title={covers[a.album_id]?.title || undefined}
                    >
                      {a.album}
                    </a>
                    <div className="artist">
                      {a.artist} · {a.n_faixas} faixas · {a.minutos} min
                    </div>
                  </div>
                </div>
              </td>
              <td className="num">{a.score.toFixed(3)}</td>
              <td className="comp">{a.cos_album.toFixed(3)}</td>
              <td className="comp">
                {a.cos_artist.toFixed(3)}
                {a.dup ? <span className="down"> −{a.dup.toFixed(2)}</span> : null}
              </td>
              <td className="comp">{a.pop_boost.toFixed(2)}</td>
              <td className="comp">
                #{a.rank_album} <RankDelta r={a} place={i + 1} />
              </td>
              <td className="num">{nf.format(a.pop)}</td>
              <td>
                <button className="addseed" onClick={() => onAddSeed(a.album_ix)}>
                  + seed
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="resultsFoot">
        {data.results.length} resultados · {nf.format(data.n_candidates)} candidatos (união de{' '}
        {nf.format(perSeed)} por seed) · {ms} ms · "cos.álb"/"cos.art" = média sobre as seeds ·
        "rank cru" = posição pela soma dos cossenos de álbum, antes do re-ranking
      </div>
    </div>
  )
}
