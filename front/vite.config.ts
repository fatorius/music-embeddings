import { defineConfig } from 'vite'
import react, { reactCompilerPreset } from '@vitejs/plugin-react'
import babel from '@rolldown/plugin-babel'

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [react(), babel({ presets: [reactCompilerPreset()] })],
  // GitHub Pages serves the build as a project site, at /<repo>/ rather than /. Asset
  // URLs and the router basename (see main.tsx) both derive from this — but only for
  // `build`, so `npm run dev` keeps serving at the root.
  base: command === 'build' ? '/music-embeddings/' : '/',
  server: {
    // `npm run dev` serves the UI; the API stays on uvicorn
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
}))
