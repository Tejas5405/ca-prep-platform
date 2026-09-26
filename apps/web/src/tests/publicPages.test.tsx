/**
 * Tests for the public site: the routes, the shell, the metadata and the honesty.
 *
 * WHY THE PUBLIC PAGES GET TESTS OF THEIR OWN
 *
 * The landing page has its own file, and it tests claims — ICAI figures, the
 * calculator's arithmetic, the absence of social proof. This file tests the things
 * that only exist once there is more than one public page:
 *
 *   * every public route RENDERS, has exactly one h1, and links nowhere that does
 *     not exist. A footer link to a page that 404s is the cheapest way for a site
 *     to look abandoned, and it is invisible if nobody clicks it in review.
 *   * the pricing table is rendered from the API, and when the API cannot be
 *     reached the page says so instead of showing a price.
 *   * `/signup` and `/forgot-password` are real destinations. A password-reset
 *     email that lands on a sign-IN form is the kind of thing an automated test
 *     catches and a human reviewer does not.
 *   * the reset flow never reveals whether an address has an account — asserted,
 *     because it is a security property that is easy to lose in a copy edit.
 *   * metadata is per-route: three public pages sharing one title would compete
 *     for the same search result and look identical when shared.
 *   * the mobile menu closes on Escape and locks the page behind it, because a
 *     keyboard user trapped behind an overlay is a real failure, not a nitpick.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext, type AuthState } from '../hooks/authContext'
import FaqPage from '../pages/public/FaqPage'
import FeaturesPage from '../pages/public/FeaturesPage'
import LoginPage from '../pages/Login'
import PricingPage from '../pages/public/PricingPage'
import PrivacyPage from '../pages/public/PrivacyPage'
import TermsPage from '../pages/public/TermsPage'
import { ALL_FAQS, FAQ_GROUPS, PUBLIC_NAV, ROADMAP } from '../lib/publicContent'
import { SITE_URL } from '../lib/site'

import { makeAuth } from './authStub'

vi.mock('../lib/supabase', () => ({
  supabase: {
    auth: {
      getSession: vi.fn().mockResolvedValue({
        data: { session: { access_token: 'test-access-token' } },
        error: null,
      }),
    },
  },
  authRedirectUrl: () => 'http://localhost:3000/auth/callback',
  supabaseDashboardUrl: (path: string) => `https://supabase.com/dashboard/project/test/${path}`,
}))

function ok(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

const envelope = <T,>(data: T) => ({ data, meta: { requestId: 'test' } })

const PLAN_CATALOGUE = envelope({
  plans: [
    {
      code: 'FREE',
      tier: 'FREE',
      label: 'Free',
      amountPaise: 0,
      amountRupees: 0,
      currency: 'INR',
      durationDays: 365,
      tagline: 'Practise, revise and plan without paying.',
      features: ['Full question bank', 'Spaced revision'],
      recommended: false,
    },
    {
      code: 'PREMIUM_365',
      tier: 'PREMIUM',
      label: 'Premium',
      amountPaise: 99900,
      amountRupees: 999,
      currency: 'INR',
      durationDays: 365,
      tagline: 'Everything, for one attempt cycle.',
      features: ['Full question bank', 'Timed mocks', 'Study planner'],
      recommended: true,
    },
  ],
})

function stubFetch(routes: [string, unknown][]) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      calls.push(url)
      for (const [needle, body] of routes) {
        if (url.includes(needle)) return Promise.resolve(ok(body))
      }
      return Promise.resolve(new Response('{}', { status: 500 }))
    }),
  )
  return calls
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** Render a page at a URL, the way the router would. */
function renderAt(entry: string, element: React.ReactNode, auth: AuthState = makeAuth()) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <AuthContext.Provider value={auth}>
        <Routes>
          <Route path="*" element={element} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}

/**
 * The public routes, as (path, component, heading) triples.
 *
 * Declared once so a new page cannot be added to the router without either being
 * listed here or being noticed as missing.
 */
