import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev, Vite serves the frontend on :5173 and proxies /api to the FastAPI
// backend on :8080. In production the frontend is built to ./dist and served
// by FastAPI itself, so there is no proxy and everything is same-origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    // pdf.js bundles fine; keep chunks reasonable.
    chunkSizeWarningLimit: 2000,
  },
})
