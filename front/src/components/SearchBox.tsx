import { useEffect, useRef, useState } from 'react'
import { search } from '../api'
import type { Album, PopFilter } from '../api'
import { CoverArt, useCovers } from '../covers'
import './SearchBox.css'

const DEBOUNCE_MS = 180
const MIN_CHARS = 2

/** Album search with a debounced dropdown and keyboard navigation. */
export function SearchBox({ onPick, pop }: { onPick: (a: Album) => void; pop?: PopFilter }) {
  const [q, setQ] = useState('')
  const [opts, setOpts] = useState<Album[]>([])
  const [active, setActive] = useState(-1)
  const [open, setOpen] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  const covers = useCovers(opts)

  useEffect(() => {
    const term = q.trim()
    if (term.length < MIN_CHARS) return // the input handler already cleared the list
    const ctl = new AbortController()
    const timer = setTimeout(async () => {
      try {
        const { results } = await search(term, 15, ctl.signal, pop)
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
    // let a click add an album the filter is meant to keep out
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

  function pick(a: Album) {
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
        placeholder="Search for an album or artist"
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
              key={a.album_ix}
              className={'opt' + (i === active ? ' active' : '')}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(a)}
              // o nome oficial do Spotify costuma trazer "(Remastered)", edição etc.
              title={covers[a.album_id]?.title || undefined}
            >
              <CoverArt hash={covers[a.album_id]?.cover} px={36} />
              <div className="optText">
                {a.album}{' '}
                <span className="a">
                  — {a.artist} · {a.pop} playlists
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
