import './VinylLoader.css'

/**
 * Vinil girando com braço de toca-discos e notas musicais subindo.
 * SVG puro animado por CSS — sem dependência de Lottie.
 */
export function VinylLoader() {
  return (
    <svg viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">
      {/* Sombra sob o disco */}
      <ellipse cx="100" cy="182" rx="58" ry="7" className="vinylShadow" />

      {/* Disco: tudo aqui dentro gira junto */}
      <g className="vinylDisc">
        <circle cx="100" cy="108" r="68" fill="var(--panel)" />
        <circle cx="100" cy="108" r="68" fill="none" stroke="var(--line)" strokeWidth="2" />

        {/* Sulcos */}
        <circle cx="100" cy="108" r="58" fill="none" stroke="var(--line)" strokeWidth="1" />
        <circle cx="100" cy="108" r="50" fill="none" stroke="var(--line)" strokeWidth="1" />
        <circle cx="100" cy="108" r="42" fill="none" stroke="var(--line)" strokeWidth="1" />
        <circle cx="100" cy="108" r="34" fill="none" stroke="var(--line)" strokeWidth="1" />

        {/* Brilho nos sulcos — é o que torna o giro visível */}
        <path
          d="M 100 108 m -54 0 a 54 54 0 0 1 38 -51"
          fill="none"
          stroke="var(--dim)"
          strokeWidth="10"
          strokeLinecap="round"
          opacity="0.28"
        />
        <path
          d="M 100 108 m 54 0 a 54 54 0 0 1 -38 51"
          fill="none"
          stroke="var(--dim)"
          strokeWidth="10"
          strokeLinecap="round"
          opacity="0.28"
        />

        {/* Selo central */}
        <circle cx="100" cy="108" r="22" fill="var(--accent)" />
        <circle cx="100" cy="108" r="22" fill="none" stroke="var(--accent-ink)" strokeOpacity="0.25" strokeWidth="1.5" />
        <circle cx="100" cy="93" r="2.4" fill="var(--accent-ink)" opacity="0.55" />
        <circle cx="100" cy="123" r="2.4" fill="var(--accent-ink)" opacity="0.55" />
        {/* Furo */}
        <circle cx="100" cy="108" r="3.5" fill="var(--bg)" />
      </g>

      {/* Braço do toca-discos, oscilando de leve sobre o disco */}
      <g className="vinylArm">
        <line x1="168" y1="34" x2="140" y2="75" stroke="var(--fg)" strokeWidth="4" strokeLinecap="round" />
        <line x1="140" y1="75" x2="133" y2="84" stroke="var(--fg)" strokeWidth="6" strokeLinecap="round" />
        <circle cx="168" cy="34" r="9" fill="var(--panel)" stroke="var(--fg)" strokeWidth="3" />
      </g>

      {/* Notas musicais subindo do disco */}
      <g className="vinylNote note1" fill="var(--accent)">
        <ellipse cx="34" cy="120" rx="5" ry="3.8" transform="rotate(-20 34 120)" />
        <path d="M 38.5 118.5 V 98 q 6 1 8 6" fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeLinecap="round" />
      </g>
      <g className="vinylNote note2" fill="var(--fg)">
        <ellipse cx="162" cy="130" rx="4.5" ry="3.4" transform="rotate(-20 162 130)" />
        <path d="M 166 128.5 V 110" fill="none" stroke="var(--fg)" strokeWidth="2.5" strokeLinecap="round" />
        <path d="M 166 110 q 7 2 6 9" fill="none" stroke="var(--fg)" strokeWidth="2.5" strokeLinecap="round" />
      </g>
      <g className="vinylNote note3" fill="var(--dim)">
        <ellipse cx="52" cy="70" rx="4" ry="3" transform="rotate(-20 52 70)" />
        <ellipse cx="66" cy="66" rx="4" ry="3" transform="rotate(-20 66 66)" />
        <path d="M 55.5 68.5 V 52 L 69.5 48 V 64.5" fill="none" stroke="var(--dim)" strokeWidth="2.5" strokeLinejoin="round" />
        <path d="M 55.5 52 L 69.5 48" stroke="var(--dim)" strokeWidth="4" strokeLinecap="round" />
      </g>
    </svg>
  )
}
