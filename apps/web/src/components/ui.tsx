/**
 * The small set of primitives every screen uses.
 *
 * A design system would be overkill for ten screens; what is not overkill is one
 * place that decides how a LOADING state, an EMPTY state and a FAILED state look,
 * because those three are what a student actually sees when something is not
 * perfect. Left to each screen, one page shows a spinner forever, another shows
 * "no data" while it is still loading, and a third swallows the error.
 *
 * `QueryError` carries a request id, which is rendered on failures. That single
 * detail turns "the app is broken" into a log line somebody can find.
 */

import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'

import type { QueryError } from '../lib/queries'

export function Card({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string | undefined
}) {
  return (
    <div className={`rounded-lg border border-slate-200 bg-white p-5 shadow-sm ${className}`}>
      {children}
    </div>
  )
}

export function SectionHeading({
  title,
  action,
  subtitle,
}: {
  title: string
  action?: ReactNode
  subtitle?: string | undefined
}) {
  return (
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
      <div>
        <h2 className="text-lg font-medium text-slate-900">{title}</h2>
        {subtitle && <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>}
      </div>
      {action}
    </div>
  )
}

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <p role="status" className="py-6 text-center text-sm text-slate-500">
      {label}
    </p>
  )
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string
  body: string
  action?: ReactNode
}) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 p-6 text-center">
      <p className="font-medium text-slate-800">{title}</p>
      <p className="mx-auto mt-1 max-w-md text-sm text-slate-600">{body}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function ErrorState({ error, what }: { error: QueryError; what: string }) {
  return (
    <div role="alert" className="rounded-lg border border-wrong-500/30 bg-wrong-50 p-4">
      <p className="font-medium text-wrong-500">Could not load {what}</p>
      <p className="mt-1 text-sm text-slate-700">{error.message}</p>
      {error.isAuthError && (
        <p className="mt-2 text-sm text-slate-700">
          Your session may have expired. Reload the page to sign in again.
        </p>
      )}
      {error.requestId && (
        <p className="mt-2 font-mono text-xs text-slate-500">Request id: {error.requestId}</p>
      )}
    </div>
  )
}

type ButtonTone = 'primary' | 'secondary' | 'quiet'

const TONES: Record<ButtonTone, string> = {
  primary:
    'bg-brand-600 text-white hover:bg-brand-700 disabled:bg-slate-300 disabled:text-slate-500',
  secondary:
    'border border-slate-300 text-slate-700 hover:bg-slate-50 disabled:text-slate-400',
  quiet: 'text-brand-600 hover:underline disabled:text-slate-400',
}

export function Button({
  children,
  onClick,
  tone = 'primary',
  disabled,
  type = 'button',
  className = '',
  title,
}: {
  children: ReactNode
  onClick?: () => void
  tone?: ButtonTone
  disabled?: boolean
  type?: 'button' | 'submit'
  className?: string
  title?: string
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`rounded-md px-3.5 py-2 text-sm font-medium transition-colors ${
        TONES[tone]
      } disabled:cursor-not-allowed ${className}`}
    >
      {children}
    </button>
  )
}

/**
 * A link that looks like a Button.
 *
 * WHY THIS EXISTS. The landing page had hand-written `className` strings for every
 * CTA, and they had already drifted from the app's buttons in radius, height and
 * hover state - the same action looked like two different products depending on
 * which page you were on. Sharing `TONES` means a change to the primary button
 * reaches the marketing site and the authenticated app in one edit.
 *
 * `Link` rather than `<a href>`: every public CTA is an in-app route, and a full
 * page reload on "Start free" throws away the loaded bundle for nothing.
 */
export function ButtonLink({
  to,
  children,
  tone = 'primary',
  className = '',
  size = 'md',
}: {
  to: string
  children: ReactNode
  tone?: ButtonTone
  className?: string
  size?: 'md' | 'lg'
}) {
  const sizing = size === 'lg' ? 'min-h-12 px-6 text-base' : 'min-h-11 px-4 text-sm'
  return (
    <Link
      to={to}
      className={`inline-flex items-center justify-center rounded-md font-medium transition-colors ${TONES[tone]} ${sizing} ${className}`}
    >
      {children}
    </Link>
  )
}

export function Badge({
  children,
  tone = 'slate',
}: {
  children: ReactNode
  tone?: 'slate' | 'brand' | 'right' | 'wrong' | 'amber'
}) {
  const tones = {
    slate: 'bg-slate-100 text-slate-700',
    brand: 'bg-brand-50 text-brand-700',
    right: 'bg-right-50 text-right-600',
    wrong: 'bg-wrong-50 text-wrong-500',
    amber: 'bg-amber-100 text-amber-800',
  }
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  )
}

/** A labelled progress bar. Used for accuracy and the level ladder. */
export function Meter({
  value,
  label,
  tone = 'brand',
}: {
  /** 0..1 */
  value: number
  label: string
  tone?: 'brand' | 'right' | 'wrong'
}) {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0))
  const tones = { brand: 'bg-brand-600', right: 'bg-right-500', wrong: 'bg-wrong-500' }
  return (
    <div>
      <div className="flex items-baseline justify-between text-sm">
        <span className="text-slate-600">{label}</span>
        <span className="font-medium text-slate-800">{Math.round(clamped * 100)}%</span>
      </div>
      <div
        className="mt-1 h-2 overflow-hidden rounded-full bg-slate-100"
        role="progressbar"
        aria-valuenow={Math.round(clamped * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        <div className={`h-full ${tones[tone]}`} style={{ width: `${clamped * 100}%` }} />
      </div>
    </div>
  )
}
