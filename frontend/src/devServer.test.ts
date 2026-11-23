// @vitest-environment node
import { describe, expect, it } from "vitest";

import { buildProxy, DEFAULT_PROXY_TARGET, PROXY_PATHS, resolveProxyTarget } from "../devServer";

describe("resolveProxyTarget", () => {
  it("defaults to the documented local backend", () => {
    expect(resolveProxyTarget({})).toBe(DEFAULT_PROXY_TARGET);
    expect(resolveProxyTarget({ VITE_DEV_PROXY_TARGET: "  " })).toBe(DEFAULT_PROXY_TARGET);
  });

  it("takes the target from VITE_DEV_PROXY_TARGET", () => {
    expect(resolveProxyTarget({ VITE_DEV_PROXY_TARGET: "http://api:8000" })).toBe(
      "http://api:8000",
    );
  });

  it("fails fast on a value that is not a URL", () => {
    expect(() => resolveProxyTarget({ VITE_DEV_PROXY_TARGET: "not a url" })).toThrow(
      /not a valid URL/,
    );
  });

  it("rejects non-http schemes", () => {
    expect(() => resolveProxyTarget({ VITE_DEV_PROXY_TARGET: "ftp://api:21" })).toThrow(
      /must be http/,
    );
  });
});

describe("buildProxy", () => {
  it("proxies /v1 and /api to the target", () => {
    const proxy = buildProxy("http://api:8000");
    expect(Object.keys(proxy)).toEqual([...PROXY_PATHS]);
    expect(proxy["/v1"]).toEqual({ target: "http://api:8000", changeOrigin: true });
    expect(proxy["/api"]).toEqual({ target: "http://api:8000", changeOrigin: true });
  });
});
