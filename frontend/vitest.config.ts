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
        include: ["src/**/*.{ts,tsx}", "devServer.ts"],
        exclude: ["src/**/*.test.{ts,tsx}", "src/**/*.d.ts"],
        reporter: [["text", { skipFull: false }]],
      },
    },
  }),
);
