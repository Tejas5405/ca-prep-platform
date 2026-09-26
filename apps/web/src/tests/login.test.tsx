/**
 * Tests for the sign-in screen.
 *
 * The interesting assertions here are about the Google button, because it is the
 * one control on the page bound by someone else's rules: the "G" mark has to be
 * present, in Google's four brand colours, unmodified - and the button still has
 * to work for a screen reader, which means the mark must be decorative and the
 * label must carry the meaning.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import GoogleLogo from '../components/GoogleLogo'
import { AuthContext, type AuthState } from '../hooks/authContext'
import LoginPage from '../pages/Login'

import { makeAuth as makePlainAuth } from './authStub'

/**
 * The shared stub with spies.
 *
 * The assertions on this screen are mostly "was this flow called, and was the
 * OTHER one not" — a sign-up form that quietly submits a sign-in is a real bug —
 * so the four actions that can be pressed are spies here. Everything else comes
 * from the shared factory, so a new field in `AuthState` breaks in one place.
 */
function makeAuth(overrides: Partial<AuthState> = {}): AuthState {
  return makePlainAuth({
    signInWithEmail: vi.fn(() => Promise.resolve()),
    signUpWithEmail: vi.fn(() => Promise.resolve('SIGNED_IN' as const)),
    signInWithGoogle: vi.fn(() => Promise.resolve()),
    resendConfirmation: vi.fn(() => Promise.resolve()),
    ...overrides,
  })
}

/**
 * Google's four brand colours, transcribed here from their published branding
 * guidelines rather than imported from the component.
 *
 * Importing would make this test circular: it would pass whatever the component
 * contained, including a mistyped hex, because both sides would change together.
 * A literal held separately is the only version of this assertion that catches a
 * recoloured logo.
 */
const GOOGLE_BRAND_COLOURS: readonly string[] = [
  '#4285F4', // blue
  '#34A853', // green
  '#FBBC05', // yellow
  '#EA4335', // red
]

