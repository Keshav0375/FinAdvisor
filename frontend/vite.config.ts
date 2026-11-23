import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import { buildProxy, DEV_PORT, resolveProxyTarget } from "./devServer";

// The dev server and `vite preview` both fall back to index.html for unknown paths (appType "spa"),
// so deep links such as /t/<slug> work. Inside the container the server listens on 0.0.0.0 (see
// the Dockerfile CMD); the proxy target comes from VITE_DEV_PROXY_TARGET.
const proxy = buildProxy(resolveProxyTarget(process.env));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: { port: DEV_PORT, strictPort: true, proxy },
  preview: { port: DEV_PORT, strictPort: true, proxy },
});
