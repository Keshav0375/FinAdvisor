import { cleanup, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

describe("main.tsx entry", () => {
  beforeAll(() => {
    // jsdom has no layout engine, so it does not implement scrollIntoView.
    window.HTMLElement.prototype.scrollIntoView = () => {};
  });

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    cleanup();
    document.body.innerHTML = "";
  });

  it("mounts the app into #root", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    await import("@/main");
    await waitFor(() => expect(screen.getByRole("heading", { name: "FinAdvisor" })).toBeTruthy());
    expect(document.getElementById("root")?.children.length).toBeGreaterThan(0);
  });

  it("fails fast when index.html has no #root element", async () => {
    document.body.innerHTML = "";
    await expect(import("@/main")).rejects.toThrow("Missing #root element in index.html");
  });
});
