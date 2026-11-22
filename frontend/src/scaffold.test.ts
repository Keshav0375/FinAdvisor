// @vitest-environment node
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const root = path.resolve(__dirname, "..");

function readPackageJson(): Record<string, Record<string, string> | undefined> {
  return JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
}

describe("Vite scaffold (E8-T1)", () => {
  it("has no next or eslint-config-next dependency in any section", () => {
    const pkg = readPackageJson();
    for (const section of [
      "dependencies",
      "devDependencies",
      "peerDependencies",
      "optionalDependencies",
    ]) {
      const names = Object.keys(pkg[section] ?? {});
      expect(names, section).not.toContain("next");
      expect(names, section).not.toContain("eslint-config-next");
    }
  });

  it("has no next.config file or next-env.d.ts", () => {
    for (const file of ["next.config.mjs", "next.config.js", "next.config.ts", "next-env.d.ts"]) {
      expect(existsSync(path.join(root, file)), file).toBe(false);
    }
  });

  it("pins vite, react and typescript exactly", () => {
    const pkg = readPackageJson();
    const pinned = {
      vite: pkg.devDependencies?.vite,
      "@vitejs/plugin-react": pkg.devDependencies?.["@vitejs/plugin-react"],
      typescript: pkg.devDependencies?.typescript,
      react: pkg.dependencies?.react,
      "react-dom": pkg.dependencies?.["react-dom"],
    };
    for (const [name, version] of Object.entries(pinned)) {
      expect(version, name).toMatch(/^\d+\.\d+\.\d+$/);
    }
    expect(pinned.react).toMatch(/^19\./);
  });

  it("serves a single-page entry point from index.html", () => {
    const html = readFileSync(path.join(root, "index.html"), "utf8");
    expect(html).toContain('<div id="root"></div>');
    expect(html).toContain('src="/src/main.tsx"');
  });
});
