/**
 * Supabase Auth provider.
 *
 * The role exposed here is read from the session user's `app_metadata`, which is
 * server-set and therefore not writable by the student. It is NOT a security
 * boundary: the backend re-reads the same value from the verified token on every
 * request and enforces it there. Hiding a button does not protect an endpoint.
 *
 * TWO SOURCES, IN THIS ORDER
 *
 * 1. THE SERVER. `GET /me` returns the role from the `users` row — the same row every
 *    request is authorized against. It is fetched once the session is known, and it WINS.
 * 2. THE TOKEN, as an immediate placeholder while that request is in flight.
 *
 * WHY THE SERVER HAS TO WIN. The token's copy is a snapshot: Supabase mints it at
 * sign-in and refreshes it on a schedule. The owner can promote somebody in the console
 * in between, and the database already says ADMIN. Trusting the token alone meant the
 * promoted admin was shown "this area needs a admin role" and locked out of the console
 * they had just been granted — until they signed out and back in. The reverse case is
 * milder (a demoted user keeps seeing a link) and is still worth fixing, because the
 * server answers both immediately.
 *
 * TWO BAGS OF METADATA, ONE OF THEM DANGEROUS
 *
 * `user_metadata` is writable by the authenticated user — `updateUser({ data:
 * { role: 'ADMIN' } })` puts whatever they like in it and the token stays validly
 * signed. `app_metadata` can only be written server-side, through the Admin API. The
 * token fallback reads `app_metadata` and nowhere else. The previous implementation read
 * it from user-writable metadata and that was a live privilege-escalation bug;
 * `apps/api/tests/test_security.py` pins the invariant on the server side and
 * `src/tests/authRole.test.ts` pins it here.
 *
 * A FAILED FETCH IS NOT A DEMOTION. If `/me` cannot be reached the token's answer is
 * kept: the fallback is a stale role rather than no role, and a network blip must not
 * empty the navigation.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
// From `auth-js`, not `supabase-js`: the value import is what would drag the
// realtime client back into the bundle (see lib/supabase.ts for the measurement).
import { AuthError, type User } from '@supabase/auth-js'

import { describeAuthCallbackError, type AuthFailure } from '../lib/authErrors'
import { fetchMe } from '../lib/queries'
import { roleFromUser } from '../lib/roleFromUser'
import { isRole, type Role } from '../lib/roles'
import { authRedirectUrl, supabase } from '../lib/supabase'
import { AuthContext, type AuthState, type SignUpOutcome } from './authContext'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [role, setRole] = useState<Role>('STUDENT')
  const [loading, setLoading] = useState(true)
  /*
   * Derived from the URL at FIRST RENDER rather than in an effect.
   *
   * A redirect returns to the app as a fresh page load, so the query string is
   * already there when the provider mounts -- there is no later moment at which it
   * becomes available. Reading it in a lazy initialiser avoids a setState inside an
   * effect, which would render the signed-out state once before correcting itself.
   */
  const [redirectError] = useState<AuthFailure | null>(() =>
    typeof window === 'undefined'
      ? null
      : describeAuthCallbackError(new URLSearchParams(window.location.search)),
  )

  /*
   * The authoritative role, fetched whenever the session changes.
   *
   * A separate effect from the session subscription below, and deliberately so: it has to
   * run for a session that was ALREADY stored on a page load (not only for a new
   * sign-in), and it has to be able to fail without taking the session state with it.
   */
  useEffect(() => {
    if (!user) return
    let cancelled = false
    void fetchMe()
      .then((me) => {
        if (cancelled) return
        if (isRole(me.role)) setRole(me.role)
      })
      .catch(() => {
        // Keep the token's answer. See the note at the top of this file: a failed fetch
        // is a stale role, not a demotion, and emptying the navigation on a network
        // blip would read as a permissions bug.
      })
    return () => {
      cancelled = true
    }
  }, [user])

  useEffect(() => {
    let cancelled = false

    /*
     * getSession() first, then subscribe.
     *
     * The subscription alone is not enough: on a page load with a stored session
     * there is no event until the token refreshes, so the app would sit in a
     * loading state. Reading the session once gives the CURRENT state, and
     * onAuthStateChange keeps it correct from then on — including the
     * TOKEN_REFRESHED event, which is why the role is re-read on every change
     * rather than captured at sign-in. A role granted (or revoked) mid-session
     * shows up without a reload.
     */
    void supabase.auth.getSession().then(({ data }) => {
      if (cancelled) return
      setUser(data.session?.user ?? null)
      setRole(roleFromUser(data.session?.user ?? null))
      setLoading(false)
    })

    const { data: subscription } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser(session?.user ?? null)
      setRole(roleFromUser(session?.user ?? null))
      setLoading(false)
    })

    return () => {
      cancelled = true
      subscription.subscription.unsubscribe()
    }
  }, [])

  const signInWithEmail = useCallback(async (email: string, password: string) => {
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) throw error
  }, [])

  const signUpWithEmail = useCallback(
    async (email: string, password: string): Promise<SignUpOutcome> => {
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: { emailRedirectTo: authRedirectUrl() },
      })
      if (error) throw error

      /*
       * NO SESSION MEANS CONFIRMATION IS REQUIRED. Supabase returns a user with
       * no session when the project has email confirmation on — which this one
       * does. Telling the student "check your email" only in that case, instead
       * of always, is the difference between correct copy and a student waiting
       * for mail that was never sent.
       */
      return data.session ? 'SIGNED_IN' : 'CONFIRM_EMAIL'
    },
    [],
  )

  const signInWithGoogle = useCallback(async () => {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: authRedirectUrl(),
        // Always show the account chooser. Without this, a student signed into a
        // personal Google account in the same browser is silently signed into the
        // wrong account, which on a shared or college machine is a real problem.
        queryParams: { prompt: 'select account' },
      },
    })
    if (error) throw error
    // No further work here: the browser is navigating to Google. The session is
    // established on the return leg and picked up by onAuthStateChange.
  }, [])

  const resetPassword = useCallback(async (email: string) => {
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: authRedirectUrl(),
    })
    if (error) throw error
  }, [])

  const resendConfirmation = useCallback(async (email: string) => {
    const { error } = await supabase.auth.resend({ type: 'signup', email })
    if (error) throw error
  }, [])

  const signOutUser = useCallback(async () => {
    const { error } = await supabase.auth.signOut()
    // A sign-out that fails because the session was already gone is not an error
    // worth showing anyone; the local state is cleared either way.
    if (error && !(error instanceof AuthError && error.status === 403)) {
      throw error
    }
  }, [])

  const value = useMemo<AuthState>(
    () => ({
      user,
      role,
      loading,
      signInWithEmail,
      signUpWithEmail,
      signInWithGoogle,
      resetPassword,
      resendConfirmation,
      signOutUser,
      redirectError,
    }),
    [
      user,
      role,
      loading,
      redirectError,
      signInWithEmail,
      signUpWithEmail,
      signInWithGoogle,
      resetPassword,
      resendConfirmation,
      signOutUser,
    ],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
