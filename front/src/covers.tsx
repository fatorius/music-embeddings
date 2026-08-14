import { useEffect, useRef, useState } from 'react'
import { getCovers } from './api'
import type { Cover } from './api'
import './covers.css'

/** Prefixos que o CDN do Spotify usa para cada resolução da mesma capa. */
const SIZES = {
  xs: 'ab67616d00004851', //   64px
  sm: 'ab67616d00001e02', //  300px
  md: 'ab67616d0000b273', //  640px — o maior que a Web API oficial documenta
  lg: 'ab67616d000082c1', // 1500px — não documentado, pode não existir
} as const

export type Size = keyof typeof SIZES

export function coverUrl(hash: string, size: Size) {
  return `https://image-cdn-ak.spotifycdn.com/image/${SIZES[size]}${hash}`
}

/**
 * Resolve as capas de uma lista de álbuns num único request.
 *
 * Cada album_id é pedido uma vez por sessão: `asked` guarda o que já saiu daqui,
 * então rolar a lista ou reabrir o dropdown não refaz o trabalho.
 */
export function useCovers(albums: { album_id: string }[]) {
  const [map, setMap] = useState<Record<string, Cover>>({})
  const asked = useRef<Set<string>>(new Set())
  const key = albums.map((a) => a.album_id).join(',')

  useEffect(() => {
    const need = albums.map((a) => a.album_id).filter((id) => id && !asked.current.has(id))
    if (!need.length) return
    need.forEach((id) => asked.current.add(id))

    const ctl = new AbortController()
    getCovers(need, ctl.signal)
      .then(({ covers }) => setMap((m) => ({ ...m, ...covers })))
      .catch(() => {
        // backend fora: libera para uma próxima tentativa
        need.forEach((id) => asked.current.delete(id))
      })
    return () => {
      // A liberação tem de ser síncrona, aqui, e não no catch do abort: o
      // StrictMode monta duas vezes em dev, e a segunda passada avaliaria `need`
      // antes de o catch assíncrono rodar — veria tudo "já pedido", não pediria
      // nada, e as capas nunca chegariam.
      need.forEach((id) => asked.current.delete(id))
      ctl.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return map
}

/**
 * Miniatura da capa. Reserva o espaço mesmo sem capa, para a lista não pular
 * quando as imagens chegam depois do texto.
 */
export function CoverArt({
  hash,
  size = 'sm',
  px,
  alt = '',
}: {
  hash?: string
  size?: Size
  /** Lado em px. Omitido, a capa preenche o container — quem dimensiona é o CSS. */
  px?: number
  alt?: string
}) {
  const style = px ? { width: px, height: px } : undefined
  if (!hash) return <div className="cover cover-empty" style={style} />
  return (
    <img
      className="cover"
      style={style}
      src={coverUrl(hash, size)}
      alt={alt}
      loading="lazy"
      decoding="async"
      // o tamanho 1500 não existe para todo o catálogo; cai para os 640 garantidos
      onError={(e) => {
        const img = e.currentTarget
        if (size === 'lg' && !img.dataset.fellBack) {
          img.dataset.fellBack = '1'
          img.src = coverUrl(hash, 'md')
        }
      }}
    />
  )
}
