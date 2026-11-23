// @vitest-environment node
import { mkdtempSync, rmSync } from "node:fs";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";

import { build, createServer, preview, type PreviewServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

// Port 0 picks a free port, so parallel worktrees do not collide.
const listen = { port: 0, strictPort: false, host: "127.0.0.1" };

function baseUrl(server: ViteDevServer | PreviewServer): string {
  return `http://127.0.0.1:${(server.httpServer?.address() as AddressInfo).port}`;
}

async function expectIndexHtml(base: string, route: string) {
  const res = await fetch(`${base}${route}`, { headers: { accept: "text/html" } });
  expect(res.status).toBe(200);
  expect(res.headers.get("content-type")).toContain("text/html");
  const body = await res.text();
  expect(body).toContain('<div id="root"></div>');
  expect(body).toContain("<title>FinAdvisor</title>");
}

describe("SPA fallback for deep links (dev server)", () => {
  let server: ViteDevServer;

  beforeAll(async () => {
    server = await createServer({ server: listen });
    await server.listen();
  });

  afterAll(async () => {
    await server.close();
  });

  it("returns index.html for GET /t/acme", async () => {
    await expectIndexHtml(baseUrl(server), "/t/acme");
  });
});

describe("SPA fallback for deep links (vite preview)", () => {
  let server: PreviewServer;
  let outDir: string;

  beforeAll(async () => {
    outDir = mkdtempSync(path.join(tmpdir(), "fa-preview-"));
    await build({ logLevel: "silent", build: { outDir, emptyOutDir: true } });
    server = await preview({ logLevel: "silent", build: { outDir }, preview: listen });
  }, 60_000);

  afterAll(async () => {
    await server?.close();
    rmSync(outDir, { recursive: true, force: true });
  });

  it("returns index.html for GET /t/acme", async () => {
    await expectIndexHtml(baseUrl(server), "/t/acme");
  });
});
