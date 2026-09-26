/**
 * Shared furniture for the legal pages.
 *
 * A privacy policy and a set of terms are the two pages most likely to be written
 * once, badly, and never read again - which matters more here than in most
 * products, because the audience is students and the data includes what they get
 * wrong. Two things are therefore structural rather than editorial:
 *
 *   * every claim is written as a description of what the software actually does,
 *     so a section is only added when there is code behind it. The retention
 *     section, for instance, says what the schema does on delete rather than
 *     promising a schedule nothing implements.
 *   * the contact address comes from configuration rather than being invented here.
 *     A legal page with an address that bounces is worse than one that points at a
 *     supported channel.
 */

import type { ReactNode } from 'react'

import { SUPPORT_EMAIL } from '../../lib/site'
import { Link } from 'react-router-dom'

/** The date the text below was last changed. Kept explicit, not derived from a build. */
export const LEGAL_UPDATED = '25 September 2026'

export function LegalSection({ id, heading, children }: { id: string; heading: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-24 border-t border-slate-200 py-8">
      <h2 className="text-lg font-semibold tracking-tight text-slate-900">{heading}</h2>
      <div className="mt-3 space-y-3 text-sm leading-relaxed text-slate-600">{children}</div>
    </section>
  )
}

export function LegalList({ items }: { items: ReactNode[] }) {
  return (
    <ul className="space-y-2 pl-1">
      {items.map((item, index) => (
        <li key={index} className="flex gap-3">
          <span aria-hidden="true" className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  )
}

/** The contact line both pages need, so neither invents its own channel. */
export function ContactLine() {
  return (
    <p className="text-sm text-slate-600">
      Contact:{' '}
      <a href={`mailto:${SUPPORT_EMAIL}`} className="font-medium text-brand-600 hover:underline">
        {SUPPORT_EMAIL}
      </a>
    </p>
  )
}

/** A short "read together with" note: two documents that contradict each other are worse than one. */
export function LegalCrossLink({ to, label }: { to: string; label: string }) {
  return (
    <p className="text-sm text-slate-600">
      See also{' '}
      <Link to={to} className="font-medium text-brand-600 hover:underline">
        {label}
      </Link>
      .
    </p>
  )
}

export function LegalHeader({
  eyebrow,
  title,
  intro,
}: {
  eyebrow: string
  title: string
  intro: string
}) {
  return (
    <div className="max-w-3xl">
      <p className="text-xs font-semibold tracking-wide text-brand-600 uppercase">{eyebrow}</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-900 text-balance sm:text-4xl">
        {title}
      </h1>
      <p className="mt-4 text-base leading-relaxed text-slate-600">{intro}</p>
      <p className="mt-4 text-xs text-slate-500">Last updated: {LEGAL_UPDATED}</p>
    </div>
  )
}
