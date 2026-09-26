import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

/**
 * Vitest configuration, kept separate from vite.config.ts.
 *
 * Vite's own `UserConfig` type has no `test` key, so putting the test block in
 * vite.config.ts makes `tsc` fail under a strict config. There are two ways out:
 * import `defineConfig` from 'vitest/config' there, or keep the files separate.
 * Separate is clearer — the build config and the test config have genuinely
 * different concerns (the build needs Tailwind and source maps; the tests need
 * jsdom and a setup file).
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/tests/setup.ts'],
    // Component tests do not need real stylesheets compiled; disabling CSS
    // processing keeps the suite fast.
    css: false,
    include: ['src/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/tests/**', 'src/vite-env.d.ts'],
    },
  },
})
