import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-only proxy so `npm run dev` (port 5173) can call the FastAPI backend
// (port 8000) without CORS friction while iterating on the UI. The
// single-command `./run.sh` path doesn't need this at all -- there the
// backend serves the built frontend itself, same origin, same port.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
