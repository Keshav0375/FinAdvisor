// Dev-server knobs, kept in one place. Read by vite.config.ts only; nothing here reaches the browser bundle.

export const DEFAULT_PROXY_TARGET = "http://localhost:8000";
export const DEV_PORT = 3000;
// Paths the SPA proxies so it stays same-origin: /v1 is the gateway API, /api is the MVP chat route
// that src/lib/api.ts still calls until E8-T3.
export const PROXY_PATHS = ["/v1", "/api"] as const;

export function resolveProxyTarget(env: Record<string, string | undefined>): string {
  const raw = env.VITE_DEV_PROXY_TARGET?.trim() || DEFAULT_PROXY_TARGET;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`VITE_DEV_PROXY_TARGET is not a valid URL: ${raw}`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(`VITE_DEV_PROXY_TARGET must be http(s), got: ${raw}`);
  }
  return raw;
}

export function buildProxy(target: string) {
  return Object.fromEntries(PROXY_PATHS.map((path) => [path, { target, changeOrigin: true }]));
}
