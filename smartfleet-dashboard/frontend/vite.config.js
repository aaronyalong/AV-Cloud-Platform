import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Local dev: the SPA calls /api/... on its own origin and Vite forwards it to the FastAPI container.
// In AWS, CloudFront does the same routing (/api/* -> ALB), so the code is identical in both places.
export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 1500 },
  server: { port: 5173, proxy: { '/api': 'http://localhost:8000', '/healthz': 'http://localhost:8000' } },
})
