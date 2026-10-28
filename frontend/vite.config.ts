import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': { target: process.env.WALNUT_API || 'http://127.0.0.1:8001', changeOrigin: false } } },
  build: { outDir: 'dist', sourcemap: false }
})