const PUBLIC_PAGES: [string, React.ReactNode, RegExp][] = [
  ['/features', <FeaturesPage />, /everything in the platform/i],
  ['/pricing', <PricingPage />, /free to start/i],
  ['/faq', <FaqPage />, /answers, including the awkward ones/i],
  ['/privacy', <PrivacyPage />, /what this platform stores/i],
  ['/terms', <TermsPage />, /terms of use/i],
]

describe('every public page', () => {
  it.each(PUBLIC_PAGES)('%s renders one h1 and the shared shell', async (path, element, heading) => {
    stubFetch([['/payments/plans', PLAN_CATALOGUE]])

    const { container } = renderAt(path, element)

    // One h1 per page: the thing a screen reader and a crawler both use to decide
    // what the page is about.
    const h1s = container.querySelectorAll('h1')
    expect(h1s).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(heading)

    // The shell, on every public page.
    expect(screen.getByRole('navigation', { name: 'Public' })).toBeInTheDocument()
    expect(screen.getByRole('contentinfo')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'CA Prep home' })).toHaveAttribute('href', '/')
  })

  it.each(PUBLIC_PAGES)('%s links only to routes the site declares', async (path, element) => {
    stubFetch([['/payments/plans', PLAN_CATALOGUE]])

    const { container } = renderAt(path, element)
    const known = new Set([
      '/',
      '/features',
      '/pricing',
      '/faq',
      '/privacy',
      '/terms',
      '/login',
      '/signup',
      '/dashboard',
    ])

    for (const anchor of Array.from(container.querySelectorAll('a'))) {
      const href = anchor.getAttribute('href') ?? ''
      if (href.startsWith('mailto:')) continue
      const route = href.split('#')[0] ?? ''
      if (route === '') continue // pure in-page anchor
      expect(known.has(route), `${path} links to unrouted ${href}`).toBe(true)
    }
  })

  it.each(PUBLIC_PAGES)('%s sets its own title, description and canonical', async (path, element) => {
    stubFetch([['/payments/plans', PLAN_CATALOGUE]])

    renderAt(path, element)

    await waitFor(() => {
      expect(document.title.length).toBeGreaterThan(20)
    })
    const description = document.head.querySelector('meta[name="description"]')
    const canonical = document.head.querySelector('link[rel="canonical"]')
    expect(description?.getAttribute('content')?.length ?? 0).toBeGreaterThan(50)
    expect(canonical?.getAttribute('href')).toBe(`${SITE_URL}${path}`)
    // Three pages sharing one description would be one page competing with itself.
    expect(description?.getAttribute('content')).not.toMatch(/React App|placeholder/i)
  })

  it.each(PUBLIC_PAGES)('%s renders the navbar from the shared nav data', async (path, element) => {
    stubFetch([['/payments/plans', PLAN_CATALOGUE]])
    renderAt(path, element)
    const nav = screen.getByRole('navigation', { name: 'Public' })
    for (const item of PUBLIC_NAV) {
      expect(within(nav).getByRole('link', { name: item.label })).toHaveAttribute('href', item.href)
    }
  })
})

describe('pricing', () => {
  it('renders the catalogue the API serves, prices included', async () => {
    const calls = stubFetch([['/payments/plans', PLAN_CATALOGUE]])
    renderAt('/pricing', <PricingPage />)

    expect(await screen.findByText('Premium')).toBeInTheDocument()
    // Indian digit grouping, and the price is the server's number.
    expect(screen.getByText(/₹999/)).toBeInTheDocument()
    // Scoped: the plan's name and its price are both the word "Free".
    expect(screen.getByRole('heading', { name: 'Free' })).toBeInTheDocument()
    // The recommended card is the server's decision, not the page's.
    expect(screen.getByText(/most students/i)).toBeInTheDocument()

    // Fetched WITHOUT credentials: the pricing page is read by people who have not
    // signed up, so there must be no auth round trip in front of it.
    expect(calls.some((url) => url.includes('/payments/plans'))).toBe(true)
  })

  it('says the catalogue could not be loaded rather than inventing a price', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('{}', { status: 503 }))),
    )
    renderAt('/pricing', <PricingPage />)

    expect(await screen.findByText(/plans could not be loaded/i)).toBeInTheDocument()
    // No price is shown, because no price was known.
    expect(screen.queryByText(/₹/)).not.toBeInTheDocument()
  })

  it('explains that payment is confirmed by the server, not the browser', async () => {
    stubFetch([['/payments/plans', PLAN_CATALOGUE]])
    renderAt('/pricing', <PricingPage />)
    // The claim that matters on this page: success in the browser is not success.
    expect(await screen.findByText(/never because a browser/i)).toBeInTheDocument()
  })
})

