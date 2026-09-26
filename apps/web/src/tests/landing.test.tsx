/**
 * Tests for the public landing page.
 *
 * WHY A MARKETING PAGE GETS TESTS
 *
 * This page asserts facts: ICAI pass percentages, candidate counts, which months
 * each level is examined in. It is hand-written JSX, so a transposed digit, a
 * level's sessions copy-pasted from the wrong level, or a claim about the exam
 * format nobody verified will sit in production looking entirely plausible. The
 * tests below read the rendered DOM and compare it against `examData.ts`, so the
 * page cannot drift from the fact module that the rest of the app trusts.
 *
 * There is also an explicit NEGATIVE assertion that the exam-mode claim is absent.
 * Pen-and-paper vs computer-based is genuinely unverified across ICAI's own
 * channels, so the page must not assert either, and that rule needs an executable
 * guard rather than a good intention.
 */

import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { AuthContext, type AuthState } from '../hooks/authContext'
import { ESTIMATE_NOTE, LEVELS, LEVEL_ORDER, PASS_RULE } from '../lib/examData'
import LandingPage from '../pages/Landing'

import { makeAuth } from './authStub'

/** Signed-out viewer: the case the page is designed for. */
const SIGNED_OUT: AuthState = makeAuth()

/**
 * Every route the public site declares. A link outside this set is either a typo
 * or a page that does not exist - both of which a visitor experiences as broken.
 *
 * The original version of this test allowed only four hrefs, because the page was
 * built around a single CTA and any other exit was considered a leak. The public
 * site now has a navbar and a footer with real destinations (blueprint §6/§22), so
 * the invariant was rewritten rather than deleted: what matters is that every link
 * resolves, that none of them leaves the site, and that the page still has exactly
 * one PRIMARY call to action rather than five competing ones.
 */
