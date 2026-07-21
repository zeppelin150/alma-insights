import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Separate from vite.config.js so viteSingleFile (a build-only concern) never
// runs in test mode. Tests render with react-dom/server (no DOM), so the
// plain node environment suffices — no jsdom dependency.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "node",
    include: ["src/**/*.test.{js,jsx}"],
  },
});
