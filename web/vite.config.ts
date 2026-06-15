import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build into the FastAPI static dir so `opendomainmcp-web` serves the SPA.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/opendomainmcp/api/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
