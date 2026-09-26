/**
 * CA course facts, as data.
 *
 * WHY THIS IS A MODULE AND NOT COPY PASTED INTO THE PAGE
 *
 * Every number here is a claim a student can check against ICAI, and several of
 * them CHANGE (exam frequency changed in April 2026; pass rates change every
 * session). Scattering them through JSX means the day one is wrong, nobody knows
 * how many places need fixing.
 *
 * Keeping them in one typed module also means the landing page, the planner and
 * any future admin screen read the same values. A student who sees "May and
 * November only" on the marketing page and then finds a January option in the
 * planner has lost trust in both.
 *
 * SOURCES
 *   Pass percentages  - ICAI result statistics, republished per session and
 *                       cross-checked across outlets on 2026-09-24.
 *   Exam frequency    - ICAI notification dated 6 April 2026 (CA Final moves
 *                       from three attempts a year to May and November).
 *   Syllabus / papers - ICAI New Scheme of Education and Training (2024).
 *
 * Anything not sourced is labelled as an estimate in the UI rather than being
 * presented as fact. See ESTIMATE_NOTE.
 */

export type Level = 'FOUNDATION' | 'INTERMEDIATE' | 'FINAL'

/** Which papers an attempt covers. Foundation sits all four at once. */
export type AttemptScope = 'BOTH' | 'GROUP_I' | 'GROUP_II'

export interface PassRate {
  /** Human label for the session, e.g. "May 2026". */
  session: string
  appeared: number
  passed: number
  /** Share of those who appeared, in percent. Published by ICAI. */
  percent: number
}

export interface PaperGroup {
  id: AttemptScope
  label: string
  papers: string[]
}

export interface LevelSpec {
  id: Level
  label: string
  /** How long the whole level is, for context in the UI. */
  summary: string
  groups: PaperGroup[]
  /**
   * Estimated hours to take ONE paper to exam standard: a first pass plus two
   * revisions. This is OUR estimate, not an ICAI figure - see ESTIMATE_NOTE.
   */
  hoursPerPaper: number
  /**
   * Calendar months in which this level holds exams.
   *
   * Foundation and Intermediate run three attempts a year. Final runs TWO, from
   * May 2026: the January and September windows were discontinued by ICAI's
   * 6 April 2026 notification. Storing this per level rather than globally is
   * what stops the planner offering a CA Final student a January attempt that
   * no longer exists.
   */
  sessionMonths: number[]
  /** Most recent published result for this level, all groups attempted together. */
  latestPassRate: PassRate
  /** Recent history, newest first, for the trend display. */
  passRateHistory: PassRate[]
}

export const ESTIMATE_NOTE =
  'Study-hour estimates are ours, not ICAI\u2019s. We show the assumption instead of hiding it.'

export const PASS_RULE = {
  perPaper: 40,
  aggregate: 50,
  /** ICAI excludes candidates absent in any paper from the denominator. */
  note:
    'ICAI counts only candidates who appeared in every paper of the group, so the ' +
    'published rate is a per-attempt rate, not a per-paper one.',
} as const

