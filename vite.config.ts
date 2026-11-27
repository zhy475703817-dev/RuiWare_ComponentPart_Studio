import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Business routes and credentials belong to FastAPI. Vite only forwards /api.
const proxy = { "/api": { target: "http://127.0.0.1:8020", changeOrigin: true } };

export default defineConfig({
  plugins: [react()],
  cacheDir: ".runtime/vite-cache",
  server: {
    host: "127.0.0.1", port: 5180, strictPort: true, proxy,
    // Runtime databases, logs and browser profiles contain Windows-locked
    // files. Watching them can terminate Vite with an unhandled EBUSY error.
    watch: { ignored: ["**/.runtime/**", "**/.venv/**"] },
  },
  preview: { host: "127.0.0.1", port: 5180, strictPort: true, proxy },
});
