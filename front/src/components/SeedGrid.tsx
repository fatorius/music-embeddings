import type { Album } from '../api'
import { CoverArt, useCovers } from '../covers'
import './SeedGrid.css'

/** Abaixo disso a recomendação sai enviesada pelo único álbum escolhido. */
export const MIN_SEEDS = 3
/** A grade tem 5 colunas e cresce até 2 linhas — daí o teto. */
export const MAX_SEEDS = 10

export function SeedGrid({
  seeds,
  onRemove,
}: {
  seeds: Album[]
  onRemove: (album_ix: number) => void
}) {
  const covers = useCovers(seeds)

  return (
    <div className="seedGrid">
      {seeds.map((a) => (
        <div className="seed" key={a.album_ix}>
          <div className="seedCover">
            <CoverArt hash={covers[a.album_id]?.cover} size="md" alt={a.album} />
            <button
              className="seedX"
              onClick={() => onRemove(a.album_ix)}
              aria-label={`Remover ${a.album}`}
            >
              ×
            </button>
            <div className="seedCap">
              <span className="seedAlbum">{a.album}</span>
              <span className="seedArtist">{a.artist}</span>
            </div>
          </div>
          {/* Sempre visível — mesmo padrão do .eqLabel em Album2Vec/Artist2Vec,
              a legenda de hover acima (.seedCap) continua com o detalhe completo. */}
          <div className="seedLabel" title={a.album}>
            {a.album}
          </div>
        </div>
      ))}
    </div>
  )
}
