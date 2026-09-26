/**
 * Supabase client initialisation — identity, and nothing else.
 *
 * ONE PROJECT DOES THREE JOBS. This app uses a single Supabase project for
 * authentication, the PostgreSQL database and file storage. The browser touches
 * ONLY the auth endpoints: it never talks to the database (PostgREST) and never
 * talks to Storage. Data goes through the FastAPI backend, and files are reached
 * through short-lived signed URLs the backend issues.
 *
 * That is not a limitation imposed by this file — it is the authorization design.
 * If the browser held a Supabase client with database access, every future table
 * would need a row-level-security policy correct enough to be the second
 * authorization boundary. Keeping the browser's Supabase client to auth means
 * there is exactly one boundary to get right.
 *
 * WHY THE PUBLISHABLE KEY AND NOT `service_role`
 *
 * `VITE_SUPABASE_ANON_KEY` is the publishable key. Everything in a browser bundle
 * is public: the publishable key identifies the project and is designed to be
 * shipped. The secret/service_role key BYPASSES row-level security and would let
 * anyone read every student's data and every uploaded PDF, so it lives only in the
 * backend's environment. Nothing with a `VITE_` prefix should ever be a secret.
 *
 * The tests in `apps/api/tests/test_infra_contract.py` assert that the frontend
 * bundle contains the publishable key and NOT the secret one.
 *
 * WHY PKCE
 *
 * OAuth in a single-page app without a server-side callback must not use the
 * implicit flow, which returns the session in the URL fragment where any script
 * (or a browser extension) can read it. PKCE exchanges a one-time code instead,
 * which is why `flowType: 'pkce'` is set explicitly rather than left to the
 * library's default.
 */
import { GoTrueClient } from '@supabase/auth-js'

const url = import.meta.env.VITE_SUPABASE_URL
const publishableKey = import.meta.env.VITE_SUPABASE_ANON_KEY

/**
 * Fail loudly at module load if the config is missing.
 *
 * The alternative is a runtime failure on the first sign-in attempt, which
 * presents as "login does not work" with nothing in the console pointing at the
 * cause. Development is where this should be discovered.
 */
const missing = [
  ['VITE_SUPABASE_URL', url],
  ['VITE_SUPABASE_ANON_KEY', publishableKey],
]
  .filter(([, value]) => !value)
  .map(([name]) => name)

if (missing.length > 0) {
  throw new Error(
    `Supabase is not configured. Missing: ${missing.join(', ')}. ` +
      'Copy apps/web/.env.example to apps/web/.env.local and fill in the values ' +
      'from the Supabase dashboard (Project settings -> API keys).',
  )
}

/**
 * WHY THIS IS `GoTrueClient` AND NOT `createClient` FROM supabase-js
 *
 * This app uses Supabase for IDENTITY ONLY. `createClient` returns a bundle of four
 * clients, and it constructs a `RealtimeClient` (websocket) in the constructor that
 * nothing here calls. Because the constructor references it, no bundler can shake it
 * out: the whole front-end was carrying 210 kB of Realtime, PostgREST and Storage
 * client code - the single largest chunk in the build - to call nine auth methods.
 *
 * `GoTrueClient` is the same object supabase-js wraps and exposes as `.auth`, so the
 * call sites (`supabase.auth.signInWithPassword(...)`) are unchanged and the behaviour
 * is identical by construction: supabase-js passes exactly these options through.
 *
 * Two details this has to keep true, because a mistake in either one is silent:
 *
 *   * `storageKey`. supabase-js defaults to `sb-<project-ref>-auth-token`; auth-js
 *     defaults to `supabase.auth.token`. A different key means every signed-in session
 *     disappears on the deploy that makes the switch, with no error anywhere.
 *   * `flowType: 'pkce'`. Without it OAuth falls back to the implicit flow, which puts
 *     the session in the URL fragment where any script on the page can read it.
 *
 * Both are asserted in `src/tests/supabaseClient.test.ts` rather than trusted.
 */
const projectRef = new URL(url).hostname.split('.')[0]

/** The session storage key supabase-js would have used, kept identical on purpose. */
export const sessionStorageKey = `sb-${projectRef}-auth-token`

const auth = new GoTrueClient({
  url: `${url}/auth/v1`,
  headers: { apikey: publishableKey, Authorization: `Bearer ${publishableKey}` },
  storageKey: sessionStorageKey,
  // The session is persisted in localStorage and refreshed in the background, so a
  // student returning the next day is not asked to sign in again.
  persistSession: true,
  autoRefreshToken: true,
  // Required for the OAuth redirect to be picked up on the return leg.
  detectSessionInUrl: true,
  flowType: 'pkce',
})

/**
 * The shape every call site uses: an object with an `auth` property.
 *
 * Kept as a wrapper so `supabase.auth.X` keeps working, which is the whole reason this
 * swap is a one-file change rather than an edit to every screen that signs someone in.
 */
export const supabase = { auth }

/** The dashboard page for this project, used in error messages. */
export function supabaseDashboardUrl(path: string): string {
  const ref = new URL(url).hostname.split('.')[0]
  return `https://supabase.com/dashboard/project/${ref}/${path}`
}

/**
 * Where OAuth should send the student back to.
 *
 * This URL must be listed under Authentication -> URL Configuration -> Redirect
 * URLs, or Supabase refuses the flow with a validation error. It is exported so
 * the error mapper can name the exact value to paste, instead of leaving the
 * reader to guess between the preview host, localhost and production.
 */
export function authRedirectUrl(): string {
  if (typeof window === 'undefined') return ''
  return `${window.location.origin}/auth/callback`
}
