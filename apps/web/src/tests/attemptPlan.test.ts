/**
 * Tests for the attempt-planning model and the CA facts it reads.
 *
 * The model runs on the landing page before a student has an account, so a bug
 * here is the first thing anyone sees. The date tests matter most: CA Final lost
 * its January and September attempts in April 2026, and a planner that still
 * offers one sends a student to prepare for an exam that will not be held.
 */

import { describe, expect, it } from 'vitest'

import {
  LEVELS,
  LEVEL_ORDER,
  PASS_RULE,
  papersForScope,
  scopesForLevel,
  sessionNames,
} from '../lib/examData'
import {
  MIN_DAYS_TO_SESSION,
  formatCoverage,
  formatHours,
  nextSession,
  planAttempt,
  verdictFor,
} from '../lib/attemptPlan'

// ---------------------------------------------------------------- CA facts

describe('exam facts', () => {
  it('runs CA Final twice a year, in May and November only', () => {
    // ICAI notification dated 6 April 2026 discontinued the January and
    // September windows for Final from the May 2026 session onwards.
    expect(LEVELS.FINAL.sessionMonths).toEqual([5, 11])
  })

  it('never offers CA Final a January attempt', () => {
    // Stated separately from the test above because this is the specific
    // regression that matters, and it reads clearly when it fails.
    expect(LEVELS.FINAL.sessionMonths).not.toContain(1)
    expect(LEVELS.FINAL.sessionMonths).not.toContain(9)
  })

  it('keeps three attempts a year for Foundation and Intermediate', () => {
    // The April 2026 notification covered Final only.
    for (const level of ['FOUNDATION', 'INTERMEDIATE'] as const) {
      expect(LEVELS[level].sessionMonths).toEqual([1, 5, 9])
    }
  })

  it('names Final sessions the way a student would say them', () => {
    expect(sessionNames('FINAL')).toBe('May and November')
    expect(sessionNames('FOUNDATION')).toBe('January, May and September')
  })

  it('gives every paper list the right length for its level', () => {
    expect(papersForScope('FOUNDATION', 'BOTH')).toHaveLength(4)
    expect(papersForScope('INTERMEDIATE', 'GROUP_I')).toHaveLength(3)
    expect(papersForScope('INTERMEDIATE', 'GROUP_II')).toHaveLength(3)
    expect(papersForScope('INTERMEDIATE', 'BOTH')).toHaveLength(6)
    expect(papersForScope('FINAL', 'BOTH')).toHaveLength(6)
  })

  it('offers Foundation no group split, because it has none', () => {
    expect(scopesForLevel('FOUNDATION')).toHaveLength(1)
    expect(scopesForLevel('INTERMEDIATE')).toHaveLength(3)
  })

  it('uses the New Scheme paper names', () => {
    expect(papersForScope('FINAL', 'GROUP_I')).toContain('Financial Reporting')
    expect(papersForScope('FINAL', 'GROUP_II')).toContain(
      'Integrated Business Solutions',
    )
    // CA Final dropped to six compulsory papers; electives are gone.
    expect(papersForScope('FINAL', 'BOTH')).not.toContain('Elective')
  })

  it('states the published pass rule', () => {
    expect(PASS_RULE.perPaper).toBe(40)
    expect(PASS_RULE.aggregate).toBe(50)
  })
})

describe('published pass percentages are internally consistent', () => {
  // A typo in appeared/passed would render as a wrong percentage on the landing
  // page, which is the fastest way to lose a student's trust. The published
  // percent must equal passed/appeared, so a transposed digit fails here.
  for (const level of LEVEL_ORDER) {
    for (const rate of [LEVELS[level].latestPassRate, ...LEVELS[level].passRateHistory]) {
      it(`${level} ${rate.session}: ${rate.passed}/${rate.appeared} = ${rate.percent}%`, () => {
        expect(rate.passed).toBeLessThanOrEqual(rate.appeared)
        const computed = (rate.passed / rate.appeared) * 100
        expect(computed).toBeCloseTo(rate.percent, 1)
      })
    }
  }

  it('has history in newest-first order and starting with the latest', () => {
    for (const level of LEVEL_ORDER) {
      const spec = LEVELS[level]
      expect(spec.passRateHistory[0]).toEqual(spec.latestPassRate)
      expect(spec.passRateHistory.length).toBeGreaterThan(1)
    }
  })
})

