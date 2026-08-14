import './EqualizerMark.css'

/** Spotify-style "now playing" bars — a tiny hand-built motion mark, used next to the wordmark. */
export function EqualizerMark() {
	return (
		<svg className="eqMark" viewBox="0 0 14 14" aria-hidden="true" style={{ overflow: 'visible' }}>
			<rect className="bar1" x="0" y="4" width="3" height="10" rx="1" style={{ transformBox: 'fill-box', transformOrigin: 'bottom' }} />
			<rect className="bar2" x="5.5" y="0" width="3" height="14" rx="1" style={{ transformBox: 'fill-box', transformOrigin: 'bottom' }} />
			<rect className="bar3" x="11" y="6" width="3" height="8" rx="1" style={{ transformBox: 'fill-box', transformOrigin: 'bottom' }} />
		</svg>
	)
}
