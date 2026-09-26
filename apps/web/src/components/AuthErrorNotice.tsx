/**
 * The error block on the sign-in screen.
 *
 * WHY THIS IS NOT ONE RED PARAGRAPH
 *
 * The failure that sent a developer here was `auth/unauthorized-domain`, and the
 * fix for it is a value that has to be typed into another website's settings page.
 * A single sentence - "This site is not authorised for sign-in yet" - cannot carry
 * that value, so the reader has to work out which domain the app is running on and
 * hope they guessed right.
 *
 * So the notice renders three things: what happened, what to do, and the exact
 * value to paste - a redirect URL, or the callback URL Google needs allow-listing
 * - in a selectable box next to a copy button. The dashboard link appears only
 * outside production: a student on the real site cannot fix a project setting, and
 * a link into the Supabase dashboard is not something to show a paying customer.
 */

import { useState } from 'react'

import type { AuthFailure } from '../lib/authErrors'

export default function AuthErrorNotice({ failure }: { failure: AuthFailure }) {
  const [copied, setCopied] = useState(false)

  const showConsoleLink = import.meta.env.VITE_APP_ENV !== 'production' && failure.settingsUrl

  async function copyValue() {
    if (!failure.value) return
    try {
      await navigator.clipboard.writeText(failure.value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard access is denied in some browsers and in every sandboxed frame.
      // The value is rendered as selectable text, so the fallback is selecting it
      // by hand - which is why the copy attempt is never allowed to throw.
    }
  }

  return (
    <div
      role="alert"
      data-testid="auth-error"
      className="rounded-md border border-wrong-500/30 bg-wrong-50 px-3 py-2 text-sm text-wrong-500"
    >
      <p className="font-medium">{failure.message}</p>
      {failure.hint && <p className="mt-1 text-xs leading-relaxed text-slate-700">{failure.hint}</p>}

      {failure.value && (
        <div className="mt-2 flex items-stretch gap-2">
          <code className="min-w-0 flex-1 truncate rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-800">
            {failure.value}
          </code>
          <button
            type="button"
            onClick={() => void copyValue()}
            className="shrink-0 rounded border border-slate-300 bg-white px-2 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      )}

      {showConsoleLink && (
        <a
          href={failure.settingsUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-block text-xs font-medium text-brand-600 underline"
        >
          Open Supabase Auth settings
        </a>
      )}
    </div>
  )
}