function renderLogin(auth: AuthState = makeAuth()) {
  return render(
    <MemoryRouter>
      <AuthContext.Provider value={auth}>
        <LoginPage />
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}

describe('Google sign-in button', () => {
  it('carries the official Google mark', () => {
    renderLogin()
    const button = screen.getByRole('button', { name: /continue with google/i })
    const mark = button.querySelector('svg')
    expect(mark).not.toBeNull()
    expect(mark?.getAttribute('viewBox')).toBe('0 0 48 48')
  })

  it('uses Google\u2019s four brand colours and nothing else', () => {
    renderLogin()
    const button = screen.getByRole('button', { name: /continue with google/i })
    const fills = [...(button.querySelector('svg')?.querySelectorAll('path') ?? [])].map((p) =>
      p.getAttribute('fill'),
    )

    // A recoloured or monochrome mark is a trademark misuse, and it is exactly the
    // kind of thing a well-meaning redesign does. Asserting the exact hex values
    // rather than "has some colours" is the whole point: #4285F5 is not Google
    // blue, and no reviewer will spot that by eye.
    expect(new Set(fills)).toEqual(new Set([...GOOGLE_BRAND_COLOURS]))
    expect(fills).toHaveLength(4)
  })

  it('keeps the label as the accessible name, with the mark decorative', () => {
    renderLogin()
    // The button is reachable and named by its text, not by an aria-label that
    // would hide a missing label. If the mark were exposed instead, this query
    // would still pass but the announcement would be "logo, button".
    const button = screen.getByRole('button', { name: /continue with google/i })
    expect(button).toHaveAccessibleName('Continue with Google')

    const mark = button.querySelector('svg')
    expect(mark).toHaveAttribute('aria-hidden', 'true')
    expect(button.textContent).toMatch(/Continue with Google/)
  })

  it('meets the minimum touch target', () => {
    renderLogin()
    const button = screen.getByRole('button', { name: /continue with google/i })
    // 44px. Asserted on the class because jsdom does not apply stylesheets, so
    // the computed height is always 0 - the utility is the contract here.
    expect(button.className).toMatch(/min-h-11/)
  })

  it('calls the Google sign-in flow when pressed', async () => {
    const user = userEvent.setup()
    const auth = makeAuth()
    renderLogin(auth)
    /*
     * userEvent rather than fireEvent: the handler is async (it awaits the
     * popup), so the state update that clears `busy` lands after the click
     * returns. fireEvent leaves that update outside act(), which produces a
     * warning and - more importantly - an assertion that can run against a
     * half-applied state.
     */
    await user.click(screen.getByRole('button', { name: /continue with google/i }))
    expect(auth.signInWithGoogle).toHaveBeenCalledTimes(1)
  })

  it('cannot submit the email form', async () => {
    const user = userEvent.setup()
    const auth = makeAuth()
    renderLogin(auth)
    const google = screen.getByRole('button', { name: /continue with google/i })

    /*
     * Two properties, and the second is the one that actually prevents the bug.
     *
     * An earlier version of this test justified itself with "a missing
     * type=button would submit the form - the classic bug in this exact layout".
     * That was wrong, and a mutation proved it: deleting type="button" changed
     * nothing, because the Google button sits outside the <form>. The story was
     * plausible and the assertion it justified was hollow.
     *
     * What matters is therefore asserted directly. The button must carry an
     * explicit type, and it must stay outside the form - moving it inside during
     * a layout change is the accident that would make pressing it submit whatever
     * happens to be in the email and password fields.
     */
    expect(google).toHaveAttribute('type', 'button')
    expect(google.closest('form')).toBeNull()

    await user.click(google)
    expect(auth.signInWithEmail).not.toHaveBeenCalled()
    expect(auth.signUpWithEmail).not.toHaveBeenCalled()
  })
})

describe('sign-in screen', () => {
  it('offers email sign-in and account creation', () => {
    renderLogin()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^sign in$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /create one/i })).toBeInTheDocument()
  })

  it('surfaces an error raised by a redirect sign-in that happened before the page loaded', () => {
    renderLogin(
      makeAuth({
        redirectError: {
          code: 'auth/network-request-failed',
          message: 'Network problem.',
          kind: 'network',
        },
      }),
    )
    expect(screen.getByRole('alert')).toHaveTextContent(/network problem/i)
  })

  it('names the redirect URL to allow-list when Supabase refuses this origin', () => {
    /*
     * The failure this test exists for: an OAuth redirect target that is not on
     * the project's allow-list. The only useful thing the app can do is print the
     * EXACT url, because the reader otherwise has to guess between the preview
     * host, localhost and production - and the guess is usually wrong.
     */
    renderLogin(
      makeAuth({
        redirectError: {
          code: 'redirect_to_not_allowed',
          kind: 'redirect_url',
          message: 'This address is not on the project’s allowed redirect list.',
          hint: 'Add the URL below to Authentication -> URL Configuration -> Redirect URLs.',
          value: 'https://preview.example/auth/callback',
          settingsUrl:
            'https://supabase.com/dashboard/project/zyrmlnpvylhcpwaoizyz/auth/url-configuration',
        },
      }),
    )

    const notice = screen.getByTestId('auth-error')
    expect(notice).toHaveTextContent(/not on the project’s allowed redirect list/i)
    expect(notice).toHaveTextContent('https://preview.example/auth/callback')
    expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /supabase auth settings/i })).toHaveAttribute(
      'href',
      'https://supabase.com/dashboard/project/zyrmlnpvylhcpwaoizyz/auth/url-configuration',
    )
  })

  it('tells a new student to confirm their email instead of appearing to do nothing', () => {
    // The project has email confirmation ON, so sign-up creates an account with no
    // session. Without this state the screen would look like the button did nothing.
    renderLogin(makeAuth({ signUpWithEmail: () => Promise.resolve('CONFIRM_EMAIL') }))

    return (async () => {
      await userEvent.click(screen.getByRole('button', { name: /create/i }))
      // Switching to the sign-up form.
      await userEvent.type(screen.getByLabelText(/email/i), 'new@example.com')
      await userEvent.type(screen.getByLabelText(/password/i), 'longenough123')
      await userEvent.click(screen.getByRole('button', { name: /create account/i }))

      const panel = await screen.findByRole('heading', { name: /confirm your email/i })
      expect(panel).toBeInTheDocument()
      expect(screen.getByText(/new@example.com/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /resend the email/i })).toBeInTheDocument()
    })()
  })

  it('lets a visitor back out to the public page', () => {
    renderLogin()
    // The logo links home; without it the sign-in screen is a dead end for anyone
    // who arrived by mistake.
    expect(screen.getByRole('link', { name: 'CA Prep' })).toHaveAttribute('href', '/')
  })

  it('renders the standalone GoogleLogo for reuse', () => {
    const { container } = render(<GoogleLogo size={24} />)
    const mark = container.querySelector('svg')
    expect(mark).toHaveAttribute('width', '24')
    expect(mark).toHaveAttribute('aria-hidden', 'true')
  })
})
