# FinAdvisor frontend

Vite + React 19 + TypeScript single-page app (ADR-019, replaces Next.js 14). In production it is
static files served from the same origin as the API; locally the dev server proxies the API so the
browser never needs CORS.

## Commands

```bash
npm ci               # install from package-lock.json (Node >= 20.19)
npm run dev          # Vite dev server on http://localhost:3000
npm run build        # static build into dist/
npm run preview      # serve dist/ on http://localhost:3000
npm run lint         # eslint
npm run typecheck    # tsc --noEmit
npm run test         # vitest (jsdom)
npm run test:coverage
```

## Dev server

- Port: 3000 (`strictPort`; the compose service publishes it as `FRONTEND_HOST_PORT`).
- Deep links such as `/t/<slug>` fall back to `index.html` in `npm run dev` and `npm run preview`.
- `/v1` and `/api` are proxied to `VITE_DEV_PROXY_TARGET` (default `http://localhost:8000`).
  docker-compose sets it to `http://api:8000`. Until the gateway exists (E2), the target is the
  chat API; `src/lib/api.ts` still calls `/api/chat/stream` with the MVP `X-User-Id` header.
- The dev server settings live in `devServer.ts` and `vite.config.ts`.

## Layout

```
index.html          entry page (loads src/main.tsx)
src/main.tsx        React root
src/App.tsx         header + chat window
src/components/     UI components
src/hooks/          useChat, useUser (zustand)
src/lib/            api client, citation parsing
src/assets/fonts/   Geist fonts (@font-face in src/index.css)
public/             static files copied as-is
```
