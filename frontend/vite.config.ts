import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  preview: {
    port: 3000,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
