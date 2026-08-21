import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  root: 'frontend',
  build: {
    outDir: import.meta.resolve('../dist').replace('file://', ''),
    emptyOutDir: true,
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8100',
        ws: true,
      },
      '/healthz': 'http://localhost:8100',
    },
  },
})
