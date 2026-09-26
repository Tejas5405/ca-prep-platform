/**
 * A complete `AuthState` for tests and the static preview.
 *
 * WHY THIS EXISTS AS ONE FILE
 *
 * The stub was copy-pasted into three places (two test files and the preview
 * harness), and every one of them broke the same way when `AuthState` changed: the
 * type error appeared in whichever file was compiled first, the fix was applied
 * there, and the next file failed on the next run. One factory means a new field
 * breaks in exactly one place, and `Partial<AuthState>` keeps each test honest
 * about the single thing it is overriding.
 */

import type { AuthState, SignUpOutcome } from '../hooks/authContext'

/** Every sign-up below the project's email-confirmation setting. */
export const SIGNED_IN: SignUpOutcome = 'SIGNED_IN'
export const CONFIRM_EMAIL: SignUpOutcome = 'CONFIRM_EMAIL'

export function makeAuth(overrides: Partial<AuthState> = {}): AuthState {
  return {
    user: null,
    role: 'STUDENT',
    loading: false,
    signInWithEmail: () => Promise.resolve(),
    signUpWithEmail: () => Promise.resolve(SIGNED_IN),
    signInWithGoogle: () => Promise.resolve(),
    resetPassword: () => Promise.resolve(),
    resendConfirmation: () => Promise.resolve(),
    signOutUser: () => Promise.resolve(),
    redirectError: null,
    ...overrides,
  }
}
