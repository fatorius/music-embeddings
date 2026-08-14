import type { ReactNode } from 'react'
import './LoadingScreen.css'

/** Piso de exibição: a tela fica no ar pelo menos isto, mesmo se a API responder antes. */
export const MIN_LOADING_MS = 3000

export function LoadingScreen({
  leaving,
  onExited,
  children,
}: {
  leaving: boolean
  /** Chamado quando o slide-down termina — é a deixa para desmontar. */
  onExited: () => void
  /** Animação em SVG; o espaço já fica reservado enquanto ela não existe. */
  children?: ReactNode
}) {
  return (
    <div
      className={'loading' + (leaving ? ' leaving' : '')}
      role="status"
      aria-live="polite"
      onAnimationEnd={(e) => {
        // Só a animação do próprio painel: a do SVG lá dentro também borbulha até
        // aqui e desmontaria a tela no primeiro ciclo dela.
        if (leaving && e.target === e.currentTarget) onExited()
      }}
    >
      <div className="loadingArt" aria-hidden="true">
        {children}
      </div>
      <p className="loadingText">Finding some albums you're gonna love</p>
    </div>
  )
}
