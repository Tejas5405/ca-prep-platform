/**
 * Supabase Auth failures, explained to the person who can fix them.
 *
 * WHY THIS RETURNS A STRUCTURE, NOT A SENTENCE
 *
 * Two of the failures a student can hit are configuration on OUR side, and the
 * fix is a value that has to be pasted into the Supabase dashboard:
 *
 *   * an OAuth redirect URL that is not on the project's allow-list, and
 *   * a provider (Google) that has not been enabled on the project at all.
 *
 * A sentence cannot carry that value, so the mapper returns the exact URL, a
 * deep link to the settings page, and a next step. The notice component renders
 * them.
 *
 * THE INVALID-CREDENTIAL RULE STILL HOLDS. Supabase returns the same
 * `invalid_credentials` code for a wrong password and a non-existent account, and
 * the copy preserves that: telling them apart reveals which email addresses are
 * registered.
 *
 * ERROR SHAPES, WHICH ARE NOT UNIFORM. It is worth writing down, because it
 * surprises people:
 *
 *   * `AuthApiError` from the auth endpoints carries `.code` (snake_case, e.g.
 *     `invalid_credentials`) and `.status`.
 *   * A failed OAuth AUTHORIZE redirect comes back as a query/fragment on the
 *     return URL (`?error=access_denied&error_description=...`), not as an
 *     exception — handled by `describeAuthCallbackError`.
 *   * Google's own failures (a `redirect_uri_mismatch`) arrive inside
 *     `error_description` text, so the description is inspected as a fallback.
 */

export type AuthFailureKind =
  | 'redirect_url'
  | 'provider_disabled'
  | 'callback'
  | 'credentials'
  | 'email_confirmation'
  | 'rate_limit'
  | 'network'
  | 'config'
  | 'unknown'

export interface AuthFailure {
  /** Supabase's own code, kept for logs and bug reports. */
  code: string
  /** One line, student-readable. */
  message: string
  /** What to do next, when there is something to do. */
  hint?: string
  kind: AuthFailureKind
  /** A URL (or redirect target) that has to be configured, when that is the issue. */
  value?: string
  /** Link to the dashboard page where the fix lives. */
  settingsUrl?: string
  /** True when the page is inside an iframe (the sandboxed preview). */
  inFrame?: boolean
}

export interface AuthEnvironment {
  origin: string
  redirectUrl: string
  inFrame: boolean
  /** Builds a dashboard URL for this project. Injected so tests need no browser. */
  dashboardUrl?: (path: string) => string
}

export function currentAuthEnvironment(): AuthEnvironment {
  const browser = typeof window !== 'undefined'
  return {
    origin: browser ? window.location.origin : '',
    redirectUrl: browser ? `${window.location.origin}/auth/callback` : '',
    // Cross-origin frame access throws; a sandboxed preview frame reports itself
    // as framed, which is the case worth naming.
    inFrame: browser && window.self !== window.top,
  }
}

/** The settings pages the hints point at. Kept in one place so they cannot drift. */
export const AUTH_URL_CONFIG_PATH = 'auth/url-configuration'
export const AUTH_PROVIDERS_PATH = 'auth/providers'

function codeOf(error: unknown): string {
  const code = (error as { code?: unknown })?.code
  if (typeof code === 'string') return code
  const status = (error as { status?: unknown })?.status
  return typeof status === 'number' ? `status_${status}` : ''
}

function messageOf(error: unknown): string {
  const message = (error as { message?: unknown })?.message
  return typeof message === 'string' ? message : ''
}

const GENERIC: AuthFailure = {
  code: '',
  message: 'Something went wrong. Please try again.',
  kind: 'unknown',
}

