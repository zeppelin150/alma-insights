import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// The bundle is loaded by a QWebEngineView via file:// (no server). ES modules
// don't load from file:// (CORS), so viteSingleFile inlines the JS/CSS into one
// index.html. qwebchannel.js stays a sibling CLASSIC script (public/) — classic
// scripts load fine from file://. Output lands next to the Python AgentPage.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  base: "./",
  build: {
    outDir: "../src/ui/web/dist",
    emptyOutDir: true,
    cssCodeSplit: false,
    assetsInlineLimit: 100000000,
  },
});
