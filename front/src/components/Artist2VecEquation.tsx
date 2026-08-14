import { useState } from 'react'
import type { Artist, Artist2VecOp } from '../api'
import { ArtistPhoto, useArtistPhotos } from '../artistPhotos'
// Reuses Album2VecEquation's stylesheet: same eq* classes, same visual language —
// the only difference here is one caption line (artist name) instead of two.
import './Album2VecEquation.css'

export type Term = { artist: Artist; op: Artist2VecOp }
export type ResultState = 'empty' | 'loading' | 'none' | { artist: Artist & { score: number } }

/** + → − → × → + ... */
const NEXT_OP: Record<Artist2VecOp, Artist2VecOp> = { '+': '-', '-': '*', '*': '+' }
const OP_GLYPH: Record<Artist2VecOp, string> = { '+': '+', '-': '−', '*': '×' }
const OP_CLASS: Record<Artist2VecOp, string> = { '+': '', '-': ' neg', '*': ' mul' }

/**
 * The whole equation as one row: term, op, term, op, ... = result. Same interaction
 * model as Album2VecEquation (ops cycle + → − → ×, terms drag to reorder) — see that
 * component's docstring for the reasoning. Kept as a separate component rather than
 * a generic one because the payload shape differs (artist_ix vs album_ix, one caption
 * line vs two) enough that a shared abstraction would need as many branches as it saves.
 */
export function Artist2VecEquation({
  terms,
  result,
  onCycleOp,
  onRemove,
  onReorder,
}: {
  terms: Term[]
  result: ResultState
  onCycleOp: (artist_ix: number) => void
  onRemove: (artist_ix: number) => void
  onReorder: (from: number, to: number) => void
}) {
  const photos = useArtistPhotos(terms.map((t) => t.artist))
  const resultArtist = typeof result === 'object' ? result.artist : null
  const resultPhotos = useArtistPhotos(resultArtist ? [resultArtist] : [])

  const [draggedIx, setDraggedIx] = useState<number | null>(null)
  const [overIx, setOverIx] = useState<number | null>(null)

  function handleDrop(i: number) {
    if (draggedIx !== null && draggedIx !== i) onReorder(draggedIx, i)
    setDraggedIx(null)
    setOverIx(null)
  }

  return (
    <div className="eqRow">
      {terms.map(({ artist: a, op }, i) => (
        <div
          className={'eqItem' + (i === draggedIx ? ' dragging' : '') + (i === overIx ? ' dragOver' : '')}
          key={a.artist_ix}
          draggable
          onDragStart={() => setDraggedIx(i)}
          onDragEnd={() => {
            setDraggedIx(null)
            setOverIx(null)
          }}
          onDragOver={(e) => {
            e.preventDefault()
            if (i !== overIx) setOverIx(i)
          }}
          onDrop={(e) => {
            e.preventDefault()
            handleDrop(i)
          }}
        >
          {/* The first term anchors the equation — it's just the starting vector, so
              there's nothing before it to combine with and no op glyph to show. */}
          {i > 0 && (
            <button
              className={'eqSign' + OP_CLASS[op]}
              onClick={() => onCycleOp(a.artist_ix)}
              aria-label={`Cycle op for ${a.artist} (currently ${OP_GLYPH[op]}, next ${OP_GLYPH[NEXT_OP[op]]})`}
            >
              {OP_GLYPH[op]}
            </button>
          )}
          <div className="eqStack">
            <div className="eqCard">
              <ArtistPhoto hash={photos[a.artist_id]} size="md" alt={a.artist} />
              <button className="eqX" onClick={() => onRemove(a.artist_ix)} aria-label={`Remove ${a.artist}`}>
                ×
              </button>
              <div className="eqCap">
                <span className="eqAlbum">{a.artist}</span>
              </div>
            </div>
            <div className="eqLabel" title={a.artist}>
              {a.artist}
            </div>
          </div>
          {i === terms.length - 1 && <span className="eqSign eqEquals">=</span>}
        </div>
      ))}

      {result === 'empty' && <div className="eqCard eqPlaceholder">?</div>}
      {result === 'loading' && <div className="eqCard eqPlaceholder eqPulse">?</div>}
      {result === 'none' && <div className="eqCard eqPlaceholder">∅</div>}
      {typeof result === 'object' && (
        <div className="eqStack">
          <a
            className="eqCard eqResult"
            href={result.artist.spotify_url}
            target="_blank"
            rel="noopener"
            title={`${result.artist.artist} (${result.artist.score.toFixed(3)})`}
          >
            <ArtistPhoto hash={resultPhotos[result.artist.artist_id]} size="md" alt={result.artist.artist} />
            <div className="eqCap">
              <span className="eqAlbum">{result.artist.artist}</span>
            </div>
          </a>
          <div className="eqLabel" title={result.artist.artist}>
            {result.artist.artist}
          </div>
        </div>
      )}
    </div>
  )
}
