import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// VITE_BASE lets the dashboard be served from a sub-path (e.g. GitHub Pages /tracelens/)
export default defineConfig({
  base: process.env.VITE_BASE || "/",
  plugins: [react(), tailwindcss()],
  build: { outDir: process.env.VITE_OUT_DIR || "dist", emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  server: { proxy: { "/api": "http://localhost:8000", "/v1": "http://localhost:8000" } },
});