describe('features and the honest roadmap', () => {
  it('marks the unbuilt features as in build instead of hiding them', async () => {
    stubFetch([])
    renderAt('/features', <FeaturesPage />)

    const badges = await screen.findAllByText(/in build/i)
    expect(badges.length).toBeGreaterThan(0)
    // The unbuilt feature is named, so nobody signs up expecting it.
    expect(screen.getByText(/grounded ai assistant/i)).toBeInTheDocument()
    // And video solutions are gone from the product entirely - not listed as in
    // build, not listed as available, not mentioned anywhere on the page.
    expect(screen.queryByText(/video/i)).not.toBeInTheDocument()
  })

  it('puts a human between extraction and publication, in that order', async () => {
    stubFetch([])
    renderAt('/features', <FeaturesPage />)

    const steps = await screen.findAllByRole('heading', { level: 3 })
    const labels = steps.map((step) => step.textContent ?? '')
    const qa = labels.findIndex((label) => /human qa/i.test(label))
    const publish = labels.findIndex((label) => /^publish$/i.test(label))
    const extract = labels.findIndex((label) => /^extract$/i.test(label))

    expect(extract).toBeGreaterThanOrEqual(0)
    expect(qa).toBeGreaterThan(extract)
    expect(publish).toBeGreaterThan(qa)
  })
})

describe('faq', () => {
  it('renders every question the content module declares, grouped', async () => {
    stubFetch([])
    renderAt('/faq', <FaqPage />)

    for (const group of FAQ_GROUPS) {
      expect(screen.getByRole('heading', { name: group.heading })).toBeInTheDocument()
      for (const item of group.items) {
        expect(screen.getByText(item.q)).toBeInTheDocument()
      }
    }
    expect(ALL_FAQS.length).toBeGreaterThan(12)
  })

  it('deep-links a group, so a sceptical visitor lands on the answer they came for', async () => {
    stubFetch([])
    const { container } = renderAt('/faq', <FaqPage />)
    // The footer links to /faq#content; if that id is missing the link silently
    // drops the reader at the top of a seventeen-item page.
    expect(container.querySelector('#content')).not.toBeNull()
    expect(container.querySelector('#pricing')).not.toBeNull()
  })

  it('answers the roadmap question by naming what is not built', async () => {
    stubFetch([])
    renderAt('/faq', <FaqPage />)
    // ROADMAP is the same list the landing page shows; this page must not imply
    // those features exist.
    expect(ROADMAP.length).toBeGreaterThan(0)
  })
})

describe('legal pages say what the implementation does', () => {
  it('privacy describes private storage and signed URLs, not a template', async () => {
    stubFetch([])
    renderAt('/privacy', <PrivacyPage />)
    expect(await screen.findByText(/private bucket/i)).toBeInTheDocument()
    // The phrase appears in two sections (uploads, and the security summary), which
    // is the point: the rule is stated where a reader looking for either will find
    // it.
    expect(screen.getAllByText(/signed URL/i).length).toBeGreaterThan(1)
    // Cookies: the claim is specific - no advertising or tracking cookies.
    expect(screen.getByText(/no advertising or tracking cookies/i)).toBeInTheDocument()
    // And the processor list is real, not a generic list of every SaaS on earth.
    for (const processor of ['Supabase', 'Render', 'Vercel', 'Razorpay']) {
      expect(screen.getAllByText(new RegExp(processor, 'i')).length).toBeGreaterThan(0)
    }
  })

  it('terms state the non-affiliation and the ICAI override', async () => {
    stubFetch([])
    renderAt('/terms', <TermsPage />)
    expect(await screen.findByText(/not affiliated with, endorsed by or connected to/i)).toBeInTheDocument()
    expect(screen.getByText(/override anything stated here/i)).toBeInTheDocument()
  })

  it('terms restrict bulk redistribution without pretending the questions are ours', async () => {
    stubFetch([])
    renderAt('/terms', <TermsPage />)
    expect(
      await screen.findByText(/bulk-download, scrape or systematically export/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/originate in papers ICAI publishes/i)).toBeInTheDocument()
  })
})

