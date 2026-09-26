/**
 * The public landing page.
 *
 * STRUCTURE, AND WHY IT IS THIS STRUCTURE
 *
 * The page answers four questions in order, because a visitor leaves when one goes
 * unanswered:
 *
 *   1. What is this?        -> hero, outcome-led and CA-specific
 *   2. Is it any good?      -> the calculator, immediately, before any pitch
 *   3. Can I trust you?     -> real ICAI figures, sourced and dated, and a plain
 *                              account of where the content comes from
 *   4. What do I do now?    -> one primary CTA, repeated, never varied
 *
 * The product previews sit between the promises and the proof, because a claim
 * about a study tool is worth nothing until the visitor can see the screen they
 * will be looking at for the next six months.
 *
 * DELIBERATE CHOICES THAT LOOK LIKE OMISSIONS
 *
 * No testimonials, no logos, no student counts. The product has no users yet, and a
 * fabricated logo bar is the most common way a landing page lies. The honest
 * substitute for social proof in a trust-poor category is verifiable third-party
 * DATA, so the proof strip carries ICAI pass percentages with their sessions and
 * candidate counts - numbers a visitor can check. When there are real students,
 * testimonials go where the doubt is: directly under the calculator, because that
 * is the moment a visitor thinks "does this actually work".
 *
 * No stock photography, no illustration library, no icon set. The page is type,
 * space, hairline rules, real numbers and product UI built from the same
 * components the application uses.
 *
 * VISUAL RHYTHM
 *
 * Sections alternate white and muted backgrounds rather than all looking the same,
 * and the product previews are the only place with a heavy visual - so the eye is
 * pulled to the product, not to the decoration.
 */

import { Link } from 'react-router-dom'

import AttemptCalculator from '../components/AttemptCalculator'
import { PublicLayout } from '../components/public/PublicLayout'
import { Seo } from '../components/public/Seo'
import {
  DashboardPreview,
  MockShowcase,
  PlannerShowcase,
  ProgressShowcase,
  QuestionBankShowcase,
} from '../components/public/Showcases'
import {
  FaqList,
  FeatureGrid,
  FinalCta,
  HistoricalLaw,
  HowItWorks,
  IngestionPipeline,
  PlansTable,
  RoadmapSection,
  Section,
  SectionHeading,
} from '../components/public/sections'
import { useAuth } from '../hooks/authContext'
import { useLoader } from '../lib/useLoader'
import { fetchPlans } from '../lib/queries'
import { LEVELS, LEVEL_ORDER, sessionNames, shortSession } from '../lib/examData'
import { ALL_FAQS, ANCHOR, SIGNUP_HREF, STEPS, TRUST_POINTS } from '../lib/publicContent'

/**
 * The landing page's six questions, chosen by the objection each one removes.
 *
 * Deliberately named rather than "the first six": the order they are written in
 * the content module is an editorial order for a reference page, and the landing
 * page needs the five that block a signup (whose material is this, does it know
 * the current exam rules, what happens when life intervenes, what is covered, what
 * does it cost) plus the one that is asked most often in conversation.
 */
const LANDING_QUESTIONS = [
  'Is this ICAI material?',
  'Does CA Final really only have two attempts a year now?',
  'Which levels does it cover?',
  'Can I practise chapter-wise?',
  'What happens when I miss days?',
  'Do I need to pay?',
] as const

const LANDING_FAQS = LANDING_QUESTIONS.map((question) =>
  ALL_FAQS.find((item) => item.q === question),
).flatMap((item) => (item ? [{ q: item.q, a: item.a }] : []))