// ------------------------------------------------------------ next session

describe('nextSession', () => {
  const sept24 = new Date(Date.UTC(2026, 8, 24))

  it('sends a CA Final student to November, not January', () => {
    const session = nextSession('FINAL', sept24)
    expect(session.label).toBe('November 2026')
  })

  it('sends an Intermediate student to January, because Final rules do not apply', () => {
    // September 2026 is under MIN_DAYS_TO_SESSION away on 24 September, so the
    // next targetable session is January 2027.
    const session = nextSession('INTERMEDIATE', sept24)
    expect(session.label).toBe('January 2027')
  })

  it('skips a session that is too close to prepare for', () => {
    const sixDaysBeforeMay = new Date(Date.UTC(2026, 3, 25))
    const session = nextSession('FOUNDATION', sixDaysBeforeMay)
    expect(session.label).toBe('September 2026')
    expect(session.days).toBeGreaterThanOrEqual(MIN_DAYS_TO_SESSION)
  })

  it('rolls over the year correctly', () => {
    const dec = new Date(Date.UTC(2026, 11, 15))
    // Final: November has gone, so the next is May 2027.
    expect(nextSession('FINAL', dec).label).toBe('May 2027')
    // Foundation: January 2027 is next.
    expect(nextSession('FOUNDATION', dec).label).toBe('January 2027')
  })

  it('always returns a forward-looking, non-negative day count', () => {
    for (const level of LEVEL_ORDER) {
      const session = nextSession(level, sept24)
      expect(session.days).toBeGreaterThan(0)
      expect(Number.isFinite(session.days)).toBe(true)
    }
  })
})

// -------------------------------------------------------------- the model

describe('verdictFor', () => {
  it('maps coverage onto named bands', () => {
    expect(verdictFor(2)).toBe('COMFORTABLE')
    expect(verdictFor(1.25)).toBe('COMFORTABLE')
    expect(verdictFor(1)).toBe('ON_TRACK')
    expect(verdictFor(0.99)).toBe('TIGHT')
    expect(verdictFor(0.85)).toBe('TIGHT')
    expect(verdictFor(0.84)).toBe('SHORT')
    expect(verdictFor(0.6)).toBe('SHORT')
    expect(verdictFor(0.59)).toBe('NOT_VIABLE')
  })
})

