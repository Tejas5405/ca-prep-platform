/**
 * Attempt feasibility: can you cover this syllabus in the days you have?
 *
 * This is the same calculation `app/services/study_planner.py` performs on the
 * server, reduced to the part that needs no account. It exists on the landing
 * page because it is the product's most distinctive behaviour, and because a
 * student can judge it in ten seconds without signing up.
 *
 * THE DESIGN RULE: REFUSE TO FLATTER
 *
 * The tempting version of this widget always returns "great, you're on track".
 * That version produces a plan the student cannot finish, and they discover it
 * in April with a month to go. So this returns the shortfall, names the papers
 * that do not fit, and says so plainly. `study_planner.py` already returns an
 * explicit coverage warning rather than over-scheduling; this is the same
 * promise stated earlier.
 *
 * Every function here is pure, so the behaviour is tested without a browser.
 */

import {
  LEVELS,
  monthName,
  type AttemptScope,
  type Level,
  type PassRate,
  papersForScope,
} from './examData'

/**
 * How close a session may be before it is treated as effectively passed.
 *
 * DELIBERATELY SMALL, and the number was reduced from 45 after a test caught the
 * reason. An earlier version used 45 days, which meant a CA Final student 38 days
 * from the November attempt was shown a default of May next year - while they
 * were, in fact, registered for November. Students do sit attempts on short
 * notice, and a calendar that quietly edits them out is telling them something
 * false.
 *
 * The division of responsibility is now explicit:
 *
 *   * this module reports WHICH SESSION COMES NEXT - a fact
 *   * `planAttempt` reports whether the syllabus FITS - a judgement
 *
 * Conflating the two produced a "helpful" default that contradicted the real
 * exam calendar. Seven days is short enough to only exclude a session that has
 * already begun.
 */
export const MIN_DAYS_TO_SESSION = 7

export type Verdict = 'COMFORTABLE' | 'ON_TRACK' | 'TIGHT' | 'SHORT' | 'NOT_VIABLE'

export interface PlanInput {
  level: Level
  scope: AttemptScope
  /** Realistic study hours per day, not the aspirational number. */
  hoursPerDay: number
  /** Days from today to the exam session. */
  daysRemaining: number
  /** Hours already done towards these papers. */
  hoursAlreadyCovered: number
}

export interface PlanResult {
  papers: string[]
  hoursRequired: number
  hoursAvailable: number
  /** available / required. 1 means exactly enough, >1 spare, <1 short. */
  coverage: number
  verdict: Verdict
  verdictLabel: string
  verdictDetail: string
  shortfallHours: number
  /**
   * Papers that do not fit, worst case first. Named rather than counted, because
   * "drop 2 papers" is abstract and "you cannot finish Financial Reporting and
   * Indirect Tax Laws" is a decision the student already knows is bad.
   */
  papersAtRisk: string[]
  hoursPerPaper: number
}

const VERDICTS: Record<Verdict, { label: string; detail: string }> = {
  COMFORTABLE: {
    label: 'Comfortable',
    detail: 'You have room for revision and full mock papers. Use it.',
  },
  ON_TRACK: {
    label: 'On track',
    detail: 'Enough time for the syllabus and one full revision. Protect the mocks.',
  },
  TIGHT: {
    label: 'Tight',
    detail: 'Covers the syllabus only if you lose no days. Add hours or drop a paper.',
  },
  SHORT: {
    label: 'Falling short',
    detail: 'As planned, part of the syllabus will not be revised before the attempt.',
  },
  NOT_VIABLE: {
    label: 'Not viable as scoped',
    detail:
      'This scope cannot be covered at this pace. Cut the scope, not the revision.',
  },
}

/**
 * The next session a student could realistically target.
 *
 * Reads the level's own session months rather than assuming a global calendar.
 * CA Final is May and November; Foundation and Intermediate are January, May and
 * September. A shared list would tell a Final student to prepare for January,
 * which ICAI discontinued from May 2026.
 */
export function nextSession(
  level: Level,
  from: Date = new Date(),
): { date: Date; label: string; days: number } {
  const months = LEVELS[level].sessionMonths
  const candidates: Date[] = []

  // Two calendar years of sessions, so a December query still finds January.
  for (const yearOffset of [0, 1]) {
    const year = from.getUTCFullYear() + yearOffset
    for (const month of months) {
      candidates.push(new Date(Date.UTC(year, month - 1, 1)))
    }
  }

  const cutoff = from.getTime() + MIN_DAYS_TO_SESSION * 86_400_000
  const upcoming = candidates
    .filter((d) => d.getTime() >= cutoff)
    .sort((a, b) => a.getTime() - b.getTime())

  // The construction above always yields at least one candidate within two
  // years, so the fallback is unreachable; it exists so the return type is not
  // a lie and so a future change to the loop cannot produce a crash.
  const chosen = upcoming[0] ?? candidates.at(-1)
  if (chosen === undefined) {
    // Unreachable: the construction above always yields at least one candidate
    // within two years. Thrown rather than cast away so that a future edit to
    // the loop surfaces as a failing test instead of a wrong exam date.
    throw new Error('nextSession: no upcoming session found')
  }

  return {
    date: chosen,
    label: `${monthName(chosen.getUTCMonth() + 1)} ${chosen.getUTCFullYear()}`,
    days: Math.max(0, Math.round((chosen.getTime() - from.getTime()) / 86_400_000)),
  }
}

