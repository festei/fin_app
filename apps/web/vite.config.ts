/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The browser never holds the API write token. In development this proxy adds it server-side
// (set API_TOKEN to match the API; the dev compose default is "dev-token"). In production nginx
// does the same (deploy/nginx.conf).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", ""); // all variables, not only VITE_-prefixed ones
  const target = env.API_URL || "http://localhost:8000";
  const authorization = `Bearer ${env.API_TOKEN || "dev-token"}`;
  return {
    plugins: [react()],
    server: {
      proxy: {
        "/api": { target, changeOrigin: true, headers: { Authorization: authorization } },
        "/healthz": { target },
      },
    },
    test: { environment: "jsdom", setupFiles: ["./src/test/setup.tsx"], css: false },
  };
});
