import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// In development /api is proxied to API_URL (environment, or ../.env.deploy written for scripts/deploy.sh),
// falling back to a local API. In production the API serves the built app itself, so relative URLs just work.
function apiUrl(mode: string): string {
  const env = loadEnv(mode, process.cwd(), "");
  if (env.API_URL) return env.API_URL;
  const file = fileURLToPath(new URL("../.env.deploy", import.meta.url));
  if (existsSync(file)) {
    const m = readFileSync(file, "utf8").match(/^API_URL=(.+)$/m);
    if (m) return m[1].trim();
  }
  return "http://localhost:8000";
}

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: apiUrl(mode), changeOrigin: true } },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 800 },
}));