const PUBLIC_ROUTES = new Set([
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

/**
 * Anchor links, in both forms.
 *
 * `#product` is right on the landing page itself; `/#product` is right in the nav
 * and footer, which share code with the other public pages - where a bare `#how`
 * would silently do nothing.
 */
const ANCHORS = new Set(['#calculator', '#how', '#product', '#pricing'])
const ABSOLUTE_ANCHORS = new Set(['/#calculator', '/#how', '/#product', '/#pricing'])

/**
 * The verdict vocabulary, best first.
 *
 * Hardcoded rather than imported on purpose: these are user-facing words, and a
 * rename during a redesign should fail a test rather than silently pass one.
 */
const VERDICT_LABELS = [
  'Comfortable',
  'On track',
  'Tight',
  'Falling short',
  'Not viable as scoped',
] as const

function renderLanding(auth: AuthState = SIGNED_OUT) {
  return render(
    <MemoryRouter>
      <AuthContext.Provider value={auth}>
        <LandingPage />
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}

/**
 * Read the verdict currently displayed by the calculator.
 *
 * Anchored to the `aria-live` region, which exists for screen readers, rather
 * than to a test id: it means the assertion would also fail if the live region
 * were dropped, since a verdict that changes silently is a real accessibility
 * regression on a slider-driven widget.
 */
function readVerdict(): { label: string; rank: number } | null {
  const region = document.querySelector('[aria-live="polite"]')
  if (!region) return null
  const text = region.textContent ?? ''
  const matches = VERDICT_LABELS.filter((label) => text.includes(label))
  if (matches.length !== 1) return null
  const label = matches[0] as string
  return { label, rank: VERDICT_LABELS.indexOf(label as (typeof VERDICT_LABELS)[number]) }
}

function setHours(hours: number) {
  const slider = screen.getByLabelText(/Study hours you can hold/i)
  fireEvent.change(slider, { target: { value: String(hours) } })
}

function clickLevel(name: 'Foundation' | 'Intermediate' | 'Final') {
  fireEvent.click(screen.getByRole('button', { name }))
}

describe('Landing page: the promise', () => {
  it('leads with an outcome the visitor recognises, not a product name', () => {
    renderLanding()
    const heading = screen.getByRole('heading', { level: 1 })
    expect(heading).toHaveTextContent(/CA attempt/i)
    expect(heading).toHaveTextContent(/planned to the day/i)
  })

  it('offers the calculator above the fold as the primary secondary action', () => {
    renderLanding()
    const link = screen.getByRole('link', { name: /check my attempt/i })
    expect(link).toHaveAttribute('href', '#calculator')
    expect(screen.getByRole('heading', { name: /Does your attempt actually fit/i })).toBeVisible()
  })

  it('claims no social proof it does not have', () => {
    const { container } = renderLanding()
    const text = container.textContent ?? ''
    // No invented logos, ratings or student counts. Only ICAI's own published
    // figures are allowed to look like evidence.
    expect(text).not.toMatch(/\b(trusted by|students enrolled|users love|rated \d)/i)
    expect(text).not.toMatch(/\b\d{4,}\+? (students|users|aspirants)\b/i)
  })

  it('states the ICAI non-affiliation', () => {
    const { container } = renderLanding()
    expect(container.textContent).toMatch(/not affiliated with or endorsed by ICAI/i)
  })
})

describe('Landing page: published ICAI figures', () => {
  it.each(LEVEL_ORDER)('renders %s pass percentages from the fact module', (level) => {
    const { container } = renderLanding()
    const text = container.textContent ?? ''
    for (const attempt of LEVELS[level].passRateHistory) {
      // Every published session is on the page, not just the latest. Reading the
      // rendered output is the whole point: this is what a visitor actually sees,
      // and `20.09` mistyped as `20.90` would pass a test that read the module.
      expect(text).toContain(`${attempt.percent}%`)
      expect(text).toContain(attempt.session)
    }
  })

  it('shows the spread between sessions rather than a single flattering number', () => {
    const { container } = renderLanding()
    const text = container.textContent ?? ''
    // Rates for one level range from 8.47% to 21.52% across recent sessions. A
    // page showing only the best session would be technically true and
    // materially misleading.
    expect(text).toContain('8.47%')
    expect(text).toContain('9.39%')
    expect(text).toContain('10.06%')
  })

  it.each(LEVEL_ORDER)('renders %s candidate counts in Indian digit grouping', (level) => {
    const { container } = renderLanding()
    const rate = LEVELS[level].latestPassRate
    const text = container.textContent ?? ''
    expect(text).toContain(rate.appeared.toLocaleString('en-IN'))
    expect(text).toContain(rate.passed.toLocaleString('en-IN'))
  })

  it('states the passing rule per paper and in aggregate', () => {
    const { container } = renderLanding()
    const text = container.textContent ?? ''
    expect(text).toContain(`${PASS_RULE.perPaper}%`)
    expect(text).toContain(`${PASS_RULE.aggregate}%`)
    expect(text).toMatch(/aggregate/i)
  })

  it('labels the study-hour figures as ours, not ICAI\u2019s', () => {
    const { container } = renderLanding()
    expect(container.textContent).toContain(ESTIMATE_NOTE)
  })

  it('never asserts an exam mode, because that is still unverified', () => {
    const { container } = renderLanding()
    const text = container.textContent ?? ''
    // Pen-and-paper and CBT sources conflict and ICAI's own pages do not settle
    // it. Asserting either would be inventing a fact about the exam.
    expect(text).not.toMatch(/pen[- ]and[- ]paper/i)
    expect(text).not.toMatch(/\bCBT\b/)
    expect(text).not.toMatch(/computer[- ]based/i)
  })

  it('carries the CA Final two-attempts rule and the per-level split', () => {
    renderLanding()
    /*
     * Asserted per level, on the card that level owns.
     *
     * An earlier version scanned the whole page with
     * /Final[^.]{0,80}(January|September)/ and failed on a correct sentence in the
     * FAQ ("a Final student is never offered a September attempt"). A whole-page
     * regex cannot tell a claim from its denial, so the check has to be anchored
     * to the element that makes the claim.
     */
    const finalSessions = screen.getByText('May and November').closest('div') as HTMLElement
    expect(finalSessions).toHaveTextContent('2 attempts a year')
    expect(finalSessions).toHaveTextContent('CA Final sessions')

    // Both other levels keep three attempts, and must not inherit Final's
    // calendar.
    const threeAttemptLevels = screen.getAllByText('January, May and September')
    expect(threeAttemptLevels).toHaveLength(2)
    for (const node of threeAttemptLevels) {
      expect(node.closest('div')).toHaveTextContent('3 attempts a year')
    }

    expect(screen.getByText(/ICAI notification dated 6 April 2026/)).toBeInTheDocument()
  })
})

describe('Landing page: honesty about what is unbuilt', () => {
  it('separates unshipped features from shipped ones', () => {
    renderLanding()
    const heading = screen.getByRole('heading', { name: /What is not built yet/i })
    const section = heading.closest('section')
    expect(section).not.toBeNull()
    const text = within(section as HTMLElement).getByRole('list').textContent ?? ''
    expect(text).toMatch(/AI assistant/i)
    // Video solutions were withdrawn from the product. They must not reappear as a
    // roadmap item either, because "coming soon" is a promise this build cannot keep.
    expect(text).not.toMatch(/video/i)
  })

  it('does not list video or AI inside the shipped feature grid', () => {
    renderLanding()
    const heading = screen.getByRole('heading', { name: /Built for the way CA is actually examined/i })
    const section = heading.closest('section') as HTMLElement
    const shipped = section.textContent ?? ''
    expect(shipped).not.toMatch(/video/i)
    expect(shipped).not.toMatch(/AI assistant/i)
  })

  it('answers the questions that block a signup', () => {
    renderLanding()
    // The five objections that stop a CA student signing up: whose material is
    // it, does it know the current exam rules, what happens when life
    // intervenes, does it cover my level, and what does it cost.
    for (const question of [
      'Is this ICAI material?',
      'Does CA Final really only have two attempts a year now?',
      'What happens when I miss days?',
      'Which levels does it cover?',
      'Do I need to pay?',
    ]) {
      expect(screen.getByText(question)).toBeInTheDocument()
    }
    // And it points at the full set rather than pretending to be it.
    expect(screen.getByRole('link', { name: /read all \d+ questions/i })).toHaveAttribute(
      'href',
      '/faq',
    )
  })
})

describe('Landing page: navigation', () => {
  it('links only to routes that exist, and never off-site', () => {
    renderLanding()
    const hrefs = screen.getAllByRole('link').map((a) => a.getAttribute('href') ?? '')
    expect(hrefs.length).toBeGreaterThan(5)

    for (const href of hrefs) {
      // A link that leaves the site is a visitor gone with no way back; there are
      // no external links on the public pages by design. (`mailto:` is the one
      // exception and it is not on this page.)
      expect(href.startsWith('http'), `off-site link: ${href}`).toBe(false)
      if (href.startsWith('#')) {
        expect(ANCHORS.has(href), `unknown anchor: ${href}`).toBe(true)
      } else if (ABSOLUTE_ANCHORS.has(href)) {
        // Fine: the target lives on the landing page and the layout scrolls to it.
        continue
      } else {
        // `<route>#<anchor>` is allowed when the ROUTE is real; the hash points at
        // a section of that page (`/features#question-bank`). The route is what
        // must exist - a section id is checked by the page that owns it.
        const route = href.split('#')[0] ?? ''
        expect(PUBLIC_ROUTES.has(route), `unrouted link: ${href}`).toBe(true)
      }
    }
  })

  it('routes the primary action to signup when signed out', () => {
    renderLanding()
    /*
     * `/signup`, not `/login`. They render the same component, but the URL is what
     * a visitor shares and what a returning student bookmarks, and "start
     * preparing" landing on a sign-IN page is the small wrongness that makes an
     * app feel unfinished.
     */
    const primary = screen.getAllByRole('link', { name: /start preparing free/i })
    expect(primary.length).toBeGreaterThan(0)
    for (const link of primary) expect(link).toHaveAttribute('href', '/signup')
  })

  it('every in-page anchor resolves to an element that exists', () => {
    const { container } = renderLanding()
    for (const anchor of ANCHORS) {
      const id = anchor.slice(1)
      expect(container.querySelector(`#${id}`), `#${id} is linked but not rendered`).not.toBeNull()
    }
  })

  it('sends a signed-in visitor to the dashboard instead of a signup form', () => {
    renderLanding({
      ...SIGNED_OUT,
      // Only `email` is read by the page; a full Supabase User is not needed and
      // constructing one would test the SDK, not the page.
      user: { email: 'student@example.com' } as unknown as AuthState['user'],
    })
    expect(screen.getByRole('link', { name: /open dashboard/i })).toHaveAttribute(
      'href',
      '/dashboard',
    )
    expect(screen.queryByRole('link', { name: /^start free$/i })).not.toBeInTheDocument()
  })
})

describe('Landing page: the calculator behaves honestly', () => {
  it('opens on a usable default rather than a verdict about the visitor', () => {
    renderLanding()
    const verdict = readVerdict()
    expect(verdict).not.toBeNull()
    expect(screen.getByLabelText(/Study hours you can hold/i)).toHaveValue('6')

    /*
     * The opening state must not be a scare.
     *
     * Defaulting to both groups at five hours a day produced "Not viable as
     * scoped" before the visitor had told us anything - a true answer to a
     * question they had not been asked, and the first thing they would read. A
     * landing page is allowed to open on a representative case; it is not
     * allowed to open on an insult.
     */
    expect(verdict?.label).not.toBe('Not viable as scoped')
    expect(verdict?.label).not.toBe('Falling short')
  })

  it('opens on one group, since group-wise is how most students sit', () => {
    renderLanding()
    /*
     * Exact accessible names, which also pins the spacing.
     *
     * /Group I/ alone matches "Group II", and the count is part of the name: an
     * earlier version of the pill rendered "Group I3" because JSX had dropped the
     * whitespace between the label and the count. Nothing else in the suite would
     * have noticed.
     */
    expect(screen.getByRole('button', { name: 'Group I 3' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('button', { name: 'Group II 3' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
    expect(screen.getByRole('button', { name: /Both groups/ })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })

  it('refuses to flatter an impossible pace', () => {
    renderLanding()
    clickLevel('Intermediate')
    setHours(1)
    const verdict = readVerdict()
    expect(verdict).not.toBeNull()
    // At an hour a day the page must not tell the student they are fine.
    expect(['Falling short', 'Not viable as scoped']).toContain(verdict?.label)
    const text = document.body.textContent ?? ''
    expect(text).toMatch(/will not fit|not viable|cannot be covered/i)
  })

  it('names the papers that do not fit rather than only counting them', () => {
    renderLanding()
    clickLevel('Final')
    setHours(1)
    const text = document.body.textContent ?? ''
    // Real paper names from the level's own list, so a student can act on it.
    const finalPapers = LEVELS.FINAL.groups.flatMap((g) => g.papers)
    expect(finalPapers.some((paper) => text.includes(paper))).toBe(true)
  })

  it('never worsens the verdict when the student adds hours', () => {
    renderLanding()
    clickLevel('Final')
    // Lower rank is a better verdict, so the rank may only fall as hours rise.
    // Monotonicity is the property that matters: a widget that flipped back to
    // "not viable" at 12 hours a day would be unusable in a way no single
    // snapshot assertion catches.
    let previousRank = VERDICT_LABELS.length - 1
    for (const hours of [1, 2, 3, 4, 6, 8, 10, 12, 14]) {
      setHours(hours)
      const verdict = readVerdict()
      if (verdict === null) throw new Error(`no verdict rendered at ${hours}h/day`)
      expect(verdict.rank, `verdict got worse when hours rose to ${hours}`).toBeLessThanOrEqual(
        previousRank,
      )
      previousRank = verdict.rank
    }
    // At the maximum the page offers, a Final student is no longer told the
    // scope is impossible.
    expect(readVerdict()?.label).not.toBe('Not viable as scoped')
  })

  it('shows the arithmetic behind the verdict', () => {
    const { container } = renderLanding()
    const text = container.textContent ?? ''
    expect(text).toMatch(/Papers in scope/)
    expect(text).toMatch(/Hours needed/)
    expect(text).toMatch(/Hours you have/)
    expect(text).toMatch(/Next targetable session/)
  })

  it('only ever names a session the level actually has', () => {
    // The bug this guards: a shared session calendar told CA Final students to
    // prepare for January, an attempt ICAI discontinued from May 2026.
    renderLanding()
    clickLevel('Final')
    expect(screen.getByText(/Next targetable session/i).parentElement?.textContent).toMatch(
      /(May|November) \d{4}/,
    )

    clickLevel('Intermediate')
    expect(screen.getByText(/Next targetable session/i).parentElement?.textContent).toMatch(
      /(January|May|September) \d{4}/,
    )
  })

  it('does not offer a group scope the level does not have', () => {
    renderLanding()
    // Foundation is a single group, so the scopes control is absent entirely.
    clickLevel('Foundation')
    expect(screen.queryByText(/^Group II$/)).not.toBeInTheDocument()

    // Switching back to a grouped level re-offers its own scopes only.
    clickLevel('Intermediate')
    expect(screen.getByRole('button', { name: /Group II/ })).toBeInTheDocument()
  })

  it('treats hours already done as reducing what is left', () => {
    renderLanding()
    clickLevel('Intermediate')
    setHours(3)
    const before = readVerdict()
    const covered = screen.getByLabelText(/Hours already done/i)
    fireEvent.change(covered, { target: { value: '500' } })
    const after = readVerdict()
    expect(before).not.toBeNull()
    expect(after).not.toBeNull()
    expect(after?.rank).toBeLessThanOrEqual(before?.rank ?? 4)
  })

  it('refuses to render Infinity for a zero requirement or zero hours', () => {
    renderLanding()
    setHours(0)
    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/Infinity|NaN/)
  })
})
