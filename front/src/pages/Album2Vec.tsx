import { useState } from 'react'
import { album2vec, MAX_ALBUM2VEC_TERMS } from '../api'
import type { Album, Album2VecOp, Album2VecResponse } from '../api'
import { SearchBox } from '../components/SearchBox'
import { Album2VecEquation } from '../components/Album2VecEquation'
import type { Term, ResultState } from '../components/Album2VecEquation'
import '../App.css'

type Status = { kind: 'idle' | 'loading' } | { kind: 'error'; msg: string }

const NEXT_OP: Record<Album2VecOp, Album2VecOp> = { '+': '-', '-': '*', '*': '+' }

/** Whatever ends up in slot 0 — after a removal or a drag — must stay a plain +. */
function withPlusFirst(terms: Term[]): Term[] {
	if (terms.length === 0 || terms[0].op === '+') return terms
	return [{ ...terms[0], op: '+' }, ...terms.slice(1)]
}

export function Album2Vec() {
	const [terms, setTerms] = useState<Term[]>([])
	const [data, setData] = useState<Album2VecResponse | null>(null)
	const [status, setStatus] = useState<Status>({ kind: 'idle' })

	function addTerm(a: Album) {
		setTerms((prev) => {
			if (prev.length >= MAX_ALBUM2VEC_TERMS || prev.some((t) => t.album.album_ix === a.album_ix)) return prev
			return [...prev, { album: a, op: '+' }]
		})
	}

	function cycleOp(album_ix: number) {
		setTerms((prev) =>
			// the first term stays a plain + — the UI already hides its toggle, this guards
			// against a term shifting into slot 0 after an earlier removal or drag
			prev.map((t, i) => (i > 0 && t.album.album_ix === album_ix ? { ...t, op: NEXT_OP[t.op] } : t)),
		)
	}

	function removeTerm(album_ix: number) {
		setTerms((prev) => withPlusFirst(prev.filter((t) => t.album.album_ix !== album_ix)))
		setData(null)
	}

	function reorderTerms(from: number, to: number) {
		setTerms((prev) => {
			const next = [...prev]
			const [moved] = next.splice(from, 1)
			next.splice(to, 0, moved)
			return withPlusFirst(next)
		})
		setData(null)
	}

	async function run() {
		if (terms.length === 0) return
		setStatus({ kind: 'loading' })
		setData(null)
		try {
			const res = await album2vec(terms.map((t) => ({ album_ix: t.album.album_ix, op: t.op })))
			setData(res)
			setStatus({ kind: 'idle' })
		} catch (e) {
			setStatus({ kind: 'error', msg: e instanceof Error ? e.message : String(e) })
		}
	}

	const result: ResultState =
		status.kind === 'loading' ? 'loading' : data ? (data.result ? { album: data.result } : 'none') : 'empty'

	return (
		<div className="wrap">
			<h1>Album2Vec</h1>
			<p className="sub">Some fun calculation can be made with vectorized albums</p>

			<SearchBox onPick={addTerm} />
			{terms.length > 0 && (
				<Album2VecEquation
					terms={terms}
					result={result}
					onCycleOp={cycleOp}
					onRemove={removeTerm}
					onReorder={reorderTerms}
				/>
			)}

			<div className="controls">
				<button
					className="go"
					disabled={status.kind === 'loading'}
					aria-disabled={terms.length === 0}
					onClick={run}
				>
					Calculate
				</button>
			</div>

			{status.kind === 'error' && <div className="appMsg">error: {status.msg}</div>}
		</div>
	)
}
