/**
 * The attempt calculator: the product's central behaviour, before sign-up.
 *
 * WHY THIS IS THE HERO ELEMENT RATHER THAN A SCREENSHOT
 *
 * Every study app claims to build "a personalised plan". None of them will tell
 * you the plan does not fit, because that is a bad sales message. This one will,
 * and it is the single most demonstrable difference: a visitor can type four
 * numbers and find out something true about their own attempt in ten seconds.
 *
 * It is also genuinely the server's behaviour. `app/services/study_planner.py`
 * refuses to schedule more minutes than the calendar holds and returns an
 * explicit coverage warning; this is the same rule, run client-side so it works
 * before there is an account to run it against.
 *
 * The estimates are labelled as estimates in the UI. Presenting a modelled
 * number as an ICAI figure would be the fastest way to lose the student this
 * whole page is trying to earn.
 */

import { useState } from 'react'

import {
  ESTIMATE_NOTE,
  LEVELS,
  LEVEL_ORDER,
  type AttemptScope,
  type Level,
  PASS_RULE,
  scopesForLevel,
  sessionNames,
} from '../lib/examData'
import {
  formatCoverage,
  formatHours,
  nextSession,
  planAttempt,
  type Verdict,
} from '../lib/attemptPlan'

const VERDICT_STYLES = {
  COMFORTABLE: { chip: 'bg-correct-50 text-correct-500', bar: 'bg-correct-500' },
  ON_TRACK: { chip: 'bg-correct-50 text-correct-500', bar: 'bg-correct-500' },
  TIGHT: { chip: 'bg-amber-50 text-amber-700', bar: 'bg-amber-500' },
  SHORT: { chip: 'bg-wrong-50 text-wrong-500', bar: 'bg-wrong-500' },
  NOT_VIABLE: { chip: 'bg-wrong-50 text-wrong-500', bar: 'bg-wrong-500' },
} satisfies Record<Verdict, { chip: string; bar: string }>

/** Today, fixed once per render pass so the session label cannot flicker. */
const TODAY = new Date()

