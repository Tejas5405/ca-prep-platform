/**
 * Tests for the Supabase error describer.
 *
 * These matter because the failures they exist for are invisible from inside the
 * app. An OAuth redirect target that is not on the project's allow-list, or a
 * provider that was never enabled, is raised by Supabase; the app can only report
 * it, and the report is useless unless it prints the exact URL to paste. So the
 * assertions below are about the CONTENT of the notice — the redirect URL, the
 * dashboard link, the in-frame warning — and not about the presence of an error.
 */

import { describe, expect, it } from 'vitest'

import {
  AUTH_PROVIDERS_PATH,
  AUTH_URL_CONFIG_PATH,
  describeAuthCallbackError,
  describeAuthError,
  type AuthEnvironment,
} from '../lib/authErrors'

const ref = 'zyrmlnpvylhcpyaoizyz'
const dashboardUrl = (path: string) => `https://supabase.com/dashboard/project/${ref}/${path}`

const DESKTOP: AuthEnvironment = {
  origin: 'https://caprep.example',
  redirectUrl: 'https://caprep.example/auth/callback',
  inFrame: false,
  dashboardUrl,
}

/** The workspace preview: a proxy host inside a sandboxed iframe. */
const PREVIEW: AuthEnvironment = {
  origin: 'https://5173-ikskil4vrmambyz28d9ij.e2b.app',
  redirectUrl: 'https://5173-ikskil4vrmambyz28d9ij.e2b.app/auth/callback',
  inFrame: true,
  dashboardUrl,
}

function supabaseError(code: string, message = ''): Error & { code: string } {
  return Object.assign(new Error(message || code), { code })
}

