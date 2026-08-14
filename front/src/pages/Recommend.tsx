import { useEffect, useState } from 'react'
import { getConfig, recommend } from '../api'
import type { Album, Config, RecoResponse } from '../api'
import { SearchBox } from '../components/SearchBox'
import { SeedGrid, MIN_SEEDS, MAX_SEEDS } from '../components/SeedGrid'
import { ResultsShow } from '../components/ResultsShow'
import { LoadingScreen, MIN_LOADING_MS } from '../components/LoadingScreen'
import { VinylLoader } from '../components/VinylLoader'
import '../App.css'

type Status = { kind: 'idle' | 'loading' } | { kind: 'error'; msg: string }

export function Recommend() {
	const [seeds, setSeeds] = useState<Album[]>([])

	const [cfg, setCfg] = useState<Config | null>(null)
	const [data, setData] = useState<RecoResponse | null>(null)
	const [ms, setMs] = useState(0)
	const [status, setStatus] = useState<Status>({ kind: 'idle' })
	// Só depois de o usuário tentar: cobrar o mínimo antes disso seria repreender
	// alguém que ainda está escolhendo.
	const [tooFew, setTooFew] = useState(false)
	// 'out' é a saída ainda animando: a tela continua montada até o slide-down acabar.
	const [overlay, setOverlay] = useState<'off' | 'in' | 'out'>('off')

	useEffect(() => {
		getConfig()
			.then(setCfg)
			.catch(() => setStatus({ kind: 'error', msg: 'API indisponível' }))
	}, [])

	function addSeed(a: Album) {
		setSeeds((prev) => {
			// o teto é o da grade: um seed além dela ficaria invisível e ainda assim pesaria
			if (prev.length >= MAX_SEEDS || prev.some((s) => s.album_ix === a.album_ix)) return prev
			return [...prev, a]
		})
	}

	function removeSeed(album_ix: number) {
		setSeeds((prev) => prev.filter((s) => s.album_ix !== album_ix))
	}

	async function run() {
		if (seeds.length < MIN_SEEDS) {
			setTooFew(true)
			return
		}
		setStatus({ kind: 'loading' })
		setData(null)
		setOverlay('in')
		// Começa a contar no clique, não depois da resposta: a tela fica no ar por
		// MIN_LOADING_MS ou pelo tempo da rede, o que for maior.
		const floor = new Promise((r) => setTimeout(r, MIN_LOADING_MS))
		try {
			// the weights file may have changed between queries; the API hot-reloads it
			setCfg(await getConfig())
			const t0 = performance.now()
			const res = await recommend(
				seeds.map((s) => s.album_ix),
				{
					min_pop: 0,
					max_pop: null,
					exclude_same_artist: false,
				},
			)
			setMs(Math.round(performance.now() - t0))
			await floor
			// Antes do slide-down: a tabela já está montada atrás, e a saída a revela.
			setData(res)
			setStatus({ kind: 'idle' })
		} catch (e) {
			await floor
			setStatus({ kind: 'error', msg: e instanceof Error ? e.message : String(e) })
		} finally {
			setOverlay('out')
		}
	}

	return (
		<div className="wrap">
			<h1>Recommend</h1>
			<p className="sub">Tell me your tastes, I'll recommend you an album.</p>

			<p>Pick 3-10 albums you like</p>
			{/* Fica junto da busca, e não no rodapé: quem procura um lançamento recente
			    descobre o limite no momento em que a busca não devolve nada. */}
			<p className="cutoff">Catalogue stops at 2018 — nothing released after that is in here.</p>
			<SearchBox onPick={addSeed} />
			<SeedGrid seeds={seeds} onRemove={removeSeed} />

			<div className="controls">
				{/* aria-disabled, e não disabled: abaixo do mínimo o botão precisa parecer
				    apagado mas ainda receber o clique, senão não há como explicar o porquê */}
				<button
					className="go"
					disabled={status.kind === 'loading'}
					aria-disabled={seeds.length < MIN_SEEDS}
					onClick={run}
				>
					Recommend
				</button>
			</div>

			{/* some sozinho quando o terceiro álbum entra, sem precisar de outro clique */}
			{tooFew && seeds.length < MIN_SEEDS && (
				<div className="appMsg warn" role="alert">
					Escolha pelo menos {MIN_SEEDS} álbuns — faltam {MIN_SEEDS - seeds.length}.
				</div>
			)}
			{status.kind === 'error' && <div className="appMsg">erro: {status.msg}</div>}
			{data && data.results.length === 0 && (
				<div className="appMsg">nenhum resultado com esses filtros</div>
			)}
			{data && data.results.length > 0 && (
				<ResultsShow
					data={data}
					perSeed={cfg?.n_candidates ?? 0}
					ms={ms}
					// Os seeds ficam: quem voltou ao início quase sempre quer ajustar a
					// escolha, não refazê-la do zero.
					onRestart={() => setData(null)}
				/>
			)}

			{overlay !== 'off' && (
				<LoadingScreen leaving={overlay === 'out'} onExited={() => setOverlay('off')}>
					<VinylLoader />
				</LoadingScreen>
			)}
		</div>
	)
}
