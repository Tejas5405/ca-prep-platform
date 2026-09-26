/**
 * The marketing sections, as components.
 *
 * WHY THEY ARE SEPARATE FROM THE LANDING PAGE
 *
 * `/features` and `/faq` are real public routes, and every one of them reuses
 * something the landing page already has. Copy-pasting a feature grid into a second
 * page is how two pages start disagreeing about what the product does - and the
 * disagreement is always found by the visitor who read both.
 *
 * Each section owns its own heading and is addressable, so a link can point at a
 * section rather than at "somewhere on /features".
 */

import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'

import { FEATURES, HISTORICAL_POINTS, INGESTION_STEPS, ROADMAP, SIGNUP_HREF } from '../../lib/publicContent'
import { useAuth } from '../../hooks/authContext'
import { DASHBOARD_HREF } from '../../lib/publicContent'
import { Badge, ButtonLink, Card } from '../ui'

/** Consistent vertical rhythm, so sections differ in content rather than in spacing. */
export function Section({
  id,
  children,
  tone = 'white',
  className = '',
  labelledBy,
}: {
  id?: string
  children: ReactNode
  tone?: 'white' | 'muted' | 'dark'
  className?: string
  labelledBy?: string
}) {
  const tones = {
    white: 'bg-white',
    muted: 'border-y border-slate-200 bg-slate-50',
    dark: 'bg-slate-900',
  }
  return (
    <section
      id={id}
      aria-labelledby={labelledBy}
      className={`${tones[tone]} scroll-mt-20 ${className}`}
    >
      <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6 sm:py-20">{children}</div>
    </section>
  )
}

export function SectionHeading({
  id,
  eyebrow,
  title,
  body,
  align = 'left',
  level = 2,
}: {
  id?: string
  eyebrow?: string
  title: string
  body?: string
  align?: 'left' | 'center'
  /**
   * Heading level. Every public PAGE needs exactly one `h1` - a page whose first
   * heading is an `h2` reads to a screen reader as a fragment of another page, and
   * a crawler has nothing to use as the page's subject. Sections keep the default.
   */
  level?: 1 | 2
}) {
  const Heading = level === 1 ? 'h1' : 'h2'
  const size = level === 1 ? 'text-4xl sm:text-5xl' : 'text-3xl'
  return (
    <div className={align === 'center' ? 'mx-auto max-w-2xl text-center' : 'max-w-2xl'}>
      {eyebrow && (
        <p className="text-xs font-semibold tracking-wide text-brand-600 uppercase">{eyebrow}</p>
      )}
      <Heading
        id={id}
        className={`mt-2 ${size} font-semibold tracking-tight text-slate-900 text-balance`}
      >
        {title}
      </Heading>
      {body && <p className="mt-3 text-base leading-relaxed text-slate-600">{body}</p>}
    </div>
  )
}

/**
 * The feature grid.
 *
 * Bento-ish: the first two cards are wider on large screens, so the grid has a
 * focal point instead of six identical tiles. Only AVAILABLE features are rendered
 * by default - an unshipped feature in this grid is the marketing lie the roadmap
 * section exists to prevent.
 */
export function FeatureGrid({ includeUnbuilt = false }: { includeUnbuilt?: boolean }) {
  const items = includeUnbuilt ? FEATURES : FEATURES.filter((f) => f.status === 'AVAILABLE')

  return (
    <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {items.map((feature, index) => (
        <article
          key={feature.id}
          id={feature.id}
          className={`scroll-mt-24 rounded-xl border border-slate-200 bg-white p-6 transition-colors hover:border-slate-300 ${
            index < 2 ? 'lg:col-span-1' : ''
          }`}
        >
          <div className="flex items-start justify-between gap-3">
            <h3 className="text-base font-semibold tracking-tight text-slate-900">
              {feature.title}
            </h3>
            {feature.status === 'IN_BUILD' && <Badge tone="amber">In build</Badge>}
          </div>
          <p className="mt-2 text-sm leading-relaxed text-slate-600">{feature.body}</p>
        </article>
      ))}
    </div>
  )
}

/** The five-step story. Numbered because the order is the advice. */
export function HowItWorks({ steps }: { steps: { title: string; body: string }[] }) {
  return (
    <ol className="mt-10 grid gap-8 sm:grid-cols-2 lg:grid-cols-3">
      {steps.map((step, index) => (
        <li key={step.title}>
          <span className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-slate-300 bg-white text-sm font-semibold tabular-nums text-slate-700">
            {index + 1}
          </span>
          <h3 className="mt-4 text-base font-semibold tracking-tight text-slate-900">{step.title}</h3>
          <p className="mt-2 text-sm leading-relaxed text-slate-600">{step.body}</p>
        </li>
      ))}
    </ol>
  )
}