export default function AttemptCalculator() {
  const [level, setLevel] = useState<Level>('INTERMEDIATE')
  /*
   * Group I rather than both groups as the opening state.
   *
   * Both groups is the more daunting scope, and opening on it produced "Not
   * viable as scoped" before the visitor had said anything about themselves - a
   * true answer to a question they had not been asked. Group-wise is also the
   * common case: most students sit one group at a time. Anyone sitting both
   * switches in one tap, and the default no longer reads as a verdict on them.
   *
   * Foundation has a single scope, 'BOTH', so it falls through to that.
   */
  const [scope, setScope] = useState<AttemptScope>('GROUP_I')
  // Six is a realistic full-time study day for a student between articleship
  // stints. Five made the opening verdict depend on the calendar month.
  const [hoursPerDay, setHoursPerDay] = useState(6)
  const [hoursAlreadyCovered, setHoursAlreadyCovered] = useState(0)

  const scopes = scopesForLevel(level)
  // A level switch can leave a scope the new level does not have (Group II is
  // meaningless at Foundation). Falling back here rather than in an effect keeps
  // the render a pure function of state.
  const activeScope = scopes.some((s) => s.id === scope) ? scope : scopes[0].id

  const session = nextSession(level, TODAY)
  const result = planAttempt({
    level,
    scope: activeScope,
    hoursPerDay,
    daysRemaining: session.days,
    hoursAlreadyCovered,
  })

  const spec = LEVELS[level]
  const styles = VERDICT_STYLES[result.verdict]
  const barWidth = Math.min(100, Math.round(result.coverage * 100))

  return (
    <section
      id="calculator"
      aria-labelledby="calculator-heading"
      className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="calculator-heading" className="text-lg font-semibold text-slate-900">
          Does your attempt actually fit?
        </h2>
        <p className="text-xs text-slate-500">{ESTIMATE_NOTE}</p>
      </div>

      <div className="mt-6 grid gap-8 lg:grid-cols-[minmax(0,20rem)_1fr]">
        {/* ------------------------------------------------------- inputs */}
        <div className="space-y-5">
          <fieldset>
            <legend className="text-sm font-medium text-slate-700">Level</legend>
            <div className="mt-2 grid grid-cols-3 gap-1 rounded-lg bg-slate-100 p-1">
              {LEVEL_ORDER.map((id) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={level === id}
                  onClick={() => setLevel(id)}
                  className={`min-h-11 rounded-md px-2 py-2 text-xs font-medium transition-colors ${
                    level === id
                      ? 'bg-white text-slate-900 shadow-sm'
                      : 'text-slate-600 hover:text-slate-900'
                  }`}
                >
                  {LEVELS[id].label.replace('CA ', '')}
                </button>
              ))}
            </div>
          </fieldset>

          {scopes.length > 1 && (
            <fieldset>
              <legend className="text-sm font-medium text-slate-700">Papers</legend>
              <div className="mt-2 flex flex-wrap gap-1">
                {scopes.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    aria-pressed={activeScope === s.id}
                    onClick={() => setScope(s.id)}
                    className={`min-h-11 rounded-full border px-3 py-2 text-xs font-medium transition-colors ${
                      activeScope === s.id
                        ? 'border-brand-600 bg-brand-50 text-brand-700'
                        : 'border-slate-300 text-slate-600 hover:border-slate-400'
                    }`}
                  >
                    {s.label}{' '}
                    {/* The explicit space is required: JSX drops the newline
                        between an expression and an element, which rendered
                        this as "Group I3". */}
                    <span className="text-slate-400">{s.papers.length}</span>
                  </button>
                ))}
              </div>
            </fieldset>
          )}

          <div>
            <label htmlFor="hours-per-day" className="text-sm font-medium text-slate-700">
              Study hours you can hold, per day
            </label>
            <div className="mt-2 flex items-center gap-3">
              <input
                id="hours-per-day"
                type="range"
                min={1}
                max={14}
                step={1}
                value={hoursPerDay}
                onChange={(e) => setHoursPerDay(Number(e.target.value))}
                className="h-11 w-full accent-brand-600"
              />
              <output
                htmlFor="hours-per-day"
                className="w-14 shrink-0 text-right text-lg font-semibold tabular-nums text-slate-900"
              >
                {hoursPerDay}h
              </output>
            </div>
            <p className="mt-1 text-xs text-slate-500">
              The number you actually manage on a working day. Sunday does not fix
              an average.
            </p>
          </div>

          <div>
            <label
              htmlFor="hours-covered"
              className="text-sm font-medium text-slate-700"
            >
              Hours already done
              <span className="ml-1 font-normal text-slate-400">optional</span>
            </label>
            <input
              id="hours-covered"
              type="number"
              inputMode="numeric"
              min={0}
              step={10}
              value={hoursAlreadyCovered}
              onChange={(e) => setHoursAlreadyCovered(Math.max(0, Number(e.target.value) || 0))}
              className="mt-2 block min-h-11 w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 tabular-nums outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
            />
          </div>
        </div>

        {/* ------------------------------------------------------ result */}
        <div className="lg:border-l lg:border-slate-200 lg:pl-8">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
            Next targetable session
          </p>
          <p className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">
            {session.label}
            <span className="ml-2 text-base font-normal text-slate-500">
              in {session.days} days
            </span>
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {spec.label} runs in {sessionNames(level)}.
          </p>

          {/* aria-live so a screen reader hears the verdict change as the
              sliders move, rather than having to re-find the region. */}
          <div aria-live="polite" className="mt-6">
            <div className="flex items-center gap-3">
              <span
                className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${styles.chip}`}
              >
                {result.verdictLabel}
              </span>
              <span className="text-sm text-slate-500 tabular-nums">
                {formatCoverage(result.coverage)} of the time needed
              </span>
            </div>

            <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full transition-[width] duration-300 ${styles.bar}`}
                style={{ width: `${barWidth}%` }}
              />
            </div>

            <p className="mt-3 text-sm leading-relaxed text-slate-700">
              {result.verdictDetail}
            </p>
          </div>

          <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-slate-200 pt-5 sm:grid-cols-3">
            <div>
              <dt className="text-xs text-slate-500">Papers in scope</dt>
              <dd className="mt-0.5 text-lg font-semibold tabular-nums text-slate-900">
                {result.papers.length}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">Hours needed</dt>
              <dd className="mt-0.5 text-lg font-semibold tabular-nums text-slate-900">
                {formatHours(result.hoursRequired)}
              </dd>
              <dd className="text-xs text-slate-400">
                {result.hoursPerPaper}h per paper
              </dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">Hours you have</dt>
              <dd className="mt-0.5 text-lg font-semibold tabular-nums text-slate-900">
                {formatHours(result.hoursAvailable)}
              </dd>
              <dd className="text-xs text-slate-400">
                {hoursPerDay}h &times; {session.days} days
              </dd>
            </div>
          </dl>

          <p className="mt-5 rounded-lg bg-slate-50 p-3 text-xs leading-relaxed text-slate-500">
            Every candidate sitting this level in {spec.latestPassRate.session} had a{' '}
            <strong className="font-semibold text-slate-700">
              {spec.latestPassRate.percent}%
            </strong>{' '}
            chance of clearing both groups. The pass rule is {PASS_RULE.perPaper}% in
            every paper and {PASS_RULE.aggregate}% aggregate. {PASS_RULE.note}
          </p>
        </div>
      </div>

      <p className="mt-6 border-t border-slate-200 pt-4 text-xs text-slate-500">
        This is the same rule the planner runs inside the app - it will not schedule
        more minutes than your calendar holds, and it says so when a chapter gets
        dropped instead of quietly skipping it.
      </p>
    </section>
  )
}
