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
    /**
     * The Supabase config the suite runs against, supplied by the TEST RUNNER.
     *
     * `lib/supabase.ts` throws at module load when these are absent, by design
     * (see the comment there), so any test that transitively imports it needs
     * them. The values used to come from a developer's `apps/web/.env.local`,
     * which is gitignored: the suite therefore passed on the machine that wrote
     * it and failed on a clean clone, and CI discovered this the hard way in run
     * 36349686508. Declaring them here makes the suite self-contained, so it
     * behaves identically on a laptop, in a clean clone and on a CI runner.
     *
     * These are syntactically real so the URL parsing in `lib/supabase.ts` runs
     * the production code path rather than a stub, and they are FAKES. The
     * project ref must not match the real one, and the key must never be a
     * secret: `test_the_built_bundle_carries_the_publishable_key_and_no_secret`
     * asserts no secret reaches the bundle, and the request-mocking tests never
     * leave the process. `supabaseClient.test.ts` still stubs its own values to
     * assert on them directly, and `vi.stubEnv` takes precedence for that file.
     */
    env: {
      VITE_SUPABASE_URL: 'https://test-project.supabase.co',
      VITE_SUPABASE_ANON_KEY: 'sb_publishable_vitest_fake_key',
    },
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/tests/**', 'src/vite-env.d.ts'],
    },
  },
})
