/**
 * The OAuth return leg.
 *
 * WHY THIS PAGE EXISTS RATHER THAN LANDING ON THE DASHBOARD
 *
 * Supabase redirects back with `?code=...` in PKCE mode, and the client library
 * exchanges it for a session when the page loads (`detectSessionInUrl`). That
 * exchange is asynchronous, so a redirect straight to `/dashboard` renders the
 * route guard BEFORE the session exists — and the guard, quite correctly, bounces
 * the student to `/login`. They then sign in again, which is the second time they
 * have done it, and the account looks broken.
 *
 * So the callback is its own page: it waits for the session, then navigates.
 *
 * It also handles the failure case with the same care. A refused flow comes back
 * as `?error=...`, and a used or expired link as `?error_code=otp_expired`. Both
 * are surfaced through the shared error notice with the exact URL to allow-list,
 * because "nothing happened" is the worst possible outcome for a student who just
 * gave Google their password.
 */

import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import AuthErrorNotice from '../components/AuthErrorNotice'
import { useAuth } from '../hooks/authContext'
import { describeAuthCallbackError, type AuthFailure } from '../lib/authErrors'

export default function AuthCallbackPage() {
  const { user, loading } = useAuth()
  const navigate = useNavigate()

  /*
   * Read at first render, not in an effect. The code and any error parameters are
   * already in the URL when this page mounts -- it exists precisely because the
   * browser has just been redirected here -- so a lazy initialiser captures them
   * without a second render, and without the risk of an effect running after
   * something has already navigated away and dropped the query string.
   */
  const [failure] = useState<AuthFailure | null>(() =>
    typeof window === 'undefined'
      ? null
      : describeAuthCallbackError(new URLSearchParams(window.location.search)),
  )

  useEffect(() => {
    if (failure) return
    // `loading` is false only once the provider has resolved the session, which is
    // after the code exchange has finished.
    if (!loading && user) {
      navigate('/dashboard', { replace: true })
    }
  }, [failure, loading, user, navigate])

  if (failure) {
    return (
      <div className="mx-auto max-w-md px-4 py-16">
        <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">
            Sign-in did not complete
          </h1>
          <div className="mt-4">
            <AuthErrorNotice failure={failure} />
          </div>
          <Link
            to="/login"
            className="mt-6 inline-block text-sm font-medium text-brand-600 hover:underline"
          >
            Back to sign in
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-md px-4 py-24 text-center">
      <p role="status" className="text-slate-600">
        Signing you in…
      </p>
      <p className="mt-2 text-sm text-slate-500">
        If this takes more than a few seconds,{' '}
        <Link to="/login" className="font-medium text-brand-600 hover:underline">
          go back to sign in
        </Link>
        .
      </p>
    </div>
  )
}