export const LEVELS: Record<Level, LevelSpec> = {
  FOUNDATION: {
    id: 'FOUNDATION',
    label: 'CA Foundation',
    summary: 'Four papers, sat together. Two subjective, two objective.',
    hoursPerPaper: 130,
    sessionMonths: [1, 5, 9],
    groups: [
      {
        id: 'BOTH',
        label: 'All four papers',
        papers: [
          'Accounting',
          'Business Laws',
          'Quantitative Aptitude',
          'Business Economics',
        ],
      },
    ],
    latestPassRate: { session: 'May 2026', appeared: 90217, passed: 18124, percent: 20.09 },
    passRateHistory: [
      { session: 'May 2026', appeared: 90217, passed: 18124, percent: 20.09 },
      { session: 'January 2026', appeared: 109694, passed: 21099, percent: 19.23 },
      { session: 'September 2025', appeared: 98827, passed: 14609, percent: 14.78 },
    ],
  },

  INTERMEDIATE: {
    id: 'INTERMEDIATE',
    label: 'CA Intermediate',
    summary: 'Six papers across two groups. You may sit either group on its own.',
    // The heaviest level per paper: six papers, and most students are also
    // starting articleship. The estimate reflects that.
    hoursPerPaper: 170,
    sessionMonths: [1, 5, 9],
    groups: [
      {
        id: 'GROUP_I',
        label: 'Group I',
        papers: [
          'Advanced Accounting',
          'Corporate and Other Laws',
          'Taxation',
        ],
      },
      {
        id: 'GROUP_II',
        label: 'Group II',
        papers: [
          'Cost and Management Accounting',
          'Auditing and Ethics',
          'Financial Management and Strategic Management',
        ],
      },
    ],
    latestPassRate: { session: 'May 2026', appeared: 33304, passed: 2820, percent: 8.47 },
    passRateHistory: [
      { session: 'May 2026', appeared: 33304, passed: 2820, percent: 8.47 },
      { session: 'January 2026', appeared: 41798, passed: 3924, percent: 9.39 },
      { session: 'September 2025', appeared: 36398, passed: 3663, percent: 10.06 },
    ],
  },

  FINAL: {
    id: 'FINAL',
    label: 'CA Final',
    summary: 'Six compulsory papers across two groups. May and November only.',
    hoursPerPaper: 240,
    // TWO attempts a year from May 2026. Not three.
    sessionMonths: [5, 11],
    groups: [
      {
        id: 'GROUP_I',
        label: 'Group I',
        papers: [
          'Financial Reporting',
          'Advanced Financial Management',
          'Advanced Auditing, Assurance and Professional Ethics',
        ],
      },
      {
        id: 'GROUP_II',
        label: 'Group II',
        papers: [
          'Direct Tax Laws and International Taxation',
          'Indirect Tax Laws',
          'Integrated Business Solutions',
        ],
      },
    ],
    latestPassRate: { session: 'May 2026', appeared: 23776, passed: 3345, percent: 14.07 },
    passRateHistory: [
      { session: 'May 2026', appeared: 23776, passed: 3345, percent: 14.07 },
      { session: 'January 2026', appeared: 22293, passed: 2446, percent: 10.97 },
    ],
  },
}

/** Levels in the order a student meets them. */
export const LEVEL_ORDER: Level[] = ['FOUNDATION', 'INTERMEDIATE', 'FINAL']

export const MONTH_NAMES = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
] as const

/**
 * Papers in scope for an attempt.
 *
 * Returns an empty array for a scope the level does not have - Foundation has no
 * groups. A caller that gets [] must handle it rather than assuming papers
 * exist, because the alternative is a plan claiming zero hours are needed.
 */
export function papersForScope(level: Level, scope: AttemptScope): string[] {
  const spec = LEVELS[level]
  const group = spec.groups.find((g) => g.id === scope)
  if (group) return group.papers
  if (scope === 'BOTH' && spec.groups.length > 1) {
    return spec.groups.flatMap((g) => g.papers)
  }
  return []
}

/** Scopes a student can actually choose at this level. */
/**
 * The scopes a student can plan for at a level.
 *
 * The tuple return type is load-bearing: every level has at least one way to sit
 * it, so callers may take `[0]` as the default scope without a null check. Typing
 * this as `PaperGroup[]` forced every caller to invent a fallback for a case that
 * cannot occur.
 */
export function scopesForLevel(level: Level): readonly [PaperGroup, ...PaperGroup[]] {
  const spec = LEVELS[level]
  if (spec.groups.length === 1) return spec.groups as [PaperGroup, ...PaperGroup[]]
  return [
    { id: 'BOTH', label: 'Both groups', papers: spec.groups.flatMap((g) => g.papers) },
    ...spec.groups,
  ] satisfies [PaperGroup, ...PaperGroup[]]
}

/**
 * A month number (1-12) as its English name.
 *
 * Throws rather than returning undefined: a month out of range means a typo in
 * the data above, and "undefined 2027" rendered as an exam session is a worse
 * failure than an exception a test will catch.
 */
export function monthName(month: number): string {
  const name = MONTH_NAMES[month - 1]
  if (name === undefined) {
    throw new RangeError(`monthName: ${month} is not a month of the year`)
  }
  return name
}

/**
 * "September 2025" -> "Sep 25", for axis labels on the pass-rate bars.
 *
 * Presentational, but kept here beside the session data so the abbreviation rule
 * lives in one place rather than being reinvented by each caller.
 */
export function shortSession(session: string): string {
  const [month, year] = session.split(' ')
  if (month === undefined || year === undefined) return session
  return `${month.slice(0, 3)} ${year.slice(2)}`
}

/** "May and November" - the human phrasing of a level's session months. */
export function sessionNames(level: Level): string {
  const months = LEVELS[level].sessionMonths.map(monthName)
  const last = months.at(-1)
  if (last === undefined) {
    // Unreachable: every level in LEVELS declares at least one session month.
    throw new RangeError(`${level} declares no exam session months`)
  }
  // Special-cased because the join would otherwise yield " and November".
  if (months.length === 1) return last
  return `${months.slice(0, -1).join(', ')} and ${last}`
}
