import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../static",
    emptyOutDir: true,
  },
  server: {
    // VITE_API_PORT lets parallel worktrees point at their own backend port
    // (default 8000 preserves the single-checkout behavior).
    proxy: {
      "/api": {
        target: `http://localhost:${process.env.VITE_API_PORT ?? 8000}`,
        changeOrigin: true,
      },
    },
  },
});
