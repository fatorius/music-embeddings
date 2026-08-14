import { NavLink, Route, Routes } from 'react-router-dom'
import { Recommend } from './pages/Recommend'
import { Album2Vec } from './pages/Album2Vec'
import { Artist2Vec } from './pages/Artist2Vec'
import { EqualizerMark } from './components/EqualizerMark'
import { Footer } from './components/Footer'
import './App.css'

function App() {
	return (
		<>
			<header className="siteHeader">
				<NavLink to="/" className="wordmark" aria-label="Music Embeddings — home">
					<EqualizerMark />
					Music Embeddings
				</NavLink>
				<nav className="topnav">
					<NavLink to="/" end className={({ isActive }) => (isActive ? 'active' : '')}>
						Recommend
					</NavLink>
					<NavLink to="/album2vec" className={({ isActive }) => (isActive ? 'active' : '')}>
						Album2Vec
					</NavLink>
					<NavLink to="/artist2vec" className={({ isActive }) => (isActive ? 'active' : '')}>
						Artist2Vec
					</NavLink>
				</nav>
				<a
					className="ghLink"
					href="https://github.com/fatorius/music-embeddings"
					target="_blank"
					rel="noopener"
					aria-label="View source on GitHub"
				>
					GitHub
				</a>
			</header>
			<Routes>
				<Route path="/" element={<Recommend />} />
				<Route path="/album2vec" element={<Album2Vec />} />
				<Route path="/artist2vec" element={<Artist2Vec />} />
			</Routes>
			<Footer />
		</>
	)
}

export default App
