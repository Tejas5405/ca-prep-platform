import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],

  server: {
    // Bind to all interfaces so the dev server is reachable from outside the
    // container (and from the platform's preview proxy). Vite defaults to
    // localhost only, which produces an unreachable preview.
    host: '0.0.0.0',
    port: 5173,

    // Vite 5+ rejects requests whose Host header it does not recognise. The
    // preview proxy serves the app from a generated subdomain, so the allowlist
    // has to include it or every request 403s with "Blocked request".
    allowedHosts: true,

    proxy: {
      // The browser calls /api on its OWN origin and Vite forwards to FastAPI.
      //
      // This matters: the API is on a different origin in production, so a
      // hardcoded absolute URL in the client would either break CORS or force
      // the backend to allow a dev origin in production. Proxying keeps the
      // client origin-relative and gives it same-origin cookies/headers during
      // development.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },

  build: {
    outDir: 'dist',
    // Source maps in production make a stack trace actionable in Sentry. They
    // are uploaded to the error tracker, not served publicly by Vercel.
    sourcemap: true,
  },
})
