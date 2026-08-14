import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// /api is proxied to the Python dev server so the browser makes same-origin
// requests. Nothing about Azure or any key is ever visible to the frontend —
// it only ever talks to its own origin.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.QUIZRANT_API || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
