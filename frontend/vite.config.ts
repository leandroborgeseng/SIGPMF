/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Versão visível no rodapé do menu: commit (quando o Coolify passa SOURCE_COMMIT) + data/hora do build.
const commit = (process.env.SOURCE_COMMIT ?? '').slice(0, 7)
const quando = new Date().toISOString().slice(0, 16).replace('T', ' ')

export default defineConfig({
  plugins: [react()],
  define: { __VERSAO__: JSON.stringify(commit ? `${commit} · ${quando} UTC` : `${quando} UTC`) },
  server: { proxy: { '/api': process.env.VITE_API_PROXY ?? 'http://localhost:8000' } },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], globals: true },
})
