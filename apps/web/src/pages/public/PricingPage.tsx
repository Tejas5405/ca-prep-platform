/**
 * `/pricing` — the public plans page.
 *
 * The table is rendered from `GET /payments/plans`, which is a public endpoint, so
 * a signed-out visitor sees the real catalogue with no auth round trip. That is a
 * deliberate architectural choice rather than a convenience: the amount charged is
 * looked up server-side from the same catalogue when an order is created, so this
 * page can never quote a price the checkout disagrees with.
 *
 * WHAT THIS PAGE DOES NOT DO
 *
 * It does not start a checkout. Payments are configured per deployment, and the
 * checkout flow belongs in the authenticated app where the student's entitlement
 * state lives. A "Buy" button here that led to a signed-out 401 would be worse than
 * a sign-up button that works.
 */

import { Link } from 'react-router-dom'

import { PublicLayout } from '../../components/public/PublicLayout'
import { Seo } from '../../components/public/Seo'
import { FinalCta, PlansTable, Section, SectionHeading } from '../../components/public/sections'
import { useLoader } from '../../lib/useLoader'
import { fetchPlans } from '../../lib/queries'
import { FAQ_GROUPS, SIGNIN_HREF, SIGNUP_HREF } from '../../lib/publicContent'

export default function PricingPage() {
  const plans = useLoader((signal) => fetchPlans(signal), [])
  const paymentFaqs = FAQ_GROUPS.find((group) => group.id === 'pricing')?.items ?? []

  return (
    <PublicLayout>
      <Seo
        title="Pricing — free to start, no card required | CA Prep"
        description="CA Prep plans and what each tier includes. The free tier covers practice, revision, the planner and mock papers; prices are served by the API, not hardcoded into the page."
        path="/pricing"
      />

      <Section labelledBy="pricing-page-heading">
        <SectionHeading
          id="pricing-page-heading"
          eyebrow="Pricing"
          title="Free to start. Pay only if it earns it."
          body="Every price on this page comes from the platform's own catalogue, fetched when the page loads. Nothing here is written into the page, so it cannot drift from what the checkout charges."
                level={1}
      />
        <PlansTable plans={plans.data} error={plans.error} loading={plans.loading} />
      </Section>

      <Section tone="muted" labelledBy="included-heading">
        <SectionHeading
          id="included-heading"
          title="What is included at every tier"
          body="These are not upsells — they are the parts of the product that would be wrong to paywall, because the plan is only useful if the data behind it is complete."
        />
        <ul className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[
            { title: 'The full question bank', body: 'Practice is not rate-limited on the free tier. A bank you can only sample is not a bank.' },
            { title: 'Server-marked answers', body: 'Explanations come with the verdict, from the same endpoint, on every tier.' },
            { title: 'Spaced revision', body: 'Wrong answers schedule themselves regardless of plan — the queue is the product.' },
            { title: 'The study planner', body: 'Your attempt, your hours, your chapters, on one group at a time.' },
            { title: 'Full mock papers', body: 'Timed and scored, with a report per question. Discounted mocks would defeat the exercise.' },
            { title: 'Your own history', body: 'Attempts, accuracy and bookmarks stay yours, including after a paid period ends.' },
          ].map((item) => (
            <li key={item.title} className="rounded-xl border border-slate-200 bg-white p-5">
              <h3 className="text-sm font-semibold tracking-tight text-slate-900">{item.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-600">{item.body}</p>
            </li>
          ))}
        </ul>
      </Section>

      <Section labelledBy="payment-heading">
        <SectionHeading
          id="payment-heading"
          title="How payment works"
          body="The short version: the browser never tells this platform that a payment succeeded. Razorpay does, over a signed webhook the server verifies before anything is activated."
        />
        <ol className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            { title: 'You pick a plan', body: 'The amount is looked up on the server from the catalogue above — never taken from the browser.' },
            { title: 'Razorpay collects', body: 'Card and UPI details never touch this platform. Only the identifiers needed to reconcile the payment are stored.' },
            { title: 'The webhook is verified', body: 'Delivery is signed, and an unsigned or replayed event is rejected and archived rather than trusted.' },
            { title: 'Your access updates', body: 'The subscription activates once, idempotently, so a retried webhook cannot double-charge or extend you twice.' },
          ].map((step, index) => (
            <li key={step.title} className="rounded-xl border border-slate-200 bg-white p-5">
              <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold tabular-nums text-slate-700">
                {index + 1}
              </span>
              <h3 className="mt-3 text-sm font-semibold tracking-tight text-slate-900">{step.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-600">{step.body}</p>
            </li>
          ))}
        </ol>
      </Section>

      <Section tone="muted" labelledBy="billing-faq-heading">
        <SectionHeading id="billing-faq-heading" title="Billing questions" />
        <div className="mt-8 divide-y divide-slate-200 border-y border-slate-200">
          {paymentFaqs.map((item) => (
            <details key={item.q} className="group py-4">
              <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-4 text-base font-medium text-slate-900">
                {item.q}
                <span aria-hidden="true" className="text-slate-400 transition-transform group-open:rotate-45">
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                  </svg>
                </span>
              </summary>
              <p className="mt-3 max-w-2xl pr-8 text-sm leading-relaxed text-slate-600">{item.a}</p>
            </details>
          ))}
        </div>

        <p className="mt-6 text-sm text-slate-600">
          Already have an account?{' '}
          <Link to={SIGNIN_HREF} className="font-medium text-brand-600 hover:underline">
            Sign in
          </Link>{' '}
          to see your current plan and entitlements, or{' '}
          <Link to={SIGNUP_HREF} className="font-medium text-brand-600 hover:underline">
            create one free
          </Link>
          .
        </p>
      </Section>

      <FinalCta />
    </PublicLayout>
  )
}
