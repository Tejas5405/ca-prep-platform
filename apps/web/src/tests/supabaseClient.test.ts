/**
 * The Supabase client: identity only, and the two settings that fail SILENTLY.
 *
 * WHY THIS FILE EXISTS
 *
 * `lib/supabase.ts` builds a `GoTrueClient` directly instead of calling
 * `createClient` from `@supabase/supabase-js`, because that factory constructs a
 * Realtime websocket client that this app never calls and no bundler can tree-shake.
 * Removing it took the startup chunk from 210 kB to 104 kB.
 *
 * The swap is safe because the options are passed straight through - but two of them
 * are the kind that do not throw when they are wrong:
 *
 *   1. `storageKey`. Get it wrong and every signed-in session is gone on deploy, with
 *      nothing in the console. auth-js defaults to `supabase.auth.token`;
 *      supabase-js computes `sb-<project-ref>-auth-token`. We must keep the latter.
 *   2. `flowType`. Get it wrong and OAuth silently uses the implicit flow, putting
 *      the session in the URL fragment where any script on the page can read it.
 *
 * Both are cheap to assert and impossible to notice otherwise, which is the whole
 * argument for asserting them.
 */

import { describe, expect, it, vi } from 'vitest'

// The module reads import.meta.env at import time and throws when the values are
// missing, so they are set before it is imported. These are the same two variables the
// app requires; the values are syntactically real so the URL parsing exercised below is
// the real code path rather than a stub.
vi.stubEnv('VITE_SUPABASE_URL', 'https://abcdefghijklm.supabase.co')
vi.stubEnv('VITE_SUPABASE_ANON_KEY', 'sb_publishable_test_key')

const { supabase, sessionStorageKey, authRedirectUrl, supabaseDashboardUrl } = await import(
  '../lib/supabase'
)

describe('the auth-only Supabase client', () => {
  it('keeps the storage key supabase-js would have used', () => {
    // Changing this signs every existing session out. It is the single most likely
    // mistake in this file and the least detectable.
    // supabase-js computes this from the hostname: `sb-<ref>-auth-token`, where the
    // ref is everything before the first dot.
    expect(sessionStorageKey).toBe('sb-abcdefghijklm-auth-token')
    expect(sessionStorageKey).not.toBe('supabase.auth.token')
  })

  it('exposes the auth surface through `supabase.auth`, so call sites are unchanged', () => {
    for (const method of [
      'getSession',
      'onAuthStateChange',
      'signInWithPassword',
      'signUp',
      'signInWithOAuth',
      'resetPasswordForEmail',
      'resend',
      'signOut',
    ]) {
      expect(typeof (supabase.auth as unknown as Record<string, unknown>)[method]).toBe('function')
    }
  })

  it('does not construct a realtime or postgrest client', () => {
    // The thing that was removed. If a future edit goes back to `createClient`, these
    // keys appear and the startup chunk doubles again.
    const keys = Object.keys(supabase)
    expect(keys).toEqual(['auth'])
    expect(keys).not.toContain('realtime')
    expect(keys).not.toContain('from')
  })

  it('sends the publishable key as an apikey header and never a secret', () => {
    // `apikey` is how GoTrue identifies the project; the Authorization header carries
    // the same publishable key for the endpoints that expect a bearer token.
    const settings = supabase.auth as unknown as {
      headers: Record<string, string>
      url: string
      flowType: string
      storageKey: string
    }
    expect(settings.headers.apikey).toBe('sb_publishable_test_key')
    expect(JSON.stringify(settings.headers)).not.toMatch(/service_role|secret/i)
    expect(settings.url).toBe('https://abcdefghijklm.supabase.co/auth/v1')
  })

  it('uses PKCE, not the implicit flow', () => {
    const settings = supabase.auth as unknown as { flowType: string }
    expect(settings.flowType).toBe('pkce')
  })

  it('detects the session in the URL, which the OAuth callback depends on', () => {
    const settings = supabase.auth as unknown as {
      detectSessionInUrl: boolean
      persistSession: boolean
      autoRefreshToken: boolean
    }
    expect(settings.detectSessionInUrl).toBe(true)
    expect(settings.persistSession).toBe(true)
    expect(settings.autoRefreshToken).toBe(true)
  })
})

describe('the URLs this module derives', () => {
  it('builds the OAuth redirect the Supabase dashboard must allow', () => {
    // The exact string a deployer has to paste into Authentication -> URL configuration.
    // Getting it from here means the error message and the setting cannot disagree.
    expect(authRedirectUrl()).toBe(`${window.location.origin}/auth/callback`)
  })

  it('points error messages at the right dashboard project', () => {
    expect(supabaseDashboardUrl('settings/auth/providers')).toBe(
      'https://supabase.com/dashboard/project/abcdefghijklm/settings/auth/providers',
    )
  })
})
