/**
 * `/features` — the long-form version of the landing page's feature grid.
 *
 * WHY THIS PAGE EXISTS SEPARATELY
 *
 * A landing page has one job and roughly ninety seconds to do it, so its feature
 * grid stays at the level of outcomes. A visitor who has decided they are
 * interested then wants the details: what exactly does the planner read, what
 * happens when OCR produces garbage, how does the LDR flag work. Those answers do
 * not belong on the landing page, and hiding them in an FAQ is worse - this is the
 * page a technical or sceptical buyer reads.
 *
 * Every feature carries a status. The unbuilt ones are shown with an "In build"
 * badge rather than omitted, because a page that lists only shipped features reads
 * as a complete product, and the two deferred items are the two a student is most
 * likely to ask about.
 */

import { Link } from 'react-router-dom'

import { PublicLayout } from '../../components/public/PublicLayout'
import { Seo } from '../../components/public/Seo'
import {
  IngestionPipeline,
  FeatureGrid,
  FinalCta,
  HistoricalLaw,
  Section,
  SectionHeading,
} from '../../components/public/sections'
import { FEATURES, SIGNUP_HREF } from '../../lib/publicContent'

export default function FeaturesPage() {
  return (
    <PublicLayout>
      <Seo
        title="Features — question bank, mocks, planner and revision | CA Prep"
        description="What CA Prep does in detail: a searchable ICAI past-paper bank, chapter-wise practice, timed mocks, spaced revision, a study planner, doubts and progress analytics."
        path="/features"
      />

      <Section labelledBy="features-hero">
        <SectionHeading
          id="features-hero"
          eyebrow="Features"
          title="Everything in the platform, and how each part works"
          body="Written as descriptions of the system rather than as a list of nouns, because the difference between a feature that works and one that is advertised is usually in the details below the headline."
                level={1}
      />
        <p className="mt-6 text-sm text-slate-600">
          Not sure where to start?{' '}
          <Link to="/#calculator" className="font-medium text-brand-600 hover:underline">
            Check whether your attempt fits
          </Link>{' '}
          first — it takes four numbers and it is the honest reason most people sign up.
        </p>
      </Section>

      <Section tone="muted">
        <FeatureGrid includeUnbuilt />
      </Section>

      {/* The two differentiators, in full. They are the reason someone picks this
          over a folder of PDFs, so they get their own sections rather than a card. */}
      <Section labelledBy="content-heading">
        <SectionHeading
          id="content-heading"
          eyebrow="Content pipeline"
          title="From a PDF to a publishable question"
          body="The pipeline is deliberately boring, and deliberately supervised."
        />
        <IngestionPipeline />
      </Section>

      <Section tone="muted" labelledBy="historical-heading">
        <HistoricalLaw />
      </Section>

      <Section>
        <div className="rounded-2xl border border-slate-200 bg-white p-8 sm:p-10">
          <h2 className="text-xl font-semibold tracking-tight text-slate-900">
            What every question carries
          </h2>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-slate-600">
            These are stored on the row, not inferred at render time — which is why they survive
            being filtered, exported or read by something other than this app.
          </p>
          <dl className="mt-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {[
              { term: 'Placement', detail: 'Course, paper, chapter and topic, with a foreign key behind each one.' },
              { term: 'Source', detail: 'Paper name, year, session and the page number in the original PDF.' },
              { term: 'Marks and type', detail: 'Marks, negative marking and question type, because scoring depends on all three.' },
              { term: 'Answer and explanation', detail: 'A key for objective questions, a model answer for descriptive ones, and the reasoning.' },
              { term: 'Historical status', detail: 'A flag plus the Finance Act year it was written against, with a disclaimer when it is set.' },
              { term: 'Verification', detail: 'Who reviewed it and when, plus the extraction confidence for anything that came from a PDF.' },
            ].map((row) => (
              <div key={row.term}>
                <dt className="text-sm font-semibold text-slate-900">{row.term}</dt>
                <dd className="mt-1 text-sm leading-relaxed text-slate-600">{row.detail}</dd>
              </div>
            ))}
          </dl>
        </div>
      </Section>

      <Section tone="muted">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <p className="max-w-xl text-sm text-slate-600">
            {FEATURES.filter((feature) => feature.status === 'AVAILABLE').length} features are built
            and tested; the two marked &ldquo;in build&rdquo; above are not, and no screen in the
            product pretends otherwise.
          </p>
          <Link
            to={SIGNUP_HREF}
            className="inline-flex min-h-12 items-center rounded-md bg-brand-600 px-6 text-base font-medium text-white transition-colors hover:bg-brand-700"
          >
            Start preparing free
          </Link>
        </div>
      </Section>

      <FinalCta />
    </PublicLayout>
  )
}
