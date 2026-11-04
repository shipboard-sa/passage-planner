/**
 * Purpose: React entry point for the passage-planner web UI.
 * Version: v1 (2026-09-17)
 * Scope: PRODUCTION - built to frontend/dist and served by
 *   backend/main.py.
 * Architecture: passage-planner subsystem. Mounts App.tsx, the passage
 *   briefing request form.
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
