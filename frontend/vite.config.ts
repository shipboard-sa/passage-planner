/**
 * Purpose: Vite build/dev config for the routing web GUI's React frontend.
 * Version: v3 (2026-09-21) - VITE_API_TARGET override for the dev proxy.
 * Scope: PRODUCTION - passage-planner web entry point.
 * Architecture: passage-planner subsystem. Dev server proxies /api to
 *   backend/main.py on :8100. Production `base: '/routing/'` matches
 *   mx-inv's backend/main.py reverse-proxying /routing/** to this app's own
 *   internal-only :8100 process (see run-live.bat), so built asset URLs
 *   resolve correctly when served from mx-inv's origin instead of this
 *   app's own root.
 */
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev proxy target; override with VITE_API_TARGET to point a dev server at a
// scratch backend (same pattern as mx-inv) without touching the live :8100.
const API_TARGET = process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8100'

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === 'build' ? '/routing/' : '/',
  server: {
    port: 5174,
    proxy: {
      '/api': API_TARGET,
    },
  },
}))
