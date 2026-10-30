import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = { "/api": { target: process.env.FESTOS_API ?? "http://127.0.0.1:8000", ws: true } };

export default defineConfig({
  plugins: [react()],
  server: { port: 3000, strictPort: true, proxy: api },
  preview: { port: 3000, strictPort: true, proxy: api },
});
