// @ts-check
import { defineConfig } from "astro/config";
import tailwindcss from "@tailwindcss/vite";

// Static output served at the root of the Vercel deployment: no base path.
export default defineConfig({
  output: "static",
  vite: {
    plugins: [tailwindcss()],
  },
});