export default function LandingPage() {
  const { user, loading } = useAuth()
  const signedIn = !loading && user !== null

  // The plan catalogue, fetched rather than written into the page: a price in a
  // React component is a price that eventually disagrees with the price charged.
  const plans = useLoader((signal) => fetchPlans(signal), [])

  return (
    <PublicLayout>
      <Seo
        title="CA Prep — practice, mock exams and a dated study plan for CA students"
        description="A question bank built from ICAI past papers, chapter-wise practice, timed mock exams, spaced revision and a study plan that re-plans when your week does. Foundation, Intermediate and Final."
        path="/"
      />

      {/* -------------------------------------------------------------- hero */}
      <section className="relative overflow-hidden">
        {/* Dot grid drawn in CSS: no image request, and it degrades to plain white
            if the browser ignores the gradient. */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 opacity-[0.45] [background-image:radial-gradient(circle_at_1px_1px,rgb(148_163_184/0.35)_1px,transparent_0)] [background-size:22px_22px] [mask-image:linear-gradient(to_bottom,black,transparent_80%)]"
        />

        <div className="relative mx-auto max-w-6xl px-4 pt-14 pb-12 sm:px-6 sm:pt-20">
          <div className="max-w-3xl">
            {/*
              Proof placed ABOVE the headline. Most pages bury their credibility
              mark below the fold; doubt exists before the copy does. A real figure
              the visitor can check is the honest version of a trust badge.
            */}
            <p className="inline-flex flex-wrap items-center gap-x-2 gap-y-1 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full rounded-full bg-wrong-500 opacity-60" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-wrong-500" />
              </span>
              <span className="font-semibold tabular-nums text-slate-900">8.47%</span>
              <span>cleared both groups of CA Intermediate, May 2026</span>
              <span className="text-slate-400">· ICAI</span>
            </p>

            <h1 className="mt-6 text-4xl font-semibold tracking-tight text-slate-900 text-balance sm:text-5xl lg:text-6xl">
              Your CA attempt, planned to the day.
            </h1>

            <p className="mt-5 max-w-2xl text-lg leading-relaxed text-slate-600">
              CA Prep turns the ICAI syllabus and the days you have left into a plan you can finish
              &mdash; built on real past papers, with revision that resurfaces a chapter right before
              you would forget it.
            </p>

            <div className="mt-8 flex flex-wrap items-center gap-x-4 gap-y-3">
              <Link
                to={signedIn ? '/dashboard' : SIGNUP_HREF}
                className="inline-flex min-h-12 items-center rounded-md bg-brand-600 px-6 text-base font-medium text-white transition-colors hover:bg-brand-700"
              >
                {signedIn ? 'Go to my plan' : 'Start preparing free'}
              </Link>
              <a
                href="#product"
                className="inline-flex min-h-12 items-center rounded-md border border-slate-300 px-6 text-base font-medium text-slate-700 transition-colors hover:border-slate-400 hover:bg-slate-50"
              >
                Explore the platform
              </a>
              {/* The calculator is the differentiated thing on this page, so it gets
                  a link of its own rather than being a button among buttons. */}
              <a
                href={`#${ANCHOR.calculator}`}
                className="inline-flex min-h-11 items-center text-base font-medium text-brand-600 hover:underline"
              >
                Check my attempt
              </a>
            </div>

            <p className="mt-3 text-sm text-slate-500">
              No card. Takes about a minute &mdash; there is nothing to install.
            </p>
          </div>

          {/* -------------------------------------------------- the calculator */}
          <div className="mt-12 sm:mt-16">
            <AttemptCalculator />
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------ proof strip */}
      <Section tone="muted" labelledBy="results-heading">
        <h2 id="results-heading" className="text-sm font-semibold tracking-tight text-slate-900">
          What the exam actually looks like
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-slate-600">
          ICAI publishes these after every session. They are here because the gap between sitting one
          group and sitting both is the single most useful number in CA planning.
        </p>

        <div className="mt-8 grid gap-4 sm:grid-cols-3">
          {LEVEL_ORDER.map((level) => {
            const spec = LEVELS[level]
            const rate = spec.latestPassRate
            const peak = Math.max(...spec.passRateHistory.map((attempt) => attempt.percent))
            return (
              <div key={level} className="rounded-xl border border-slate-200 bg-white p-5">
                <p className="text-sm font-medium text-slate-900">{spec.label}</p>
                <p className="mt-3 text-4xl font-semibold tracking-tight tabular-nums text-slate-900">
                  {rate.percent}
                  <span className="text-2xl text-slate-400">%</span>
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  both groups &middot; {rate.session}
                </p>
                <p className="mt-3 border-t border-slate-100 pt-3 text-xs text-slate-500 tabular-nums">
                  {rate.passed.toLocaleString('en-IN')} passed of{' '}
                  {rate.appeared.toLocaleString('en-IN')} appeared
                </p>

                {/*
                  The recent sessions, not just the latest one. A single number
                  invites the reading "this is roughly the chance everyone has",
                  which is not what the figure means. Rates swing from 8.47% to
                  21.52% between sessions, which is the strongest argument on this
                  page for why how you prepare matters as much as how long.

                  Oldest on the left so the bars read as a progression. The full
                  session and its figure sit in a screen-reader-only label: a bar
                  chart with three letters under it is not readable aloud.
                */}
                <ul
                  className="mt-4 flex items-end gap-1.5"
                  aria-label={`${spec.label} pass percentage by session`}
                >
                  {[...spec.passRateHistory].reverse().map((attempt) => (
                    <li key={attempt.session} className="flex flex-1 flex-col items-center gap-1">
                      <span className="sr-only">
                        {attempt.session}: {attempt.percent}% of candidates passed.
                      </span>
                      <span aria-hidden="true" className="flex h-10 w-full items-end">
                        <span
                          className="w-full rounded-t bg-slate-300"
                          style={{
                            height: `${Math.max(10, Math.round((attempt.percent / peak) * 100))}%`,
                          }}
                        />
                      </span>
                      <span
                        aria-hidden="true"
                        className="text-[10px] leading-none text-slate-400 tabular-nums"
                      >
                        {shortSession(attempt.session)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )
          })}
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-3">
          {LEVEL_ORDER.map((level) => (
            <div key={level} className="rounded-xl border border-slate-200 bg-white p-5">
              <p className="text-xs font-medium tracking-wide text-slate-400 uppercase">
                {LEVELS[level].label} sessions
              </p>
              <p className="mt-2 text-base font-semibold text-slate-900">{sessionNames(level)}</p>
              <p className="mt-1 text-xs text-slate-500">
                {LEVELS[level].sessionMonths.length} attempts a year
              </p>
            </div>
          ))}
        </div>

        <p className="mt-4 text-xs text-slate-500">
          Source: ICAI result statistics, latest published session per level. CA Final moved to two
          attempts a year by ICAI notification dated 6 April 2026.
        </p>
      </Section>

      {/* -------------------------------------------------- product preview */}
      <Section id="product" labelledBy="product-heading" className="!pb-8">
        <SectionHeading
          id="product-heading"
          eyebrow="The platform"
          title="This is the screen you will spend six months in"
          body="A dashboard that says how you are doing is a scoreboard. One that names the chapters leaking marks, and puts today's work against your attempt date, is a plan."
        />
        <div className="mt-10">
          <DashboardPreview />
        </div>
      </Section>

      {/* ------------------------------------------------------------- trust */}
      <Section labelledBy="trust-heading" className="!pt-12">
        <SectionHeading
          id="trust-heading"
          eyebrow="Why the content can be trusted"
          title="Built so a wrong question cannot reach you"
          body="Every study product claims to be accurate. These are the four properties that make it true here, and each one is implemented rather than promised."
        />
        <div className="mt-10 grid gap-4 sm:grid-cols-2">
          {TRUST_POINTS.map((point) => (
            <div key={point.title} className="rounded-xl border border-slate-200 bg-white p-5">
              <h3 className="text-sm font-semibold tracking-tight text-slate-900">{point.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-600">{point.body}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* ---------------------------------------------------------- features */}
      <Section tone="muted" labelledBy="features-heading">
        <SectionHeading
          id="features-heading"
          title="Built for the way CA is actually examined"
          body="Group-wise attempts, three sessions a year at two levels and two at the third, and taxation that changes under you. Most study apps assume an annual exam and one paper list. CA is not that."
        />
        <FeatureGrid />
        <p className="mt-6 text-sm text-slate-600">
          <Link to="/features" className="font-medium text-brand-600 hover:underline">
            See every feature in detail
          </Link>
        </p>
      </Section>

      {/* ---------------------------------------------------- question bank */}
      <Section labelledBy="bank-heading">
        <SectionHeading
          id="bank-heading"
          eyebrow="Question bank"
          title="Find the question, then open it"
          body="Filter by course, paper, chapter, year, marks, question type and attempt. Search the wording. Anything you open shows its source paper and whether the law it tests has moved on since."
        />
        <div className="mt-10">
          <QuestionBankShowcase />
        </div>
      </Section>

      {/* ------------------------------------------------------------ mocks */}
      <Section tone="muted" labelledBy="mocks-heading">
        <SectionHeading
          id="mocks-heading"
          eyebrow="Mock exams"
          title="Sit the paper before the paper sits you"
          body="A running clock, a question palette with answered and marked states, and an attempt stored on the server — so a refresh, a closed tab or a dropped connection resumes rather than restarts."
        />
        <div className="mt-10">
          <MockShowcase />
        </div>
      </Section>

      {/* --------------------------------------------------------- progress */}
      <Section labelledBy="progress-heading">
        <SectionHeading
          id="progress-heading"
          eyebrow="Progress"
          title="Accuracy you can act on"
          body="Per-chapter accuracy with the chapter's weightage applied, so the areas the app tells you to revise are ranked by what they are worth — not by what you happened to attempt last."
        />
        <div className="mt-10">
          <ProgressShowcase />
        </div>
      </Section>

      {/* ----------------------------------------------------------- planner */}
      <Section tone="muted" labelledBy="planner-heading">
        <SectionHeading
          id="planner-heading"
          eyebrow="Study planner"
          title="A plan that survives a bad week"
          body="Your attempt date, the groups you are sitting and the hours you actually have. Miss a week and the plan rebuilds around what you completed, including an honest list of what no longer fits."
        />
        <div className="mt-10">
          <PlannerShowcase />
        </div>
      </Section>

      {/* ------------------------------------------------------- historical */}
      <Section id="historical" labelledBy="historical-heading">
        <HistoricalLaw />
      </Section>

      {/* -------------------------------------------------------- ingestion */}
      <Section tone="muted" labelledBy="ingestion-heading">
        <SectionHeading
          id="ingestion-heading"
          eyebrow="How content is built"
          title="Past papers become questions — with a person in the middle"
          body="Upload a paper and it is extracted, OCR'd where needed and segmented into candidate questions. Then it stops, and a reviewer takes over. Nothing an automated pipeline produces is ever published on its own."
        />
        <IngestionPipeline />
        <p className="mt-6 max-w-3xl text-sm leading-relaxed text-slate-600">
          This is the part worth being precise about: extraction is a draft generator, not a publisher.
          A question reaches your screen only after a reviewer has corrected its options, placed it in
          the syllabus and a Content Manager has published it — and the name of whoever signed it off
          is recorded on the question.
        </p>
      </Section>

      {/* ------------------------------------------------------ how it works */}
      <Section id={ANCHOR.how} labelledBy="how-heading">
        <SectionHeading
          id="how-heading"
          title="How it works"
          body="Five steps, in the order that actually works: know what you are sitting, find the questions, answer them, follow the plan, and let the results move the plan."
        />
        <HowItWorks steps={STEPS} />
      </Section>

      {/* -------------------------------------------------------- for whom */}
      <Section tone="muted" labelledBy="who-heading">
        <SectionHeading
          id="who-heading"
          eyebrow="Who it is for"
          title="Built for CA students who want a system, not a shelf of PDFs"
          body="You already have the material. What is usually missing is the answer to three questions: what do I do tonight, am I actually improving, and which chapters will cost me the attempt."
        />
        <ul className="mt-10 grid gap-4 sm:grid-cols-3">
          {[
            {
              title: 'Sitting groups separately',
              body: 'Group I, Group II and a combined attempt are three different plans here, because they are three different exams.',
            },
            {
              title: 'Repeating a paper',
              body: 'Your accuracy history stays, so a repeat attempt starts from what you already know rather than from chapter one.',
            },
            {
              title: 'Working while studying',
              body: 'Articleship and a job both mean fewer hours than the syllabus wants. The plan is built from the hours you have, not the hours a timetable assumes.',
            },
          ].map((item) => (
            <li key={item.title} className="rounded-xl border border-slate-200 bg-white p-5">
              <h3 className="text-sm font-semibold tracking-tight text-slate-900">{item.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-600">{item.body}</p>
            </li>
          ))}
        </ul>
      </Section>

      {/* -------------------------------------------------------------- faq */}
      <Section labelledBy="faq-heading">
        <SectionHeading
          id="faq-heading"
          eyebrow="Questions"
          title="What students ask before signing up"
          body="Six of the eighteen answers on the FAQ page. The rest cover content sourcing, historical taxation and billing."
        />
        {/*
          A SUBSET on purpose. The landing page's job is to remove the objections
          that stop a signup, not to be the reference - and a wall of accordions is
          where a visitor stops reading. The full set lives at /faq, which is linked
          from here, the navbar and the footer.
        */}
        <FaqList items={LANDING_FAQS} />
        <p className="mt-6 text-sm text-slate-600">
          <Link to="/faq" className="font-medium text-brand-600 hover:underline">
            Read all {ALL_FAQS.length} questions
          </Link>
        </p>
      </Section>

      {/* ---------------------------------------------------------- pricing */}
      <Section id="pricing" labelledBy="pricing-heading">
        <SectionHeading
          id="pricing-heading"
          eyebrow="Pricing"
          title="Start free. Upgrade when it earns it."
          body="The catalogue below is served by the API — the same numbers the server charges against when an order is created. Nothing is hardcoded into this page, so it cannot quote you a price that the checkout disagrees with."
        />
        <PlansTable plans={plans.data} error={plans.error} loading={plans.loading} />
        <p className="mt-4 text-xs text-slate-500">
          Prices in Indian Rupees. Payment is handled by Razorpay; a subscription activates only after
          a signature-verified webhook confirms it, never on a browser reporting success.
        </p>
      </Section>

      {/* -------------------------------------------------------- roadmap */}
      <Section tone="muted">
        <RoadmapSection />
      </Section>

      {/* ----------------------------------------------------------- final CTA */}
      <FinalCta />
    </PublicLayout>
  )
}
