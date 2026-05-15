import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Tauri expects a fixed dev-server port matching `devUrl` in tauri.conf.json,
// and the production bundle in `dist/` (tauri.conf.json frontendDist: ../dist).
export default defineConfig({
  plugins: [react()],
  // Don't let Vite clear the screen — keep Tauri's Rust compiler output visible.
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
  },
  // Expose VITE_* and TAURI_* env vars to the bundled app.
  envPrefix: ['VITE_', 'TAURI_'],
  build: {
    outDir: 'dist',
    sourcemap: false,
    // Tauri targets modern WebViews — skip legacy transpilation.
    target: 'es2021',
    minify: 'esbuild',
  },
});