describe('describeAuthError', () => {
  it('prints the exact redirect URL to allow-list, and links to the settings page', () => {
    const failure = describeAuthError(supabaseError('redirect_to_not_allowed'), DESKTOP)

    expect(failure.kind).toBe('redirect_url')
    // The value, not the hostname: the redirect target includes the path, and the
    // allow-list entry has to match it.
    expect(failure.value).toBe('https://caprep.example/auth/callback')
    expect(failure.hint).toContain('Redirect URLs')
    expect(failure.settingsUrl).toBe(dashboardUrl(AUTH_URL_CONFIG_PATH))
  })

  it('warns that a sandboxed preview frame cannot complete an OAuth redirect', () => {
    const failure = describeAuthError(supabaseError('redirect_to_not_allowed'), PREVIEW)

    expect(failure.inFrame).toBe(true)
    // Both halves of the fix are present: the URL to allow-list (rendered in its own
    // copyable box, hence asserted on `value` rather than inside the prose), AND the
    // fact that a sandboxed frame blocks the redirect even once the URL is listed.
    expect(failure.value).toBe('https://5173-ikskil4vrmambyz28d9ij.e2b.app/auth/callback')
    expect(failure.hint).toContain('own tab')
  })

  it('does not distinguish a wrong password from a missing account', () => {
    // Supabase merges these on purpose under `invalid_credentials`. The copy must
    // not undo that: telling them apart reveals which emails are registered.
    const failure = describeAuthError(supabaseError('invalid_credentials'))

    expect(failure.kind).toBe('credentials')
    expect(failure.message).toMatch(/email and password/i)
    // One message for both cases, and no "no account found" style wording.
    expect(failure.message).not.toMatch(/no account|not found|does not exist/i)
  })

  it('also merges them when only the message text reveals the case', () => {
    const failure = describeAuthError(
      Object.assign(new Error('Invalid login credentials'), { code: 'unknown_code' }),
    )
    expect(failure.kind).toBe('credentials')
    expect(failure.message).toBe('That email and password combination is not correct.')
  })

  it('explains unmistakeably that Google is not enabled yet, and where to enable it', () => {
    // THE STATE THIS PROJECT IS ACTUALLY IN. Google is enabled nowhere until the
    // dashboard says so, and the fix needs a Google Cloud OAuth client, so the hint
    // must name more than a toggle.
    const failure = describeAuthError(supabaseError('provider_disabled'), DESKTOP)

    expect(failure.kind).toBe('provider_disabled')
    expect(failure.message).toMatch(/google/i)
    expect(failure.hint).toContain('Providers')
    expect(failure.hint).toContain('Google Cloud Console')
    expect(failure.value).toBe('https://caprep.example/auth/callback')
    expect(failure.settingsUrl).toBe(dashboardUrl(AUTH_PROVIDERS_PATH))
  })

  it('recognises a disabled provider from its message when there is no code', () => {
    const failure = describeAuthError(
      Object.assign(new Error('Unsupported provider: provider is not enabled'), { code: '' }),
    )
    expect(failure.kind).toBe('provider_disabled')
  })

  it('says so when the email address has not been confirmed', () => {
    const failure = describeAuthError(supabaseError('email_not_confirmed'))
    expect(failure.kind).toBe('email_confirmation')
    expect(failure.hint).toMatch(/confirmation link/i)
  })

  it('explains an interrupted or expired OAuth leg', () => {
    const failure = describeAuthError(supabaseError('bad_code_verifier'))
    expect(failure.kind).toBe('callback')
    expect(failure.hint).toContain('Start again')
  })

  it('calls out a bad build config instead of blaming the user', () => {
    const failure = describeAuthError(supabaseError('status_401'))
    expect(failure.kind).toBe('config')
    expect(failure.hint).toContain('.env.local')
  })

  it('tells an operator that a rate limit is the built-in mail sender, not the student', () => {
    const failure = describeAuthError(supabaseError('over_email_send_rate_limit'))
    expect(failure.kind).toBe('rate_limit')
    expect(failure.hint).toMatch(/built-in mail service/i)
  })

  it('treats a network failure as a network failure', () => {
    const failure = describeAuthError(supabaseError('network_error'))
    expect(failure.kind).toBe('network')
    expect(failure.hint).toContain('connection')
  })

  it('names the Google-side redirect mismatch, which is a different fix entirely', () => {
    const failure = describeAuthError(
      Object.assign(new Error('redirect_uri_mismatch'), { code: 'unknown' }),
      DESKTOP,
    )
    expect(failure.kind).toBe('redirect_url')
    expect(failure.hint).toContain('authorised redirect URI')
    expect(failure.value).toBe('https://caprep.example/auth/callback')
  })

  it('keeps the code for logs and still says something for an unknown one', () => {
    const failure = describeAuthError(supabaseError('some_new_code'))
    expect(failure.code).toBe('some_new_code')
    expect(failure.message).toBe('Something went wrong. Please try again.')
    expect(failure.kind).toBe('unknown')
  })

  it('survives something that is not a Supabase error at all', () => {
    // A thrown string, a rejected non-Error, a TypeError from our own code: the
    // sign-in screen must not be the place that crashes.
    for (const thrown of [new Error('boom'), 'boom', null, undefined, 42, {}]) {
      const failure = describeAuthError(thrown, DESKTOP)
      expect(failure.message).toBeTruthy()
      expect(failure.kind).toBe('unknown')
    }
  })
})

describe('describeAuthCallbackError', () => {
  /*
   * The failures that arrive as URL PARAMETERS rather than exceptions. Supabase
   * returns the student to the redirect target with these appended, so without
   * this mapper the page would quietly show the signed-out state and the student
   * would retry a flow that cannot work.
   */
  it('reports a refused consent screen', () => {
    const failure = describeAuthCallbackError(
      new URLSearchParams('error=access_denied&error_description=User+denied+the+request'),
    )
    expect(failure).not.toBeNull()
    expect(failure?.message).toBeTruthy()
  })

  it('reports an expired or already-used link from its error_code', () => {
    const failure = describeAuthCallbackError(
      new URLSearchParams(
        'error=invalid_request&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired',
      ),
    )
    expect(failure).not.toBeNull()
    // `callback`, not `credentials`: no password was involved, and the fix is to
    // request a new link rather than to retype anything.
    expect(failure?.kind).toBe('callback')
    expect(failure?.message).toMatch(/expired/i)
  })

  it('returns null for a clean return leg, so nothing is shown on success', () => {
    expect(describeAuthCallbackError(new URLSearchParams('code=abc123'))).toBeNull()
    expect(describeAuthCallbackError(new URLSearchParams(''))).toBeNull()
  })
})
