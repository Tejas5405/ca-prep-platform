/**
 * Static preview harness for the public screens.
 *
 * WHY THIS EXISTS
 *
 * These are the screens that can be judged before the app is deployed, and the
 * ones a non-engineer has to sign off on. This renders them as a single
 * self-contained HTML file with the real Tailwind stylesheet and the real bundle
 * inlined, so it opens from a file:// path, needs no server, no network and no
 * Supabase project, and can be emailed to someone for review.
 *
 * It is a DEVELOPMENT tool, not a second app: nothing here ships, and nothing in
 * src/ imports from it. The page switcher at the bottom is preview chrome - it
 * exists because the two screens are otherwise unreachable from each other in a
 * build with no router.
 */

import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'

import { AuthContext, type AuthState } from '../src/hooks/authContext'
import LandingPage from '../src/pages/Landing'
import LoginPage from '../src/pages/Login'
import '../src/index.css'

/**
 * A signed-out viewer.
 *
 * The auth object is stubbed rather than wired to a live project on purpose: a
 * preview that needs network access and a real backend is a preview nobody opens.
 * Signed out is what a real visitor is, so the stubs are honest about the label the
 * call-to-action carries.
 *
 * The Google sign-in stub resolves rather than throwing, so pressing it shows the
 * button's own pending state instead of an error path that a reviewer would
 * wrongly read as a bug.
 */
const PREVIEW_AUTH: AuthState = {
  user: null,
  role: 'STUDENT',
  loading: false,
  signInWithEmail: () => Promise.resolve(),
  signUpWithEmail: () => Promise.resolve(SIGNED_IN_OUTCOME),
  signInWithGoogle: () => Promise.resolve(),
  resetPassword: () => Promise.resolve(),
  resendConfirmation: () => Promise.resolve(),
  signOutUser: () => Promise.resolve(),
  redirectError: null,
}

/** Sign-up resolves as signed in: the stub is for a project without confirmation. */
const SIGNED_IN_OUTCOME = 'SIGNED_IN' as const

type Page = 'landing' | 'signin'

const PAGES: { id: Page; label: string }[] = [
  { id: 'landing', label: 'Landing' },
  { id: 'signin', label: 'Sign in' },
]

function PreviewChrome({ page, onChange }: { page: Page; onChange: (next: Page) => void }) {
  return (
    <div className="fixed inset-x-0 bottom-0 z-50 flex flex-wrap items-center justify-center gap-2 border-t border-slate-700 bg-slate-900/95 px-4 py-2.5 backdrop-blur">
      <span className="mr-1 text-xs font-medium uppercase tracking-wide text-slate-400">
        Preview
      </span>
      {PAGES.map((item) => (
        <button
          key={item.id}
          type="button"
          data-preview-nav={item.id}
          aria-pressed={page === item.id}
          onClick={() => onChange(item.id)}
          className={`min-h-9 rounded-md px-3 text-sm font-medium transition-colors ${
            page === item.id
              ? 'bg-white text-slate-900'
              : 'text-slate-300 hover:bg-slate-800 hover:text-white'
          }`}
        >
          {item.label}
        </button>
      ))}
      <span className="ml-2 hidden text-xs text-slate-500 sm:inline">
        Static render. Buttons do not reach the network.
      </span>
    </div>
  )
}

function PreviewApp() {
  const [page, setPage] = useState<Page>('landing')

  return (
    <MemoryRouter initialEntries={['/']}>
      <AuthContext.Provider value={PREVIEW_AUTH}>
        {/* pb-16 keeps the switcher from covering the real footer. */}
        <div className="pb-16">
          {page === 'landing' ? <LandingPage /> : <LoginPage />}
        </div>
        <PreviewChrome page={page} onChange={setPage} />
      </AuthContext.Provider>
    </MemoryRouter>
  )
}

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <PreviewApp />
  </StrictMode>,
)