export function describeAuthError(
  error: unknown,
  environment: AuthEnvironment = currentAuthEnvironment(),
): AuthFailure {
  const code = codeOf(error)
  const rawMessage = messageOf(error)
  const lower = `${code} ${rawMessage}`.toLowerCase()
  const dashboard = environment.dashboardUrl

  switch (code) {
    case 'invalid_credentials':
      // Supabase merges wrong-password and no-such-user under this one code. The
      // copy must not undo that.
      return {
        code,
        kind: 'credentials',
        message: 'That email and password combination is not correct.',
      }

    case 'email_not_confirmed':
      return {
        code,
        kind: 'email_confirmation',
        message: 'Your email address has not been confirmed yet.',
        hint: 'Open the confirmation link we emailed you, then sign in. If it never arrived, check your spam folder — or ask us to resend it.',
      }

    case 'user_already_exists':
    case 'email_exists':
      return {
        code,
        kind: 'credentials',
        message: 'An account already exists for that email.',
        hint: 'Sign in instead of creating a new account. If you signed up with Google first, use the Google button.',
      }

    case 'weak_password':
      return {
        code,
        kind: 'credentials',
        message: 'That password is too weak.',
        hint: 'Use at least 8 characters, mixing letters and numbers.',
      }

    case 'email_address_invalid':
    case 'validation_failed_email':
      return {
        code,
        kind: 'credentials',
        message: 'That does not look like a valid email address.',
      }

    case 'over_email_send_rate_limit':
    case 'over_request_rate_limit':
      return {
        code,
        kind: 'rate_limit',
        message: 'Too many attempts. Please wait a minute and try again.',
        hint: 'Supabase limits confirmation emails on its built-in mail service. If you see this repeatedly, tell us — production needs a custom mail sender.',
      }

    case 'provider_disabled': {
      /*
       * THE STATE THE PROJECT IS ACTUALLY IN when OAuth has not been turned on.
       * Google is enabled nowhere until the dashboard says so, and the fix
       * involves a Google Cloud OAuth client, so the hint names all three steps
       * rather than just the toggle.
       */
      const steps = [
        'Enable it in the Supabase dashboard under Authentication -> Providers -> Google.',
        'Create an OAuth client in Google Cloud Console and paste its client ID and secret there.',
        'Add this app as an authorised redirect URI in that Google client:',
      ]
      if (environment.inFrame) {
        steps.push('This page is in a preview frame; open it in its own tab to sign in.')
      }
      return {
        code,
        kind: 'provider_disabled',
        value: environment.redirectUrl,
        ...(dashboard ? { settingsUrl: dashboard(AUTH_PROVIDERS_PATH) } : {}),
        inFrame: environment.inFrame,
        message: 'Google sign-in is not enabled for this project yet.',
        hint: steps.join(' '),
      }
    }

    case 'otp_expired':
    case 'email_link_expired':
      /*
       * A confirmation or recovery link that has been used already, or has sat in
       * an inbox past its expiry. It arrives as `error_code` on the return URL, so
       * it never reaches the login form and would otherwise read as "nothing
       * happened".
       */
      return {
        code,
        kind: 'callback',
        message: 'That link has expired, or has already been used.',
        hint: 'Go back to sign in and request a fresh one. An email client that pre-opens links can consume them before you click.',
      }

    case 'bad_oauth_state':
    case 'bad_oauth_callback':
    case 'bad_code_verifier':
      return {
        code,
        kind: 'callback',
        message: 'That sign-in attempt expired or was interrupted.',
        hint: 'Start again and complete the sign-in in one go. Browser privacy extensions that strip URL parameters can cause this too.',
      }

    case 'auth_callback_url_redirect_not_allowed':
    case 'redirect_to_not_allowed':
      return redirectUrlFailure(code, environment, dashboard)

    case 'signup_disabled':
      return {
        code,
        kind: 'config',
        message: 'New account sign-ups are disabled for this project.',
        hint: 'Enable them under Authentication -> Sign In / Providers in the Supabase dashboard.',
      }

    case 'email_provider_disabled':
      return {
        code,
        kind: 'config',
        message: 'Email sign-in is not enabled for this project.',
        hint: 'Enable it under Authentication -> Sign In / Providers in the Supabase dashboard.',
      }

    case 'network_error':
    case 'request_timeout':
    case 'status_0':
    case 'status_502':
    case 'status_503':
    case 'status_504':
      return {
        code,
        kind: 'network',
        message: 'Network problem.',
        hint: 'Check your connection and try again. Campus Wi-Fi and mobile hotspots block Google sign-in more often than students expect.',
      }

    case 'status_401':
    case 'status_403':
      return {
        code,
        kind: 'config',
        message: 'This project rejected the request.',
        hint: 'The publishable key in apps/web/.env.local does not match this Supabase project, or the browser sent a key the Auth API refuses.',
      }

    default:
      break
  }

  // Fallback on the MESSAGE TEXT, because not every failure comes back with a
  // code: the OAuth authorize endpoint reports a rejected redirect target inside
  // the error description, and Google's own errors arrive as prose.
  if (lower.includes('redirect') && (lower.includes('not allowed') || lower.includes('invalid'))) {
    return redirectUrlFailure(code, environment, dashboard)
  }
  if (lower.includes('provider is not enabled') || lower.includes('unsupported provider')) {
    return describeAuthError({ code: 'provider_disabled' }, environment)
  }
  if (lower.includes('redirect_uri_mismatch')) {
    return {
      code,
      kind: 'redirect_url',
      value: environment.redirectUrl,
      message: 'Google rejected the sign-in request.',
      hint: 'The callback URL below has to be listed as an authorised redirect URI on the Google OAuth client used by Supabase.',
    }
  }
  if (lower.includes('invalid login credentials')) {
    return describeAuthError({ code: 'invalid_credentials' }, environment)
  }

  return code ? { ...GENERIC, code } : GENERIC
}

function redirectUrlFailure(
  code: string,
  environment: AuthEnvironment,
  dashboard?: (path: string) => string,
): AuthFailure {
  const steps = [
    'Add the URL below to Authentication -> URL Configuration -> Redirect URLs in the Supabase dashboard.',
    'Set the Site URL there to this app’s origin as well.',
  ]
  if (environment.inFrame) {
    steps.push(
      'This page is inside a preview frame; open it in its own tab, since the OAuth redirect cannot complete inside a sandboxed frame.',
    )
  }
  return {
    code,
    kind: 'redirect_url',
    value: environment.redirectUrl,
    ...(dashboard ? { settingsUrl: dashboard(AUTH_URL_CONFIG_PATH) } : {}),
    inFrame: environment.inFrame,
    message: 'This address is not on the project’s allowed redirect list.',
    hint: steps.join(' '),
  }
}

/**
 * An OAuth failure that came back as URL parameters rather than an exception.
 *
 * Supabase returns the student to `redirect_to` with `error` and
 * `error_description` appended when the provider refuses, and with
 * `error_code=otp_expired` when a magic link has already been used. Neither
 * arrives as a thrown error, so without this the page would silently show a
 * signed-out state and the student would simply try again.
 */
export function describeAuthCallbackError(params: URLSearchParams): AuthFailure | null {
  const error = params.get('error') ?? params.get('error_code')
  if (!error) return null

  const description = params.get('error_description') ?? ''
  const failure = describeAuthError({
    code: params.get('error_code') ?? error,
    message: description,
  })

  // A refused flow is not a credentials problem, however it is worded.
  if (failure.kind === 'unknown' && description) {
    return { ...failure, message: description.replace(/\+/g, ' ') }
  }
  return failure
}
