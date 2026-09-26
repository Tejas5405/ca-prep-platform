import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'

import AuthErrorNotice from '../components/AuthErrorNotice'
import GoogleLogo from '../components/GoogleLogo'
import { useAuth } from '../hooks/authContext'
import { describeAuthError, type AuthFailure } from '../lib/authErrors'

/**
 * Which of the three screens this is.
 *
 * THE ROUTE IS THE SOURCE OF TRUTH, not component state. `/signup` and
 * `/forgot-password` are real URLs because they are the two links this product
 * sends people: a shared "create an account" link, and the link a student finds
 * after forgetting their password. A mode held in `useState` makes both of those
 * arrive at a sign-in form with the right content one click away and no way to
 * tell anyone where to click.
 */
type Mode = 'signin' | 'signup' | 'reset'

function modeFromPath(pathname: string): Mode {
  if (pathname.startsWith('/signup')) return 'signup'
  if (pathname.startsWith('/forgot-password')) return 'reset'
  return 'signin'
}

export default function LoginPage() {
  const {
    user,
    loading,
    signInWithEmail,
    signUpWithEmail,
    signInWithGoogle,
    resetPassword,
    resendConfirmation,
    redirectError,
  } = useAuth()

  const location = useLocation()
  const navigate = useNavigate()
  const mode: Mode = modeFromPath(location.pathname)
  const [resetSent, setResetSent] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<AuthFailure | null>(null)
  const [busy, setBusy] = useState(false)
  /* Set when sign-up succeeded but the project requires email confirmation, so no
     session exists yet. The screen becomes a "check your inbox" state with a
     resend button, instead of appearing to have done nothing. */
  const [awaitingConfirmation, setAwaitingConfirmation] = useState(false)
  const [resent, setResent] = useState(false)

  // An error raised by the redirect flow arrives after a page reload, so it has
  // no form to attach to. Local form errors take precedence; this is the
  // fallback. Derived rather than copied into state so a later keystroke clears
  // it without an effect.
  const shownError = error ?? redirectError

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      if (mode === 'reset') {
        // Sends a recovery link. The screen confirms it was SENT rather than that
        // the address exists: saying "no such account" here would turn a password
        // reset box into a way to test whether an email is registered.
        await resetPassword(email)
        setResetSent(true)
        return
      }
      if (mode === 'signin') {
        await signInWithEmail(email, password)
      } else {
        const outcome = await signUpWithEmail(email, password)
        if (outcome === 'CONFIRM_EMAIL') {
          setAwaitingConfirmation(true)
          setPassword('')
        }
      }
    } catch (err) {
      setError(describeAuthError(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleResend() {
    setError(null)
    setBusy(true)
    try {
      await resendConfirmation(email)
      setResent(true)
    } catch (err) {
      setError(describeAuthError(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleGoogle() {
    setError(null)
    setBusy(true)
    try {
      await signInWithGoogle()
    } catch (err) {
      setError(describeAuthError(err))
    } finally {
      setBusy(false)
    }
  }

  const isSignup = mode === 'signup'
  const isReset = mode === 'reset'

  // Reading the route guard here rather than in the router keeps the redirect
  // immediate: a signed-in user hitting /login goes straight to the dashboard.
  if (loading) return null

  if (awaitingConfirmation) {
    return (
      <div className="mx-auto max-w-md px-4 py-16">
        <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">
            Confirm your email
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            We sent a confirmation link to <span className="font-medium">{email}</span>. Open it to
            activate your account, then come back and sign in.
          </p>
          {resent && (
            <p role="status" className="mt-3 text-sm text-right-600">
              Sent again. Check your spam folder if it does not arrive within a few minutes.
            </p>
          )}
          {error && (
            <div className="mt-4">
              <AuthErrorNotice failure={error} />
            </div>
          )}
          <div className="mt-5 flex items-center justify-between gap-3">
            <button
              type="button"
              onClick={() => {
                setAwaitingConfirmation(false)
                setResent(false)
                setError(null)
              }}
              className="text-sm font-medium text-brand-600 hover:underline"
            >
              Back to sign in
            </button>
            <button
              type="button"
              onClick={() => void handleResend()}
              disabled={busy}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
            >
              {busy ? 'Sending…' : 'Resend the email'}
            </button>
          </div>
        </div>
      </div>
    )
  }
  if (resetSent) {
    return (
      <div className="mx-auto max-w-md px-4 py-16">
        <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">Check your inbox</h1>
          <p className="mt-2 text-sm text-slate-600">
            If <span className="font-medium">{email}</span> has an account, a password reset link is on
            its way. The link expires shortly, so use it soon.
          </p>
          <p className="mt-3 text-sm text-slate-600">
            Nothing arrived? Check the spam folder, then try again — the address is only accepted once
            every few seconds.
          </p>
          <button
            type="button"
            onClick={() => {
              setResetSent(false)
              navigate('/login')
            }}
            className="mt-5 text-sm font-medium text-brand-600 hover:underline"
          >
            Back to sign in
          </button>
        </div>
      </div>
    )
  }

  if (user) return <Navigate to="/dashboard" replace />

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-12">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <Link
            to="/"
            className="text-2xl font-semibold tracking-tight text-slate-900 hover:underline"
          >
            CA Prep
          </Link>
          <p className="mt-1 text-sm text-slate-500">
            Practice, mock exams and a study planner for CA students.
          </p>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-lg font-medium text-slate-900">
            {isReset ? 'Reset your password' : isSignup ? 'Create your account' : 'Sign in'}
          </h2>
          {isReset && (
            <p className="mt-2 text-sm text-slate-600">
              Enter the email you signed up with and we will send a link to set a new password.
            </p>
          )}

          <form onSubmit={handleSubmit} className="mt-5 space-y-4" noValidate>
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-slate-700">
                Email
              </label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 shadow-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
              />
            </div>

            {/* Reset mode asks for the email only, and the field is NOT RENDERED
                rather than hidden: asking for a new password before the recovery
                link has been opened is how a front-end ends up "changing" a
                password the auth provider never heard about - and a hidden input
                is still in the accessibility tree, so a screen reader would still
                be asked for one. */}
            {!isReset && (
            <div>
              <label htmlFor="password" className="block text-sm font-medium text-slate-700">
                Password
              </label>
              <input
                id="password"
                type="password"
                // A distinct autocomplete value per mode stops a browser from
                // autofilling the old password into a signup form.
                autoComplete={isSignup ? 'new-password' : 'current-password'}
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 shadow-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
              />
              {isSignup && (
                <p className="mt-1 text-xs text-slate-500">At least 8 characters.</p>
              )}
            </div>
            )}

            {mode === 'signin' && (
              <p className="text-right">
                <Link
                  to="/forgot-password"
                  className="text-sm font-medium text-brand-600 hover:underline"
                >
                  Forgot your password?
                </Link>
              </p>
            )}

            {shownError && <AuthErrorNotice failure={shownError} />}

            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-md bg-brand-600 px-4 py-2 font-medium text-white transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy
                ? 'Please wait…'
                : isReset
                  ? 'Send reset link'
                  : isSignup
                    ? 'Create account'
                    : 'Sign in'}
            </button>
          </form>

          {/* No OAuth in reset mode: a Google account has no password here to reset,
              and offering the button would suggest otherwise. */}
          {!isReset && (
            <div className="my-5 flex items-center gap-3">
              <div className="h-px flex-1 bg-slate-200" />
              <span className="text-xs tracking-wide text-slate-400 uppercase">or</span>
              <div className="h-px flex-1 bg-slate-200" />
            </div>
          )}

          {/*
            Styled to Google's recommendations for the button surface (white fill,
            light border, dark neutral label) while the mark itself stays
            untouched. Google permits the button to match the surrounding product;
            it does not permit altering the logo.

            min-h-11 (44px) rather than py-2: the previous button came out at
            40px, under the 44px touch target that both Apple's and Google's own
            guidance ask for. That matters more here than elsewhere in the app,
            because this is the control most students will use one-handed on a
            phone.
          */}
          {!isReset && (
            <button
              type="button"
              onClick={() => void handleGoogle()}
              disabled={busy}
              className="flex min-h-11 w-full items-center justify-center gap-3 rounded-md border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:opacity-60"
            >
              <GoogleLogo />
              Continue with Google
            </button>
          )}

          <p className="mt-6 text-center text-sm text-slate-600">
            {isReset ? 'Remembered it?' : isSignup ? 'Already have an account?' : 'New to CA Prep?'}{' '}
            {/* A button that navigates, deliberately: these two screens are the same
                form with a different question, so the control behaves better as a
                toggle than as a link that looks like a page change. The route
                changes either way, which is what keeps /signup and /login
                shareable and the back button honest. */}
            <button
              type="button"
              onClick={() => {
                setError(null)
                navigate(isReset ? '/login' : isSignup ? '/login' : '/signup')
              }}
              className="font-medium text-brand-600 hover:underline"
            >
              {isReset ? 'Sign in' : isSignup ? 'Sign in' : 'Create one'}
            </button>
          </p>
        </div>
      </div>
    </div>
  )
}
