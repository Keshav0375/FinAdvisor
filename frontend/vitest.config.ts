import { defineConfig, mergeConfig } from "vitest/config";

import viteConfig from "./vite.config";

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: "jsdom",
      include: ["src/**/*.test.{ts,tsx}"],
      coverage: {
        provider: "v8",
        include: ["src/lib/**/*.ts"],
        exclude: ["src/**/*.test.ts"],
        reporter: [["text", { skipFull: false }]],
      },
    },
  }),
);