describe('planAttempt', () => {
  const realistic = {
    level: 'INTERMEDIATE' as const,
    scope: 'BOTH' as const,
    hoursPerDay: 6,
    daysRemaining: 200,
    hoursAlreadyCovered: 0,
  }

  it('totals the requirement from papers and per-paper hours', () => {
    const result = planAttempt(realistic)
    expect(result.papers).toHaveLength(6)
    expect(result.hoursRequired).toBe(6 * LEVELS.INTERMEDIATE.hoursPerPaper)
    expect(result.hoursAvailable).toBe(1200)
  })

  it('reports a comfortable plan honestly rather than inflating the requirement', () => {
    const result = planAttempt({ ...realistic, daysRemaining: 400 })
    expect(result.verdict).toBe('COMFORTABLE')
    expect(result.papersAtRisk).toEqual([])
  })

  it('calls out a plan that cannot cover the syllabus', () => {
    // 6 papers x 170h = 1020h needed; 60 days x 3h = 180h available.
    const result = planAttempt({ ...realistic, hoursPerDay: 3, daysRemaining: 60 })
    expect(result.verdict).toBe('NOT_VIABLE')
    expect(result.shortfallHours).toBeGreaterThan(0)
    expect(result.papersAtRisk.length).toBeGreaterThan(0)
  })

  it('names the papers that do not fit, not just a count', () => {
    // "Drop 2 papers" is abstract. Naming them is a decision the student
    // already knows is bad, which is the point.
    const result = planAttempt({ ...realistic, hoursPerDay: 3, daysRemaining: 60 })
    // Last papers of the Intermediate list, dropped from the end.
    expect(result.papersAtRisk).toContain('Financial Management and Strategic Management')
    expect(result.verdictDetail).toContain('Financial Management and Strategic Management')
    // Guard against the opposite mistake: a Final paper cannot appear in an
    // Intermediate plan.
    expect(result.papersAtRisk).not.toContain('Integrated Business Solutions')
  })

  it('never names more papers at risk than exist', () => {
    const result = planAttempt({ ...realistic, hoursPerDay: 0, daysRemaining: 1 })
    expect(result.papersAtRisk.length).toBeLessThanOrEqual(result.papers.length)
  })

  it('counts hours already covered against the requirement', () => {
    const fresh = planAttempt(realistic)
    const partly = planAttempt({ ...realistic, hoursAlreadyCovered: 170 })
    expect(partly.coverage).toBeGreaterThan(fresh.coverage)
  })

  it('does not go negative when more hours are logged than required', () => {
    const result = planAttempt({ ...realistic, hoursAlreadyCovered: 99_999 })
    expect(result.shortfallHours).toBe(0)
    expect(result.coverage).toBeGreaterThanOrEqual(1)
  })

  it('handles zero study hours without producing NaN or Infinity', () => {
    // These would render as "NaN%" or "Infinity%" in the UI, and a student
    // would reasonably conclude the whole thing is fake.
    const result = planAttempt({ ...realistic, hoursPerDay: 0 })
    expect(Number.isFinite(result.coverage)).toBe(true)
    expect(Number.isNaN(result.coverage)).toBe(false)
    expect(result.coverage).toBe(0)
    expect(Number.isFinite(result.shortfallHours)).toBe(true)
  })

  it('handles zero days remaining', () => {
    const result = planAttempt({ ...realistic, daysRemaining: 0 })
    expect(result.coverage).toBe(0)
    expect(result.verdict).toBe('NOT_VIABLE')
    expect(result.verdictDetail).toBeTruthy()
  })

  it('survives negative inputs from a stray input event', () => {
    const result = planAttempt({ ...realistic, hoursPerDay: -5, daysRemaining: -10 })
    expect(Number.isFinite(result.coverage)).toBe(true)
    expect(result.coverage).toBe(0)
  })

  it('treats an exact fit as on track rather than comfortable', () => {
    const required = 6 * LEVELS.INTERMEDIATE.hoursPerPaper
    const result = planAttempt({
      ...realistic,
      hoursPerDay: required / 200,
      daysRemaining: 200,
    })
    expect(result.verdict).toBe('ON_TRACK')
  })

  it('always produces a verdict label and a sentence to show', () => {
    for (const hoursPerDay of [0, 1, 3, 6, 10, 16]) {
      const result = planAttempt({ ...realistic, hoursPerDay })
      expect(result.verdictLabel.length).toBeGreaterThan(0)
      expect(result.verdictDetail.length).toBeGreaterThan(0)
    }
  })
})

describe('formatters', () => {
  it('renders coverage as a whole percentage', () => {
    expect(formatCoverage(1)).toBe('100%')
    expect(formatCoverage(0.8471)).toBe('85%')
    expect(formatCoverage(0)).toBe('0%')
  })

  it('shortens four-digit hour counts so they fit a stat tile', () => {
    expect(formatHours(540)).toBe('540')
    expect(formatHours(1020)).toBe('1k')
    expect(formatHours(1440)).toBe('1.4k')
  })
})
