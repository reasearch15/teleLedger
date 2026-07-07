import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    env: {
      NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000",
    },
    setupFiles: ["./tests/setup.ts"],
  },
});
