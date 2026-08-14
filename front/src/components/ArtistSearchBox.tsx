import { useEffect, useRef, useState } from 'react'
import { searchArtists } from '../api'
import type { Artist, PopFilter } from '../api'
import { ArtistPhoto, useArtistPhotos } from '../artistPhotos'
import './SearchBox.css'

const DEBOUNCE_MS = 180
const MIN_CHARS = 2

/** Artist search with a debounced dropdown and keyboard navigation. */
export function ArtistSearchBox({ onPick, pop }: { onPick: (a: Artist) => void; pop?: PopFilter }) {
  const [q, setQ] = useState('')
  const [opts, setOpts] = useState<Artist[]>([])
  const [active, setActive] = useState(-1)
  const [open, setOpen] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  const photos = useArtistPhotos(opts)

  useEffect(() => {
    const term = q.trim()
    if (term.length < MIN_CHARS) return // the input handler already cleared the list
    const ctl = new AbortController()
    const timer = setTimeout(async () => {
      try {
        const { results } = await searchArtists(term, 15, ctl.signal, pop)
        setOpts(results)
        setActive(results.length ? 0 : -1)
        setOpen(true)
      } catch {
        // aborted by the next keystroke, or the backend is down
      }
    }, DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      ctl.abort()
    }
    // re-runs on pop change too: a stale dropdown left over from a wider range would
    // let a click add an artist the filter is meant to keep out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, pop?.min_pop, pop?.max_pop])

  // keep the highlighted option inside the scroll viewport
  useEffect(() => {
    listRef.current?.children[active]?.scrollIntoView({ block: 'nearest' })
  }, [active])

  function onChange(value: string) {
    setQ(value)
    if (value.trim().length < MIN_CHARS) {
      setOpts([])
      setActive(-1)
    }
  }

  function pick(a: Artist) {
    onPick(a)
    setQ('')
    setOpts([])
    setOpen(false)
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (!opts.length) return
    if (e.key === 'ArrowDown') {
      setActive((i) => (i + 1) % opts.length)
      e.preventDefault()
    } else if (e.key === 'ArrowUp') {
      setActive((i) => (i - 1 + opts.length) % opts.length)
      e.preventDefault()
    } else if (e.key === 'Enter' && active >= 0) {
      pick(opts[active])
      e.preventDefault()
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div className="searchbox">
      <input
        type="text"
        value={q}
        placeholder="Search for an artist"
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      />
      {open && opts.length > 0 && (
        // preventDefault keeps focus on the input, so blur never fires before the click
        <div className="dropdown" ref={listRef} onMouseDown={(e) => e.preventDefault()}>
          {opts.map((a, i) => (
            <div
              key={a.artist_ix}
              className={'opt' + (i === active ? ' active' : '')}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(a)}
            >
              <ArtistPhoto hash={photos[a.artist_id]} px={36} />
              <div className="optText">
                {a.artist} <span className="a">— {a.pop} playlists</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