/**
 * The ingestion pipeline.
 *
 * Drawn as a sequence with the human step set apart, because that is the claim
 * worth making: most tools in this category advertise "AI extracts questions from
 * your PDFs", and the honest version of that feature has a person in the middle of
 * it. The last two steps are styled differently on purpose - approval and
 * publication are two separate people's decisions, not one click.
 */
export function IngestionPipeline() {
  return (
    <ol className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {INGESTION_STEPS.map((step, index) => {
        const isHuman = step.title === 'Human QA' || step.title === 'Publish'
        return (
          <li
            key={step.title}
            className={`rounded-xl border p-5 ${
              isHuman ? 'border-brand-600/30 bg-brand-50/60' : 'border-slate-200 bg-white'
            }`}
          >
            <div className="flex items-center gap-2">
              <span
                className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold tabular-nums ${
                  isHuman ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-700'
                }`}
              >
                {index + 1}
              </span>
              <h3 className="text-sm font-semibold tracking-tight text-slate-900">{step.title}</h3>
            </div>
            <p className="mt-2 text-sm leading-relaxed text-slate-600">{step.body}</p>
          </li>
        )
      })}
    </ol>
  )
}

/** Historical-taxation handling: a product differentiator, not a legal footnote. */
export function HistoricalLaw() {
  return (
    <div className="grid gap-6 lg:grid-cols-2 lg:items-start">
      <div>
        <SectionHeading
          id="historical-heading"
          eyebrow="Taxation"
          title="Old law, still worth practising — but labelled"
          body="Taxation is the one subject where a correct answer can teach you the wrong law. Questions governed by an earlier Finance Act stay in the bank with their year attached, so you can practise the pattern without mistaking it for today's position."
        />
        <ul className="mt-6 space-y-4">
          {HISTORICAL_POINTS.map((point) => (
            <li key={point.title} className="flex gap-3">
              <span
                aria-hidden="true"
                className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-600"
              />
              <div>
                <p className="text-sm font-medium text-slate-900">{point.title}</p>
                <p className="mt-0.5 text-sm leading-relaxed text-slate-600">{point.body}</p>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div className="rounded-xl border border-amber-300 bg-amber-50/70 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="amber">Historical law</Badge>
          <Badge tone="slate">AY 2019–20 · Finance Act 2018</Badge>
        </div>
        <p className="mt-3 text-sm leading-relaxed text-slate-800">
          Compute the deduction available under section 80C for the assessment year 2019–20, given
          the following particulars.
        </p>
        <p className="mt-3 border-t border-amber-200 pt-3 text-xs leading-relaxed text-amber-900">
          Answered under the law applicable to AY 2019–20. The limits shown here have since changed —
          check the current Finance Act before applying them to a present-day problem.
        </p>
        <p className="mt-3 text-xs text-slate-500">
          This is the disclaimer a student sees, attached to the question rather than collected in a
          policy page somewhere else.
        </p>
      </div>
    </div>
  )
}

/**
 * The roadmap.
 *
 * Its own labelled section rather than a footnote. A visitor who signs up expecting
 * something that is not there was misled by the marketing page, which costs more than
 * a shorter feature list.
 */
export function RoadmapSection() {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-8 sm:p-10">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id="next-heading" className="text-xl font-semibold tracking-tight text-slate-900">
          What is not built yet
        </h2>
        <Badge tone="slate">In build</Badge>
      </div>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-slate-600">
        Listed here rather than folded into the features above, so nobody signs up expecting
        something that is not there yet.
      </p>
      <ul className="mt-6 grid gap-3 sm:grid-cols-2">
        {ROADMAP.map((item) => (
          <li key={item} className="flex items-start gap-2.5 text-sm text-slate-600">
            <span aria-hidden="true" className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300" />
            {item}
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * An FAQ list, as native `details`/`summary` elements.
 *
 * Native because it is keyboard accessible and works with no JavaScript at all -
 * which keeps the public pages readable as documents rather than as applications
 * that must hydrate before they can be read.
 */
export function FaqList({ items }: { items: { q: string; a: string }[] }) {
  return (
    <div className="mt-8 divide-y divide-slate-200 border-y border-slate-200">
      {items.map((item) => (
        <details key={item.q} className="group py-4">
          <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-4 text-base font-medium text-slate-900">
            {item.q}
            <span
              aria-hidden="true"
              className="shrink-0 text-slate-400 transition-transform group-open:rotate-45"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path
                  d="M8 3v10M3 8h10"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
              </svg>
            </span>
          </summary>
          <p className="mt-3 max-w-3xl pr-8 text-sm leading-relaxed text-slate-600">{item.a}</p>
        </details>
      ))}
    </div>
  )
}

/**
 * The final CTA.
 *
 * Signed-in aware, like every other CTA on the public site: the one thing that
 * changes for a returning student is that they are no longer being asked to sign up.
 */
export function FinalCta() {
  const { user, loading } = useAuth()
  const signedIn = !loading && user !== null

  return (
    <Section tone="dark" className="!py-0">
      <div className="py-16 text-center sm:py-20">
        <h2 className="text-3xl font-semibold tracking-tight text-white text-balance sm:text-4xl">
          Turn preparation into a system.
        </h2>
        <p className="mx-auto mt-4 max-w-xl text-base leading-relaxed text-slate-300">
          A question bank, a dated plan and a revision queue that keeps up with you. Free to start,
          no card.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Link
            to={signedIn ? DASHBOARD_HREF : SIGNUP_HREF}
            className="inline-flex min-h-12 items-center rounded-md bg-white px-6 text-base font-medium text-slate-900 transition-colors hover:bg-slate-100"
          >
            {signedIn ? 'Open my dashboard' : 'Start preparing free'}
          </Link>
          <Link
            to="/pricing"
            className="inline-flex min-h-12 items-center rounded-md border border-slate-600 px-6 text-base font-medium text-white transition-colors hover:bg-slate-800"
          >
            See what is included
          </Link>
        </div>
      </div>
    </Section>
  )
}

/**
 * The plans table, fed by the API.
 *
 * WHY IT FETCHES RATHER THAN HARDCODES
 *
 * A price written into a React component is a price that disagrees with the price
 * the server charges, and the disagreement is discovered by a customer. The
 * catalogue is public, so this renders for a signed-out visitor with no auth round
 * trip.
 *
 * The fallback when the catalogue cannot be loaded says so. It does not invent a
 * price, and it does not hide the section: a visitor who reads "plans start free,
 * check the page" is better served than one shown a plausible wrong number.
 */
export function PlansTable({
  plans,
  error,
  loading,
}: {
  plans: { code: string; label: string; amountRupees: number; durationDays: number; tagline: string; features: string[]; recommended: boolean }[] | null
  error: Error | null
  loading: boolean
}) {
  if (loading) {
    return (
      <p role="status" className="mt-10 text-center text-sm text-slate-500">
        Loading plans…
      </p>
    )
  }

  if (error || !plans || plans.length === 0) {
    return (
      <Card className="mt-10 text-center">
        <p className="font-medium text-slate-800">Plans could not be loaded</p>
        <p className="mx-auto mt-1 max-w-md text-sm text-slate-600">
          The plan catalogue is served by the API. Nothing is shown here rather than a price that
          might be out of date.
        </p>
        <p className="mt-3 text-xs text-slate-500">
          You can {error ? 'retry in a moment' : 'sign up now'} — the free tier needs no payment
          details.
        </p>
      </Card>
    )
  }

  return (
    <div className="mt-10 grid gap-4 lg:grid-cols-3">
      {plans.map((plan) => (
        <article
          key={plan.code}
          className={`flex flex-col rounded-xl border bg-white p-6 ${
            plan.recommended ? 'border-brand-600 shadow-sm ring-1 ring-brand-600/20' : 'border-slate-200'
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-base font-semibold tracking-tight text-slate-900">{plan.label}</h3>
            {plan.recommended && <Badge tone="brand">Most students</Badge>}
          </div>
          <p className="mt-2 text-sm text-slate-600">{plan.tagline}</p>

          <p className="mt-4 text-3xl font-semibold tracking-tight tabular-nums text-slate-900">
            {plan.amountRupees === 0 ? 'Free' : `₹${plan.amountRupees.toLocaleString('en-IN')}`}
            {plan.amountRupees > 0 && (
              <span className="text-sm font-normal text-slate-500">
                {' '}
                / {plan.durationDays} days
              </span>
            )}
          </p>

          <ul className="mt-5 flex-1 space-y-2">
            {plan.features.map((feature) => (
              <li key={feature} className="flex items-start gap-2 text-sm text-slate-600">
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 16 16"
                  fill="none"
                  aria-hidden="true"
                  className="mt-0.5 shrink-0 text-brand-600"
                >
                  <path
                    d="M3 8.5l3 3 7-7"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
                {feature}
              </li>
            ))}
          </ul>

          <ButtonLink to={SIGNUP_HREF} className="mt-6 w-full" tone={plan.recommended ? 'primary' : 'secondary'}>
            {plan.amountRupees === 0 ? 'Start free' : 'Choose this plan'}
          </ButtonLink>
        </article>
      ))}
    </div>
  )
}
