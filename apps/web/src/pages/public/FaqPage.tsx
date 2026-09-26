/**
 * `/faq` — the full question set, grouped.
 *
 * WHY THE GROUPS ARE DEEP-LINKABLE
 *
 * The footer links to `/faq#content`, because the visitor who arrives from there is
 * the one asking the uncomfortable question: where do these questions actually come
 * from, and who checked them. Landing that person at the top of a seventeen-item
 * accordion and expecting them to scroll is how a sceptical visitor leaves.
 *
 * Each answer describes what the system does. Where something is not built, the
 * answer says so rather than staying silent — an FAQ that only answers the
 * questions with good answers is a brochure.
 */

import { Link } from 'react-router-dom'

import { PublicLayout } from '../../components/public/PublicLayout'
import { Seo } from '../../components/public/Seo'
import { FinalCta, Section, SectionHeading } from '../../components/public/sections'
import { ALL_FAQS, FAQ_GROUPS, SIGNUP_HREF } from '../../lib/publicContent'

export default function FaqPage() {
  return (
    <PublicLayout>
      <Seo
        title="FAQ — ICAI materials, content sourcing and billing | CA Prep"
        description="Answers about CA Prep: which levels are covered, how ICAI past papers become reviewed questions, how historical taxation questions are labelled, and how plans and payment work."
        path="/faq"
      />

      <Section labelledBy="faq-page-heading">
        <SectionHeading
          id="faq-page-heading"
          eyebrow="Questions"
          title="Answers, including the awkward ones"
          body="Four groups: what the product is, where the content comes from, what is not built yet, and how payment works."
                level={1}
      />
        <p className="mt-4 text-sm text-slate-500">
          {ALL_FAQS.length} answers. Nothing here is aspirational — where a feature is not built, the
          answer says so.
        </p>
      </Section>

      {FAQ_GROUPS.map((group, index) => (
        <Section
          key={group.id}
          id={group.id}
          tone={index % 2 === 0 ? 'muted' : 'white'}
          labelledBy={`${group.id}-heading`}
        >
          <h2
            id={`${group.id}-heading`}
            className="text-2xl font-semibold tracking-tight text-slate-900"
          >
            {group.heading}
          </h2>
          <div className="mt-6 divide-y divide-slate-200 border-y border-slate-200">
            {group.items.map((item) => (
              <details key={item.q} className="group py-4">
                <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-4 text-base font-medium text-slate-900">
                  {item.q}
                  <span
                    aria-hidden="true"
                    className="text-slate-400 transition-transform group-open:rotate-45"
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
                <p className="mt-3 max-w-3xl pr-8 text-sm leading-relaxed text-slate-600">
                  {item.a}
                </p>
              </details>
            ))}
          </div>
        </Section>
      ))}

      <Section labelledBy="faq-more-heading">
        <SectionHeading
          id="faq-more-heading"
          title="Still unanswered?"
          body="Two things are genuinely outside this page: whether your specific attempt fits, and what a plan costs on the day you read this. The first is a calculator, the second is fetched live."
        />
        <div className="mt-8 flex flex-wrap gap-3">
          <Link
            to="/#calculator"
            className="inline-flex min-h-12 items-center rounded-md border border-slate-300 px-6 text-base font-medium text-slate-700 transition-colors hover:border-slate-400 hover:bg-slate-50"
          >
            Check my attempt
          </Link>
          <Link
            to="/pricing"
            className="inline-flex min-h-12 items-center rounded-md border border-slate-300 px-6 text-base font-medium text-slate-700 transition-colors hover:border-slate-400 hover:bg-slate-50"
          >
            See plans
          </Link>
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
