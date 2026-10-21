import { defineConfig } from "vite";

const API_PORT = Number(process.env["PORT"] ?? 8787);
const API_ORIGIN = `http://127.0.0.1:${API_PORT}`;
const proxy = { "/api": { target: API_ORIGIN } };

export default defineConfig({
  server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true, proxy },
  build: { target: "es2022" },
});
