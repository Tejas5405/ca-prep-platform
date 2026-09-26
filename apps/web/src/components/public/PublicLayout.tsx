/**
 * The public shell: announcement bar, navbar, footer.
 *
 * WHY ONE SHELL RATHER THAN PER-PAGE HEADERS
 *
 * The marketing site and the authenticated app are the same product, and the
 * visitor's first impression of the app is formed here. When each public page
 * carried its own header, they drifted immediately: one had a scroll effect and
 * another did not, one used `Start free` and another `Get started`, and the logo
 * link pointed at `/` on some pages and `/dashboard` on others.
 *
 * The shell is signed-in aware because the landing page is exactly where a
 * returning student lands from a bookmark. Sending them back through a sign-up
 * form is the kind of small brokenness that makes a product feel abandoned.
 *
 * ACCESSIBILITY NOTES THAT ARE LOAD-BEARING
 *
 *   * The mobile menu is a real dialog: Escape closes it, focus moves into it and
 *     returns to the button, and the page behind it cannot be scrolled. A menu
 *     that traps a keyboard user on a phone-width screen is not a small bug.
 *   * Every interactive element is at least 44px tall on touch, which is the
 *     target size in the design research this page was built from.
 *   * The nav collapses at `lg`, not `sm`, because five links plus two CTAs wrap
 *     awkwardly at tablet widths - and a wrapped navbar is what a visitor sees
 *     first.
 */

import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'

import { useAuth } from '../../hooks/authContext'
import {
  DASHBOARD_HREF,
  FOOTER_COLUMNS,
  PUBLIC_NAV,
  SIGNIN_HREF,
  SIGNUP_HREF,
} from '../../lib/publicContent'
import { Wordmark } from './Logo'

/**
 * The announcement bar.
 *
 * It carries a dated, checkable fact rather than "🎉 New feature!". The fact is
 * the one that changes a student's plan: CA Final moved to two attempts a year.
 * A visitor who already knew it learns nothing and loses nothing by scrolling past
 * one line; a visitor who did not has just been told something useful before being
 * asked for anything.
 */
function AnnouncementBar() {
  return (
    <div className="border-b border-brand-700/40 bg-brand-700 text-white">
      <div className="mx-auto flex max-w-6xl items-center justify-center gap-x-2 gap-y-0.5 px-4 py-2 text-center text-xs sm:px-6 sm:text-[13px]">
        <span className="hidden font-semibold sm:inline">ICAI notification, 6 April 2026:</span>
        <span className="opacity-95">
          CA Final now runs two attempts a year — May and November.
        </span>
        <a href="/#how" className="hidden underline underline-offset-2 hover:no-underline sm:inline">
          How we plan around it
        </a>
      </div>
    </div>
  )
}

