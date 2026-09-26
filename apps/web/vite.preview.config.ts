import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

/**
 * Build config for the static landing-page preview.
 *
 * Separate from vite.config.ts because the output is a different artefact with
 * different requirements: relative asset paths (so the file opens from disk) and
 * no dev proxy. `npm run preview:static` builds it and inlines everything into
 * one HTML file.
 */
export default defineConfig({
  /*
   * root is deliberately NOT `preview`.
   *
   * Setting root to the preview directory made Vite emit the HTML exactly where
   * this script wanted it, and silently broke the stylesheet: Tailwind v4 detects
   * its source files relative to the project root, so with root moved the
   * generated CSS collapsed from 28.9 kB to 5.8 kB - preflight only, none of the
   * utilities the page uses. The build still succeeded, so nothing surfaced it.
   * The cost of leaving root alone is that the emitted HTML sits under a nested
   * `preview/` directory, which the inliner resolves by searching.
   */
  base: './',
  publicDir: false,
  plugins: [react(), tailwindcss()],
  build: {
    outDir: 'dist-preview',
    emptyOutDir: true,
    // No code splitting: every asset gets inlined into one HTML document, and a
    // second chunk would be a dangling reference in the output file.
    rollupOptions: { input: 'preview/index.html' },
    cssCodeSplit: false,
  },
})