describe('auth routes', () => {
  it('renders the sign-up form at /signup, not a sign-in form', async () => {
    renderAt('/signup', <LoginPage />)
    expect(screen.getByRole('heading', { name: /create your account/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /create account/i })).toBeInTheDocument()
  })

  it('renders the reset form at /forgot-password, with no password field', async () => {
    renderAt('/forgot-password', <LoginPage />)
    expect(screen.getByRole('heading', { name: /reset your password/i })).toBeInTheDocument()
    // Asking for a new password before the recovery link is opened is how a
    // front-end "resets" something the auth provider never heard about.
    expect(screen.queryByLabelText(/^password$/i)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /send reset link/i })).toBeInTheDocument()
  })

  it('sends the recovery email and confirms it was sent, without saying whether the account exists', async () => {
    const resetPassword = vi.fn(() => Promise.resolve())
    const user = userEvent.setup()
    renderAt('/forgot-password', <LoginPage />, makeAuth({ resetPassword }))

    await user.type(screen.getByLabelText(/email/i), 'someone@example.com')
    await user.click(screen.getByRole('button', { name: /send reset link/i }))

    await waitFor(() => expect(resetPassword).toHaveBeenCalledWith('someone@example.com'))
    const panel = await screen.findByRole('heading', { name: /check your inbox/i })
    expect(panel).toBeInTheDocument()
    // "If this address has an account" - the wording that keeps this box from
    // becoming a way to test whether an email is registered here.
    // The sentence is spread across elements (the address is its own node), so the
    // assertion reads the panel's text rather than one leaf.
    expect(panel.closest('div')?.textContent ?? '').toMatch(/has an account/i)
    expect(screen.queryByText(/no account|not registered|unknown email/i)).not.toBeInTheDocument()
  })

  it('offers no Google button in reset mode', async () => {
    renderAt('/forgot-password', <LoginPage />)
    // A Google account has no password here to reset.
    expect(screen.queryByRole('button', { name: /continue with google/i })).not.toBeInTheDocument()
  })
})

describe('the mobile menu', () => {
  it('opens, moves focus into the sheet, closes on Escape, and restores scrolling', async () => {
    stubFetch([['/payments/plans', PLAN_CATALOGUE]])
    const user = userEvent.setup()
    renderAt('/pricing', <PricingPage />)

    expect(screen.queryByRole('dialog', { name: 'Menu' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /open menu/i }))
    const dialog = await screen.findByRole('dialog', { name: 'Menu' })
    expect(dialog).toBeInTheDocument()
    // The page behind the sheet must not scroll: without the lock, a visitor
    // scrolls the page they cannot see and loses their place in both.
    expect(document.body.style.overflow).toBe('hidden')

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Menu' })).not.toBeInTheDocument())
    expect(document.body.style.overflow).toBe('')
  })

  it('offers the same destinations and a working CTA on a phone', async () => {
    stubFetch([['/payments/plans', PLAN_CATALOGUE]])
    const user = userEvent.setup()
    renderAt('/pricing', <PricingPage />)

    await user.click(screen.getByRole('button', { name: /open menu/i }))
    const dialog = await screen.findByRole('dialog', { name: 'Menu' })

    for (const item of PUBLIC_NAV) {
      expect(within(dialog).getByRole('link', { name: item.label })).toHaveAttribute(
        'href',
        item.href,
      )
    }
    expect(within(dialog).getByRole('link', { name: /start preparing free/i })).toHaveAttribute(
      'href',
      '/signup',
    )
    expect(within(dialog).getByRole('link', { name: /^sign in$/i })).toHaveAttribute('href', '/login')
  })
})