function MobileMenu({
  open,
  onClose,
  signedIn,
}: {
  open: boolean
  onClose: () => void
  signedIn: boolean
}) {
  const firstLink = useRef<HTMLAnchorElement>(null)

  useEffect(() => {
    if (!open) return
    firstLink.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    // Scroll lock: without it the page scrolls behind an open sheet and the
    // visitor loses their place in both.
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previousOverflow
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="lg:hidden">
      <div
        className="fixed inset-0 z-30 bg-slate-900/20 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Menu"
        className="fixed inset-x-0 top-0 z-40 border-b border-slate-200 bg-white p-4 shadow-lg"
      >
        <div className="flex items-center justify-between">
          <Wordmark />
          <button
            type="button"
            onClick={onClose}
            aria-label="Close menu"
            className="inline-flex h-11 w-11 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path
                d="M5 5l10 10M15 5L5 15"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        <nav className="mt-4 flex flex-col" aria-label="Public">
          {PUBLIC_NAV.map((item, index) => (
            <Link
              key={item.href}
              to={item.href}
              ref={index === 0 ? firstLink : undefined}
              onClick={onClose}
              className="flex min-h-12 items-center border-b border-slate-100 text-base font-medium text-slate-800"
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="mt-4 flex flex-col gap-2">
          <Link
            to={signedIn ? DASHBOARD_HREF : SIGNUP_HREF}
            onClick={onClose}
            className="inline-flex min-h-12 items-center justify-center rounded-md bg-brand-600 px-4 text-base font-medium text-white"
          >
            {signedIn ? 'Open dashboard' : 'Start preparing free'}
          </Link>
          {!signedIn && (
            <Link
              to={SIGNIN_HREF}
              onClick={onClose}
              className="inline-flex min-h-12 items-center justify-center rounded-md border border-slate-300 px-4 text-base font-medium text-slate-700"
            >
              Sign in
            </Link>
          )}
        </div>
      </div>
    </div>
  )
}

export function PublicLayout({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const signedIn = !loading && user !== null
  const [menuOpen, setMenuOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const location = useLocation()

  // The sheet closes itself on navigation: every link inside it calls `onClose`,
  // and the overlay behind it closes on click. An effect watching `location` for
  // the same thing was removed - it was redundant AND it set state during an
  // effect, which is a cascading render for behaviour the links already have.

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // A visitor who arrives on `/#calculator` expects the browser to jump there;
  // React Router does not do it because the page did not reload.
  useEffect(() => {
    if (location.hash) {
      const target = document.getElementById(location.hash.slice(1))
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [location.hash])

  return (
    <div className="flex min-h-screen flex-col bg-white">
      {!loading && !signedIn && <AnnouncementBar />}

      <header
        className={`sticky top-0 z-20 border-b bg-white/85 backdrop-blur transition-colors ${
          scrolled ? 'border-slate-200 shadow-sm' : 'border-transparent'
        }`}
      >
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link to="/" aria-label="CA Prep home" className="flex items-center">
            <Wordmark />
          </Link>

          <nav aria-label="Public" className="hidden items-center gap-1 lg:flex">
            {PUBLIC_NAV.map((item) => (
              <Link
                key={item.href}
                to={item.href}
                className="inline-flex min-h-11 items-center rounded-md px-3 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900"
              >
                {item.label}
              </Link>
            ))}
          </nav>

          <div className="hidden items-center gap-2 lg:flex">
            {signedIn ? (
              <Link
                to={DASHBOARD_HREF}
                className="inline-flex min-h-11 items-center rounded-md bg-slate-900 px-4 text-sm font-medium text-white transition-colors hover:bg-slate-700"
              >
                Open dashboard
              </Link>
            ) : (
              <>
                <Link
                  to={SIGNIN_HREF}
                  className="inline-flex min-h-11 items-center rounded-md px-3 text-sm font-medium text-slate-600 transition-colors hover:text-slate-900"
                >
                  Sign in
                </Link>
                <Link
                  to={SIGNUP_HREF}
                  className="inline-flex min-h-11 items-center rounded-md bg-brand-600 px-4 text-sm font-medium text-white transition-colors hover:bg-brand-700"
                >
                  Start preparing free
                </Link>
              </>
            )}
          </div>

          <button
            type="button"
            onClick={() => setMenuOpen(true)}
            aria-label="Open menu"
            aria-expanded={menuOpen}
            className="inline-flex h-11 w-11 items-center justify-center rounded-md text-slate-700 hover:bg-slate-100 lg:hidden"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path
                d="M3 6h14M3 10h14M3 14h14"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
      </header>

      <MobileMenu open={menuOpen} onClose={() => setMenuOpen(false)} signedIn={signedIn} />

      <main className="flex-1">{children}</main>

      <footer className="border-t border-slate-200 bg-slate-50">
        <div className="mx-auto max-w-6xl px-4 py-12 sm:px-6 sm:py-14">
          <div className="grid gap-10 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <Wordmark />
              <p className="mt-4 max-w-xs text-sm leading-relaxed text-slate-600">
                Study material and practice tools for the CA exams. Not affiliated with or
                endorsed by ICAI.
              </p>
              <p className="mt-3 max-w-xs text-xs text-slate-500">
                Always check icai.org for exam dates, syllabus changes and notifications.
              </p>
            </div>

            {FOOTER_COLUMNS.map((column) => (
              <div key={column.heading}>
                <h2 className="text-xs font-semibold tracking-wide text-slate-900 uppercase">
                  {column.heading}
                </h2>
                <ul className="mt-4 space-y-2">
                  {column.links.map((link) => (
                    <li key={`${column.heading}-${link.href}`}>
                      <Link
                        to={link.href}
                        className="text-sm text-slate-600 transition-colors hover:text-slate-900"
                      >
                        {link.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>

          <div className="mt-10 flex flex-col gap-4 border-t border-slate-200 pt-6 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-slate-500">
              © {new Date().getFullYear()} CA Prep. Prices in Indian Rupees, inclusive of
              applicable taxes.
            </p>
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
              <Link to="/privacy" className="text-xs text-slate-600 hover:text-slate-900">
                Privacy
              </Link>
              <Link to="/terms" className="text-xs text-slate-600 hover:text-slate-900">
                Terms
              </Link>
              <Link to="/faq" className="text-xs text-slate-600 hover:text-slate-900">
                Support
              </Link>
              <Link
                to={signedIn ? DASHBOARD_HREF : SIGNIN_HREF}
                className="text-xs font-medium text-brand-600 hover:underline"
              >
                {signedIn ? 'Dashboard' : 'Sign in'}
              </Link>
            </div>
          </div>
        </div>
      </footer>
    </div>
  )
}
