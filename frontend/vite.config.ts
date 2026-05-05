import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// NFR-08: browser compat is declared in package.json `browserslist`; Vite picks it up automatically.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5710,
    proxy: {
      // Backend lives on :8710 in dev (chosen to avoid common-port collisions).
      '/api': { target: 'http://localhost:8710', ws: true, changeOrigin: true },
      '/ws': { target: 'ws://localhost:8710', ws: true, changeOrigin: true },
    },
  },
});
