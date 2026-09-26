/**
 * The provider takes the role from the SERVER, and falls back to the token.
 *
 * WHY THIS TEST EXISTS
 *
 * The token's role is a snapshot taken at sign-in. The owner can promote somebody in the
 * admin console in between, and the database already says ADMIN while the token still
 * says STUDENT. Reading only the token meant the newly promoted admin was told "this area
 * needs a admin role" and locked out of the console they had just been given, until they
 * signed out and back in.
 *
 * `GET /me` answers from the `users` row - the same row every request is authorized
 * against - so it wins. The assertions below are about that precedence and about the
 * failure mode, which is the half that is easy to get wrong: a fetch that fails must NOT
 * empty the role, because "the request timed out" would then look exactly like "you have
 * been demoted".
 */

import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { User } from '@supabase/supabase-js'

import { AuthContext, type AuthState } from '../hooks/authContext'
import { AuthProvider } from '../hooks/AuthProvider'

const fetchMe = vi.fn()
const getSession = vi.fn()
const onAuthStateChange = vi.fn()

vi.mock('../lib/queries', () => ({
  fetchMe: () => fetchMe(),
  // The provider imports nothing else from here, but a partial mock keeps the module
  // shape honest if that changes.
  QueryError: class QueryError extends Error {},
}))

vi.mock('../lib/supabase', () => ({
  supabase: {
    auth: {
      getSession: () => getSession(),
      onAuthStateChange: (...args: unknown[]) => onAuthStateChange(...args),
    },
  },
  authRedirectUrl: () => 'http://localhost/auth/callback',
}))

/** A stored session whose token says STUDENT, as a promoted user's stale token would. */
function sessionWithTokenRole(role: string) {
  const user = {
    id: '00000000-0000-0000-0000-000000000001',
    aud: 'authenticated',
    app_metadata: { role },
    user_metadata: {},
    created_at: '2026-01-01T00:00:00Z',
  } as unknown as User
  return { data: { session: { user } } }
}

/** Renders whatever role the provider currently exposes. */
function Probe() {
  return (
    <AuthContext.Consumer>
      {(auth: AuthState | null) => <span data-testid="role">{auth?.role ?? 'none'}</span>}
    </AuthContext.Consumer>
  )
}

beforeEach(() => {
  fetchMe.mockReset()
  getSession.mockReset()
  onAuthStateChange.mockReset()
  // The subscription is only needed to exist; the tests drive state via getSession.
  onAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: () => {} } } })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('the role the UI renders', () => {
  it('comes from the server, overriding a stale token', async () => {
    getSession.mockResolvedValue(sessionWithTokenRole('STUDENT'))
    fetchMe.mockResolvedValue({ role: 'ADMIN' })

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    // The promoted user sees the console role WITHOUT signing out and back in.
    await waitFor(() => expect(screen.getByTestId('role')).toHaveTextContent('ADMIN'))
  })

  it('starts from the token so nothing flickers while the server answers', async () => {
    getSession.mockResolvedValue(sessionWithTokenRole('EDITOR'))
    // A promise that never settles: the pre-fetch state is what is being asserted.
    fetchMe.mockReturnValue(new Promise(() => {}))

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('role')).toHaveTextContent('EDITOR'))
  })

  it('keeps the token role when the server cannot be reached', async () => {
    // The failure mode that must NOT become a lockout: a failed fetch is a stale role,
    // not a demotion.
    getSession.mockResolvedValue(sessionWithTokenRole('SUPER_ADMIN'))
    fetchMe.mockRejectedValue(new Error('network down'))

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('role')).toHaveTextContent('SUPER_ADMIN'))
    // Give the rejection a turn to propagate before asserting it was ignored.
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(screen.getByTestId('role')).toHaveTextContent('SUPER_ADMIN')
  })

  it('ignores a role the server sends that is not one of ours', async () => {
    // Degrading to the least privileged role is the rule everywhere else; a server bug
    // (or the string "authenticated") must not become a staff role in the UI.
    getSession.mockResolvedValue(sessionWithTokenRole('STUDENT'))
    fetchMe.mockResolvedValue({ role: 'authenticated' })

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('role')).toHaveTextContent('STUDENT'))
  })

  it('does not ask the server anything when nobody is signed in', async () => {
    getSession.mockResolvedValue({ data: { session: null } })

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('role')).toHaveTextContent('STUDENT'))
    expect(fetchMe).not.toHaveBeenCalled()
  })
})