/**
 * Verdict thresholds.
 *
 * Held as data so the UI can show where a student sits rather than a bare
 * number, and so the boundaries are reviewable in one place.
 */
export function verdictFor(coverage: number): Verdict {
  // EXACT-FIT TOLERANCE, and it is not cosmetic.
  //
  // coverage is hours-available divided by hours-required, both of which come
  // from a student's own inputs. A plan that exactly matches the requirement
  // lands on something like 0.9999999999999998 in binary floating point - so a
  // student who has precisely enough time was told they were falling short.
  //
  // The epsilon absorbs that representation error only. It is 1000x smaller than
  // the gap between adjacent bands, so a genuine shortfall of even 0.1% still
  // reports as one.
  const EPSILON = 1e-6
  const c = coverage + EPSILON
  if (c >= 1.25) return 'COMFORTABLE'
  if (c >= 1.0) return 'ON_TRACK'
  if (c >= 0.85) return 'TIGHT'
  if (c >= 0.6) return 'SHORT'
  return 'NOT_VIABLE'
}

export function planAttempt(input: PlanInput): PlanResult {
  const spec = LEVELS[input.level]
  const papers = papersForScope(input.level, input.scope)

  const hoursRequired = papers.length * spec.hoursPerPaper

  // Hours already done reduce what remains. Clamped at zero: a student who has
  // done more than required should not see a negative requirement.
  const hoursRemaining = Math.max(0, hoursRequired - Math.max(0, input.hoursAlreadyCovered))

  const days = Math.max(0, input.daysRemaining)
  const hoursPerDay = Math.max(0, input.hoursPerDay)
  const hoursAvailable = days * hoursPerDay

  // Guard the division. A zero-hour requirement (no papers selected) is
  // "covered", but a zero available with a real requirement is "not viable" -
  // returning Infinity or NaN here would render as "Infinity%" in the UI.
  let coverage: number
  if (hoursRequired === 0) {
    coverage = 1
  } else if (hoursAvailable === 0) {
    coverage = 0
  } else {
    coverage = hoursAvailable / hoursRemaining
  }

  const verdict = verdictFor(coverage)
  const shortfallHours = Math.max(0, hoursRemaining - hoursAvailable)

  // Which papers do not fit, named from the END of the list backwards. The last
  // paper of a group is the conventional one to drop first, and naming from the
  // end keeps the output stable as the student changes the inputs.
  const papersShort = Math.min(
    papers.length,
    Math.ceil(shortfallHours / spec.hoursPerPaper),
  )
  const papersAtRisk = papersShort > 0 ? papers.slice(papers.length - papersShort) : []

  return {
    papers,
    hoursRequired,
    hoursAvailable,
    coverage,
    verdict,
    verdictLabel: VERDICTS[verdict].label,
    verdictDetail: verdictDetail(verdict, papersAtRisk, papersShort),
    shortfallHours,
    papersAtRisk,
    hoursPerPaper: spec.hoursPerPaper,
  }
}

/**
 * The sentence under the verdict.
 *
 * Built here rather than in JSX so the wording is covered by tests - this is the
 * copy that has to stay honest, and the one most likely to be softened by
 * accident during a redesign.
 */
function verdictDetail(verdict: Verdict, papersAtRisk: string[], papersShort: number): string {
  if (papersShort === 0) {
    return VERDICTS[verdict].detail
  }
  const list =
    papersAtRisk.length === 1
      ? papersAtRisk[0]
      : `${papersAtRisk.slice(0, -1).join(', ')} and ${papersAtRisk[papersAtRisk.length - 1]}`
  const noun = papersShort === 1 ? 'paper' : 'papers'
  return `${VERDICTS[verdict].detail} Roughly ${papersShort} ${noun} of revision will not fit: ${list}.`
}

/** "Times" formatting, since "1.00x" reads like a spreadsheet. */
export function formatCoverage(coverage: number): string {
  const pct = Math.round(coverage * 100)
  return `${pct}%`
}

export function formatHours(hours: number): string {
  if (hours >= 1000) return `${Math.round(hours / 100) / 10}k`
  return `${Math.round(hours)}`
}

/** Base rate for the scope the student chose, with its session label. */
export function baseRateFor(level: Level, scope: AttemptScope): PassRate {
  const spec = LEVELS[level]
  // Group-wise rates are not published for single groups in every session, so
  // the both-groups figure is used as the honest base rate for any scope.
  // Showing a single-group number would flatter the comparison.
  void scope
  return spec.latestPassRate
}
