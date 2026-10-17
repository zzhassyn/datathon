import { createHash } from "node:crypto";
import { resolve } from "node:path";
import tailwind from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "CG_");
  const digest = createHash("sha256").update(resolve("..")).digest();
  const port = 8100 + (digest.readUInt32BE(0) % 100);
  const target = env.CG_API_ORIGIN || `http://127.0.0.1:${port}`;
  return {
    plugins: [react(), tailwind()],
    server: {
      proxy: Object.fromEntries(["/api", "/ready", "/health"].map((path) => [path, { target }])),
    },
  };
});
