import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Same-origin API calls in dev: no CORS, works on whatever port Vite picks.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // Pass the browser's address on (X-Forwarded-For): the login limit counts wrong passwords per address,
        // and without this every client would be 127.0.0.1.
        xfwd: true,
        // Backend not running: answer with a clear 503 instead of Vite's bare 500.
        configure: (proxy) => {
          proxy.on('error', (_err, _req, res) => {
            if (res && 'writeHead' in res && !res.headersSent) {
              res.writeHead(503, { 'Content-Type': 'application/json' });
              res.end(JSON.stringify({ detail: 'Cannot reach the backend server. Is uvicorn running on port 8000?' }));
            }
          });
        },
      },
    },
  },
})
