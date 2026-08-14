import { useEffect, useRef, useState } from 'react'
import type { RecoResponse } from '../api'
import { CoverArt, coverUrl, useCovers } from '../covers'
import './ResultsShow.css'

const nf = new Intl.NumberFormat('pt-BR')

/** Precisa bater com a duração das animações de slide no CSS. */
const SLIDE_MS = 560

const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

/**
 * Os resultados como uma apresentação em tela cheia: um álbum por slide, capa
 * desfocada ao fundo, e uma tela de créditos no final.
 */
export function ResultsShow({
  data,
  perSeed,
  ms,
  onRestart,
}: {
  data: RecoResponse
  perSeed: number
  ms: number
  /** Volta ao início — a tela de créditos é a única saída oferecida. */
  onRestart: () => void
}) {
  const covers = useCovers(data.results)
  // A última posição é a dos créditos, por isso o length e não length - 1.
  const last = data.results.length
  const [ix, setIx] = useState(0)
  // Slide que está saindo: fica montado durante a troca para o de fora deslizar
  // enquanto o de dentro entra. `dir` decide de que lado cada um vem.
  const [prev, setPrev] = useState<number | null>(null)
  const [dir, setDir] = useState<1 | -1>(1)

  function go(step: 1 | -1) {
    // Trocar de slide no meio de uma troca deixaria dois "saindo" ao mesmo tempo
    // e só um deles seria desmontado.
    if (prev !== null) return
    const next = ix + step
    if (next < 0 || next > last) return
    setDir(step)
    setPrev(ix)
    setIx(next)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') go(1)
      if (e.key === 'ArrowLeft') go(-1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  useEffect(() => {
    // A página atrás continua rolável sem isto, e a roda do mouse move um
    // conteúdo que ninguém está vendo.
    const before = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = before
    }
  }, [])

  useEffect(() => {
    if (prev === null) return
    // O animationend do slide entrando seria o gatilho natural, mas ele não
    // dispara se a aba estiver em segundo plano — e aí a troca travaria.
    const t = setTimeout(() => setPrev(null), reduced() ? 0 : SLIDE_MS)
    return () => clearTimeout(t)
  }, [prev])

  const bgHash = ix < last ? covers[data.results[ix].album_id]?.cover : undefined
  const prevHash =
    prev !== null && prev < last ? covers[data.results[prev].album_id]?.cover : undefined

  return (
    <div className="show">
      <div className="showBgs" aria-hidden="true">
        {/* A de baixo é a do slide anterior: as duas juntas fazem o cross-fade,
            sem o piscar que um único elemento trocando de src daria. */}
        {prev !== null && <Bg hash={prevHash} key={`p${prev}`} />}
        <Bg hash={bgHash} key={ix} fading />
      </div>

      <div className="showStage">
        {prev !== null && (
          <Slide
            key={prev}
            ix={prev}
            data={data}
            covers={covers}
            perSeed={perSeed}
            ms={ms}
            onRestart={onRestart}
            cls={dir === 1 ? 'outLeft' : 'outRight'}
          />
        )}
        <Slide
          key={ix}
          ix={ix}
          data={data}
          covers={covers}
          perSeed={perSeed}
          ms={ms}
          onRestart={onRestart}
          cls={prev === null ? 'inFirst' : dir === 1 ? 'inRight' : 'inLeft'}
        />
      </div>

      <button className="showNav prev" onClick={() => go(-1)} disabled={ix === 0} aria-label="Anterior">
        ‹
      </button>
      <button className="showNav next" onClick={() => go(1)} disabled={ix === last} aria-label="Próximo">
        ›
      </button>

      <div className="showDots" aria-hidden="true">
        {Array.from({ length: last + 1 }, (_, i) => (
          <span key={i} className={'dot' + (i === ix ? ' on' : '') + (i === last ? ' end' : '')} />
        ))}
      </div>
    </div>
  )
}

function Bg({ hash, fading }: { hash?: string; fading?: boolean }) {
  return (
    <div
      className={'showBg' + (fading ? ' fade' : '')}
      style={hash ? { backgroundImage: `url(${coverUrl(hash, 'md')})` } : undefined}
    />
  )
}

function Slide({
  ix,
  data,
  covers,
  perSeed,
  ms,
  onRestart,
  cls,
}: {
  ix: number
  data: RecoResponse
  covers: Record<string, { cover: string; title: string }>
  perSeed: number
  ms: number
  onRestart: () => void
  cls: string
}) {
  const el = useRef<HTMLDivElement>(null)

  // Escreve direto no DOM: passar por estado re-renderizaria o slide inteiro a
  // cada pixel do mouse.
  function tilt(e: React.PointerEvent) {
    const node = el.current
    if (!node || reduced()) return
    const r = node.getBoundingClientRect()
    node.style.setProperty('--mx', String(((e.clientX - r.left) / r.width) * 2 - 1))
    node.style.setProperty('--my', String(((e.clientY - r.top) / r.height) * 2 - 1))
  }
  function untilt() {
    el.current?.style.setProperty('--mx', '0')
    el.current?.style.setProperty('--my', '0')
  }

  if (ix === data.results.length) {
    return (
      <div className={'showSlide credits ' + cls} ref={el}>
        <div className="showInfo">
          <p className="showRank">the end</p>
          <h2>Happy listening.</h2>
          <p className="showLine">
            {data.results.length} albums picked out of {nf.format(data.n_candidates)} candidates —
            the union of the {nf.format(perSeed)} nearest neighbours of each of the{' '}
            {data.seeds.length} albums you gave. {ms} ms.
          </p>
          <p className="showLine dim">
            Recommendations come from co-occurrence in public playlists: albums that show up together
            in the same lists end up close in the vector space. Covers and official titles come from
            Spotify's public oEmbed.
          </p>
          <p className="showLine dim">Music Embeddings · Hugo Souza</p>
          <div className="showActions">
            <button className="showLink" onClick={onRestart}>
              Back to start
            </button>
            <a
              className="showLink ghost"
              href="https://github.com/fatorius/music-embeddings"
              target="_blank"
              rel="noopener"
            >
              Read more on GitHub
            </a>
          </div>
        </div>
      </div>
    )
  }

  const a = data.results[ix]
  const cover = covers[a.album_id]
  return (
    <div className={'showSlide ' + cls} ref={el} onPointerMove={tilt} onPointerLeave={untilt}>
      <div className="showCardWrap">
        <div className="showCard">
          <CoverArt hash={cover?.cover} size="lg" alt={a.album} />
          <div className="showGlare" />
        </div>
      </div>
      <div className="showInfo">
        <p className="showRank">#{ix + 1}</p>
        <h2 title={cover?.title || undefined}>{a.album}</h2>
        <p className="showArtist">{a.artist}</p>
        <p className="showLine dim">
          {a.n_faixas} faixas · {a.minutos} min · {nf.format(a.pop)} playlists
        </p>
        <a className="showLink" href={a.spotify_url} target="_blank" rel="noopener">
          Ouvir no Spotify
        </a>
      </div>
    </div>
  )
}
