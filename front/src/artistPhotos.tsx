import { useEffect, useRef, useState } from 'react'
import { getArtistPhotos } from './api'
import './covers.css'

/** Prefixos que o CDN do Spotify usa para cada resolução da mesma foto de artista.
 * Diferente do prefixo de capa de álbum (ab67616d...) — confirmado manualmente
 * contra o CDN, os três tamanhos abaixo existem para o catálogo. */
const SIZES = {
  sm: 'ab6761610000f178', //  160px
  md: 'ab67616100005174', //  320px
  lg: 'ab6761610000e5eb', //  640px
} as const

export type ArtistPhotoSize = keyof typeof SIZES

export function artistPhotoUrl(hash: string, size: ArtistPhotoSize) {
  return `https://image-cdn-ak.spotifycdn.com/image/${SIZES[size]}${hash}`
}

/**
 * Resolve as fotos de uma lista de artistas num único request.
 *
 * Mesmo padrão de useCovers (covers.tsx): cada artist_id é pedido uma vez por
 * sessão, guardado em `asked`.
 */
export function useArtistPhotos(artists: { artist_id: string }[]) {
  const [map, setMap] = useState<Record<string, string>>({})
  const asked = useRef<Set<string>>(new Set())
  const key = artists.map((a) => a.artist_id).join(',')

  useEffect(() => {
    const need = artists.map((a) => a.artist_id).filter((id) => id && !asked.current.has(id))
    if (!need.length) return
    need.forEach((id) => asked.current.add(id))

    const ctl = new AbortController()
    getArtistPhotos(need, ctl.signal)
      .then(({ photos }) => setMap((m) => ({ ...m, ...photos })))
      .catch(() => {
        // backend fora: libera para uma próxima tentativa
        need.forEach((id) => asked.current.delete(id))
      })
    return () => {
      // liberação síncrona — ver o comentário equivalente em useCovers (covers.tsx)
      // sobre o StrictMode montando duas vezes em dev.
      need.forEach((id) => asked.current.delete(id))
      ctl.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return map
}

/**
 * Miniatura da foto de artista. Reserva o espaço mesmo sem foto, para a lista
 * não pular quando as imagens chegam depois do texto.
 */
export function ArtistPhoto({
  hash,
  size = 'sm',
  px,
  alt = '',
}: {
  hash?: string
  size?: ArtistPhotoSize
  /** Lado em px. Omitido, a foto preenche o container — quem dimensiona é o CSS. */
  px?: number
  alt?: string
}) {
  const style = px ? { width: px, height: px } : undefined
  if (!hash) return <div className="cover cover-empty" style={style} />
  return (
    <img
      className="cover"
      style={style}
      src={artistPhotoUrl(hash, size)}
      alt={alt}
      loading="lazy"
      decoding="async"
      onError={(e) => {
        const img = e.currentTarget
        if (size === 'lg' && !img.dataset.fellBack) {
          img.dataset.fellBack = '1'
          img.src = artistPhotoUrl(hash, 'md')
        }
      }}
    />
  )
}
