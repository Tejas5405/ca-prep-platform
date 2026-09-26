/**
 * Auth context and the `useAuth` hook.
 *
 * Deliberately separate from `AuthProvider.tsx`. A module that exports both a
 * React component and a hook breaks React Fast Refresh, so every edit anywhere in
 * the auth module forced a full page reload during development. Splitting them is
 * the fix the lint rule asks for, not a workaround for it.
 */

import { createContext, useContext } from 'react'
import type { User } from '@supabase/supabase-js'

import type { AuthFailure } from '../lib/authErrors'
import type { Role } from '../lib/roles'

/** What a sign-up attempt did, so the screen can respond correctly. */
export type SignUpOutcome =
  /* Supabase has email confirmation on, so the account exists but no session
     does until the student clicks the link. */
  | 'CONFIRM_EMAIL'
  /* Confirmation is off for this project, so the student is signed in. */
  | 'SIGNED_IN'

export interface AuthState {
  /** The signed-in Supabase user, or null. */
  user: User | null
  /**
   * Read from `user.app_metadata.role` — server-set metadata, which the student
   * cannot write. A UI convenience, NOT a security boundary: the backend reads the
   * same value from the verified token and enforces it there.
   */
  role: Role
  /** True until the first session resolution completes. Do NOT redirect before this. */
  loading: boolean
  signInWithEmail: (email: string, password: string) => Promise<void>
  signUpWithEmail: (email: string, password: string) => Promise<SignUpOutcome>
  signInWithGoogle: () => Promise<void>
  resetPassword: (email: string) => Promise<void>
  resendConfirmation: (email: string) => Promise<void>
  signOutUser: () => Promise<void>
  /**
   * Error from an OAuth callback that failed on the return leg, surfaced after
   * the page reloads.
   *
   * A redirect flow returns to the app as a fresh load, so the failure has no
   * form left to attach itself to. Without this the user is silently returned to
   * the login page having apparently been ignored.
   *
   * An `AuthFailure` rather than a string, because the useful part of a redirect
   * failure is the URL that has to be allow-listed.
   */
  redirectError: AuthFailure | null
}

export const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used inside an AuthProvider')
  }
  return context
}
