// Trusted harness configuration. Generated project code cannot change this file.
import { defineConfig } from 'vite';
export default defineConfig({
  base: './',
  envPrefix: '__HARNESS_PUBLIC_',
  esbuild: { jsx: 'automatic' },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false, target: 'es2022', chunkSizeWarningLimit: 1800,
    modulePreload: false, assetsInlineLimit: 12000000,
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
});
