import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({
  root: 'frontend',
  plugins: [vue()],
  build: { outDir: '../dist', emptyOutDir: true },
  server: {
    port: 5178,
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      // The embedded sector board is served by Python, like the API.
      '/sector-flow': 'http://127.0.0.1:8765',
    },
  },
})
