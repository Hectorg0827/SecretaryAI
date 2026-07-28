import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Fail-closed production guard: a shipped desktop build MUST target a real,
// secure backend. Refuse to build if VITE_API_URL is missing, uses http://,
// or points at localhost / a placeholder host.
function assertProductionApiUrl(command: string, mode: string) {
  const isProdBuild = command === 'build' && mode !== 'development';
  if (!isProdBuild) return;
  const url = process.env.VITE_API_URL ?? '';
  const unsafe =
    !url ||
    /^http:\/\//i.test(url) ||
    /(localhost|127\.0\.0\.1|0\.0\.0\.0|::1|example\.com|placeholder|your-api|changeme)/i.test(url);
  if (unsafe) {
    throw new Error(
      `\n[SecretaryAI] Production build blocked: VITE_API_URL is missing or unsafe ("${url}").\n` +
        `Set VITE_API_URL to a real https:// backend URL before building.\n`,
    );
  }
}

// Tauri expects a fixed dev-server port matching `devUrl` in tauri.conf.json,
// and the production bundle in `dist/` (tauri.conf.json frontendDist: ../dist).
export default defineConfig(({ command, mode }) => {
  assertProductionApiUrl(command, mode);
  return {
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
  };
});
