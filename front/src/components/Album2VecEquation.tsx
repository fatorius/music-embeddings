import { useState } from 'react'
import type { Album, Album2VecOp } from '../api'
import { CoverArt, useCovers } from '../covers'
import './Album2VecEquation.css'

export type Term = { album: Album; op: Album2VecOp }
export type ResultState = 'empty' | 'loading' | 'none' | { album: Album & { score: number } }

/** + → − → × → + ... */
const NEXT_OP: Record<Album2VecOp, Album2VecOp> = { '+': '-', '-': '*', '*': '+' }
const OP_GLYPH: Record<Album2VecOp, string> = { '+': '+', '-': '−', '*': '×' }
const OP_CLASS: Record<Album2VecOp, string> = { '+': '', '-': ' neg', '*': ' mul' }

/**
 * The whole equation as one row: term, op, term, op, ... = result. Ops sit between
 * covers rather than on them, and stay blank (just a big glyph, no chrome) until
 * hovered — the outline only shows up then, so the row reads as plain text with
 * clickable spots rather than a toolbar. Clicking an op cycles + → − → ×, folded
 * left to right with no operator precedence, same as a plain calculator. Terms can
 * be dragged to reorder — plain HTML5 drag and drop, no extra dependency for four
 * draggable covers.
 */
export function Album2VecEquation({
  terms,
  result,
  onCycleOp,
  onRemove,
  onReorder,
}: {
  terms: Term[]
  result: ResultState
  onCycleOp: (album_ix: number) => void
  onRemove: (album_ix: number) => void
  onReorder: (from: number, to: number) => void
}) {
  const covers = useCovers(terms.map((t) => t.album))
  const resultAlbum = typeof result === 'object' ? result.album : null
  const resultCovers = useCovers(resultAlbum ? [resultAlbum] : [])

  const [draggedIx, setDraggedIx] = useState<number | null>(null)
  const [overIx, setOverIx] = useState<number | null>(null)

  function handleDrop(i: number) {
    if (draggedIx !== null && draggedIx !== i) onReorder(draggedIx, i)
    setDraggedIx(null)
    setOverIx(null)
  }

  return (
    <div className="eqRow">
      {terms.map(({ album: a, op }, i) => (
        <div
          className={'eqItem' + (i === draggedIx ? ' dragging' : '') + (i === overIx ? ' dragOver' : '')}
          key={a.album_ix}
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
              onClick={() => onCycleOp(a.album_ix)}
              aria-label={`Cycle op for ${a.album} (currently ${OP_GLYPH[op]}, next ${OP_GLYPH[NEXT_OP[op]]})`}
            >
              {OP_GLYPH[op]}
            </button>
          )}
          <div className="eqStack">
            <div className="eqCard">
              <CoverArt hash={covers[a.album_id]?.cover} size="md" alt={a.album} />
              <button className="eqX" onClick={() => onRemove(a.album_ix)} aria-label={`Remove ${a.album}`}>
                ×
              </button>
              <div className="eqCap">
                <span className="eqAlbum">{a.album}</span>
                <span className="eqArtist">{a.artist}</span>
              </div>
            </div>
            <div className="eqLabel" title={a.album}>
              {a.album}
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
            href={result.album.spotify_url}
            target="_blank"
            rel="noopener"
            title={`${result.album.album} — ${result.album.artist} (${result.album.score.toFixed(3)})`}
          >
            <CoverArt hash={resultCovers[result.album.album_id]?.cover} size="md" alt={result.album.album} />
            <div className="eqCap">
              <span className="eqAlbum">{result.album.album}</span>
              <span className="eqArtist">{result.album.artist}</span>
            </div>
          </a>
          <div className="eqLabel" title={result.album.album}>
            {result.album.album}
          </div>
        </div>
      )}
    </div>
  )
}
