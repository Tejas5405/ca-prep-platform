/**
 * The product previews on the public site.
 *
 * WHY THESE ARE COMPONENTS AND NOT SCREENSHOTS
 *
 * A screenshot of a product that does not exist yet is the single most common
 * dishonesty in software marketing: it is a picture of a design, presented as a
 * picture of a product. These are built from the same design tokens, the same
 * type scale and the same interaction vocabulary as the application itself, so
 * when the app is opened the two visibly agree - and when a token changes, the
 * marketing page changes with it instead of quietly going stale.
 *
 * WHAT THEY SHOW, AND THE RULE THEY FOLLOW
 *
 *   * The DATA in them is illustrative, and every one of them says so in plain
 *     words, once, in a place a reader will actually see. The figures are not
 *     presented as platform statistics - there are no platform statistics, because
 *     there are no users yet.
 *   * The ICAI figures used elsewhere on the page are real, sourced and dated. The
 *     distinction is the whole point: verifiable third-party facts are proof,
 *     invented product metrics are decoration that looks like proof.
 *   * Nothing is interactive. A preview that looks clickable and is not is a
 *     broken promise; these are clearly static, and the CTAs around them are real.
 */

import type { ReactNode } from 'react'

import { Badge } from '../ui'

/** The one honest label every preview carries. */
export function Illustrative({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <figure className={className}>
      {children}
      <figcaption className="mt-3 text-center text-xs text-slate-500">
        Illustrative interface — sample data, shown to explain how the product works.
      </figcaption>
    </figure>
  )
}

/** A browser-ish frame, so a preview reads as an application rather than a graphic. */
function Frame({ children, title }: { children: ReactNode; title: string }) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center gap-2 border-b border-slate-200 bg-slate-50 px-4 py-2.5">
        <span aria-hidden="true" className="flex gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-slate-300" />
          <span className="h-2.5 w-2.5 rounded-full bg-slate-300" />
          <span className="h-2.5 w-2.5 rounded-full bg-slate-300" />
        </span>
        <span className="ml-1 truncate text-xs text-slate-500">{title}</span>
      </div>
      <div className="p-4 sm:p-5">{children}</div>
    </div>
  )
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold tracking-tight tabular-nums text-slate-900">
        {value}
      </p>
      {note && <p className="mt-0.5 text-[11px] text-slate-400">{note}</p>}
    </div>
  )
}

/**
 * The hero preview: the dashboard, which is also the screen a new student lands on.
 *
 * It answers "what will I see when I sign up" without a screenshot. The weak
 * chapters are the point of it - a dashboard that only shows a score tells a
 * student how they are doing, and one that names the chapters leaking marks tells
 * them what to do next.
 */
export function DashboardPreview() {
  return (
    <Frame title="caprep.in/dashboard">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-slate-900">CA Final · Group II · May 2027</p>
          <p className="text-xs text-slate-500">Target: 246 days of preparation left</p>
        </div>
        <Badge tone="brand">Streak: 6 days</Badge>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Attempted" value="1,284" note="of 3,910 in the bank" />
        <Stat label="Accuracy" value="62%" note="objective questions" />
        <Stat label="Due today" value="18" note="revision queue" />
        <Stat label="Hours logged" value="112" note="last 30 days" />
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <div className="rounded-lg border border-slate-200 p-3">
          <p className="text-xs font-medium text-slate-700">Where marks are leaking</p>
          <ul className="mt-2 space-y-2">
            {[
              { name: 'Cost Management · Standard Costing', value: 38 },
              { name: 'Strategic Financial Mgmt · Derivatives', value: 44 },
              { name: 'Corporate & Economic Laws · LLP', value: 51 },
            ].map((row) => (
              <li key={row.name} className="text-xs">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-slate-600">{row.name}</span>
                  <span className="tabular-nums text-slate-900">{row.value}%</span>
                </div>
                <div className="mt-1 h-1.5 w-full rounded-full bg-slate-100" aria-hidden="true">
                  <div className="h-1.5 rounded-full bg-brand-600" style={{ width: `${row.value}%` }} />
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-lg border border-slate-200 p-3">
          <p className="text-xs font-medium text-slate-700">Today&rsquo;s plan</p>
          <ul className="mt-2 space-y-1.5 text-xs">
            {[
              { task: 'Standard Costing — 20 questions', done: true },
              { task: 'Revision: 18 due cards', done: false },
              { task: 'Mock paper 3 — timed, 100 marks', done: false },
            ].map((item) => (
              <li key={item.task} className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                    item.done ? 'border-brand-600 bg-brand-600 text-white' : 'border-slate-300'
                  }`}
                >
                  {item.done && (
                    <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                      <path
                        d="M1.5 5.5l2 2 5-5"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  )}
                </span>
                <span className={item.done ? 'text-slate-400 line-through' : 'text-slate-700'}>
                  {item.task}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Frame>
  )
}

/**
 * The question-bank showcase.
 *
 * The filters are the actual filter set the API accepts. The historical question
 * is shown carrying its badge and its Finance Act year rather than being hidden -
 * a preview that omits the awkward content tells the visitor nothing about how the
 * awkward content is handled, and that handling is the reason to trust this bank
 * over a photocopied one.
 */
export function QuestionBankShowcase() {
  return (
    <Illustrative>
      <Frame title="caprep.in/questions?paper=SFM&chapter=derivatives">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex min-h-11 flex-1 items-center gap-2 rounded-md border border-slate-300 px-3">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <circle cx="7" cy="7" r="4.2" stroke="currentColor" strokeWidth="1.5" />
              <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            <span className="text-sm text-slate-400">Search the published bank…</span>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {['CA Final', 'Paper 2 — SFM', 'Chapter 4', 'May 2025', '8 marks', 'MCQ'].map((filter) => (
            <span
              key={filter}
              className="rounded-full border border-slate-300 px-2.5 py-1 text-xs text-slate-600"
            >
              {filter}
            </span>
          ))}
        </div>

        <ul className="mt-4 space-y-3">
          <li className="rounded-lg border border-slate-200 p-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone="brand">MCQ</Badge>
              <Badge tone="slate">8 marks</Badge>
              <Badge tone="slate">May 2025</Badge>
            </div>
            <p className="mt-2 text-sm text-slate-800">
              A company holds a portfolio of equity shares. Given the beta of the portfolio and the
              risk-free rate, the expected return under CAPM is —
            </p>
            <div className="mt-2 grid gap-1 sm:grid-cols-2">
              {['(A) 12.4%', '(B) 13.8%', '(C) 14.1%', '(D) 15.2%'].map((option) => (
                <span key={option} className="rounded border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600">
                  {option}
                </span>
              ))}
            </div>
          </li>

          <li className="rounded-lg border border-amber-300 bg-amber-50/60 p-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone="amber">Historical law</Badge>
              <Badge tone="slate">AY 2019–20 · Finance Act 2018</Badge>
            </div>
            <p className="mt-2 text-sm text-slate-800">
              Compute the deduction available under section 80C for the assessment year 2019–20,
              given the following particulars.
            </p>
            <p className="mt-2 text-xs text-amber-900">
              Answered under the law applicable to AY 2019–20. The limits shown here have since
              changed — check the current Finance Act before applying them to a present-day problem.
            </p>
          </li>
        </ul>
      </Frame>
    </Illustrative>
  )
}

/**
 * The mock-exam showcase.
 *
 * Everything shown is a property the server owns: the clock, whether an answer is
 * saved, which questions are marked for review, and the submit. The palette is the
 * part students recognise instantly from the real exam hall, and it is worth
 * showing because a paper you cannot navigate is a paper that teaches the wrong
 * habit.
 */
export function MockShowcase() {
  const palette = [
    'answered', 'answered', 'marked', 'unanswered', 'answered',
    'unanswered', 'current', 'unanswered', 'answered', 'unanswered',
  ] as const

  return (
    <Illustrative>
      <Frame title="caprep.in/mocks/mock-14 — attempt in progress">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 pb-3">
          <div>
            <p className="text-sm font-semibold text-slate-900">Mock Paper 3 — Strategic Financial Management</p>
            <p className="text-xs text-slate-500">Question 7 of 10 · 8 marks · 2 questions marked for review</p>
          </div>
          <div className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-semibold tabular-nums text-slate-900">
            01:47:12 <span className="text-xs font-normal text-slate-500">remaining</span>
          </div>
        </div>

        <div className="mt-3 grid gap-4 lg:grid-cols-[1fr_auto]">
          <div>
            <p className="text-sm leading-relaxed text-slate-800">
              An investor is evaluating two mutually exclusive projects with unequal lives. Using the
              equivalent annual annuity method, which project should be selected, and why?
            </p>
            <div className="mt-3 space-y-1.5">
              {[
                'Project A — higher NPV over its life',
                'Project B — higher equivalent annual annuity',
                'Project A — shorter payback period',
                'Neither — the methods disagree',
              ].map((option, index) => (
                <div
                  key={option}
                  className={`flex items-center gap-2.5 rounded-md border px-3 py-2 text-sm ${
                    index === 1 ? 'border-brand-600 bg-brand-50' : 'border-slate-200'
                  }`}
                >
                  <span
                    className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] font-medium ${
                      index === 1 ? 'border-brand-600 bg-brand-600 text-white' : 'border-slate-300 text-slate-500'
                    }`}
                  >
                    {String.fromCharCode(65 + index)}
                  </span>
                  <span className="text-slate-700">{option}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="lg:w-44">
            <p className="text-xs font-medium text-slate-700">Question palette</p>
            <ul className="mt-2 grid grid-cols-5 gap-1.5 lg:grid-cols-5">
              {palette.map((state, index) => (
                <li
                  key={index}
                  className={`flex h-7 items-center justify-center rounded text-[11px] font-medium tabular-nums ${
                    state === 'answered'
                      ? 'bg-right-50 text-right-600 ring-1 ring-right-500/30'
                      : state === 'marked'
                        ? 'bg-amber-100 text-amber-800 ring-1 ring-amber-300'
                        : state === 'current'
                          ? 'bg-brand-600 text-white'
                          : 'bg-slate-100 text-slate-500'
                  }`}
                >
                  {index + 1}
                </li>
              ))}
            </ul>

            <ul className="mt-3 space-y-1 text-[11px] text-slate-500">
              <li className="flex items-center gap-1.5">
                <span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm bg-right-50 ring-1 ring-right-500/30" />
                Answered
              </li>
              <li className="flex items-center gap-1.5">
                <span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm bg-amber-100 ring-1 ring-amber-300" />
                Marked for review
              </li>
              <li className="flex items-center gap-1.5">
                <span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm bg-slate-100" />
                Not answered
              </li>
            </ul>

            <div className="mt-3 rounded-md bg-slate-900 px-3 py-2 text-center text-xs font-medium text-white">
              Submit paper
            </div>
            <p className="mt-1.5 text-[11px] text-slate-400">
              The attempt is saved on the server, so a refresh resumes it.
            </p>
          </div>
        </div>
      </Frame>
    </Illustrative>
  )
}

/**
 * The progress showcase.
 *
 * Bars rather than a chart library. Three reasons, in order of importance: the
 * shapes are simple enough that a library adds bytes and a dependency without
 * adding meaning; the bars are real DOM, so they are readable by a screen reader
 * and print; and CSS bars are as responsive as the layout they are in, whereas an
 * SVG chart with fixed viewBox coordinates usually is not.
 */
export function ProgressShowcase() {
  const subjects = [
    { name: 'Financial Reporting', value: 71 },
    { name: 'Strategic Financial Management', value: 58 },
    { name: 'Advanced Auditing', value: 64 },
    { name: 'Corporate & Economic Laws', value: 49 },
    { name: 'Cost & Management Accounting', value: 44 },
  ]

  const trend = [42, 47, 45, 53, 56, 58, 62]

  return (
    <Illustrative>
      <Frame title="caprep.in/progress">
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <p className="text-sm font-medium text-slate-900">Accuracy by paper</p>
            <ul className="mt-3 space-y-2.5">
              {subjects.map((subject) => (
                <li key={subject.name}>
                  <div className="flex items-baseline justify-between gap-3 text-xs">
                    <span className="truncate text-slate-600">{subject.name}</span>
                    <span className="tabular-nums text-slate-900">{subject.value}%</span>
                  </div>
                  <div className="mt-1 h-2 w-full rounded-full bg-slate-100" aria-hidden="true">
                    <div
                      className={`h-2 rounded-full ${subject.value < 50 ? 'bg-wrong-500' : 'bg-brand-600'}`}
                      style={{ width: `${subject.value}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <p className="text-sm font-medium text-slate-900">Accuracy over the last 7 weeks</p>
            <ul className="mt-3 flex h-36 items-end gap-2">
              {trend.map((value, index) => (
                <li key={index} className="flex flex-1 flex-col items-center gap-1">
                  <span className="text-[10px] tabular-nums text-slate-500">{value}%</span>
                  <span
                    className="w-full rounded-t bg-brand-600/85"
                    style={{ height: `${(value / 70) * 100}%` }}
                    aria-hidden="true"
                  />
                  <span className="text-[10px] text-slate-400">W{index + 1}</span>
                </li>
              ))}
            </ul>

            <div className="mt-4 grid grid-cols-2 gap-3">
              <Stat label="Mocks taken" value="4" note="best 61%" />
              <Stat label="Median time / question" value="1m 48s" note="objective" />
            </div>
          </div>
        </div>
      </Frame>
    </Illustrative>
  )
}

/**
 * The planner showcase.
 *
 * The interesting column is the last one. A plan that silently drops chapters is
 * worse than one that admits it cannot fit them, because the student discovers the
 * gap in the exam hall - so the preview shows the admission, not just the
 * timetable.
 */
export function PlannerShowcase() {
  return (
    <Illustrative>
      <Frame title="caprep.in/planner">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-sm font-semibold text-slate-900">Study plan — May 2027 attempt</p>
            <p className="text-xs text-slate-500">Exam on 2 May 2027 · 2.5 hours on weekdays, 5 on Sundays</p>
          </div>
          <span className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700">
            Re-plan around what I completed
          </span>
        </div>

        <ul className="mt-4 space-y-2">
          {[
            { day: 'Today', task: 'Standard Costing — variances', minutes: 75, state: 'in progress' },
            { day: 'Today', task: 'Revision queue — 18 due cards', minutes: 45, state: 'due' },
            { day: 'Tomorrow', task: 'Derivatives — hedging with futures', minutes: 90, state: 'planned' },
            { day: 'Friday', task: 'Mock Paper 3 — under exam conditions', minutes: 180, state: 'planned' },
          ].map((row) => (
            <li
              key={`${row.day}-${row.task}`}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2.5"
            >
              <div className="min-w-0">
                <p className="text-xs text-slate-400">{row.day}</p>
                <p className="truncate text-sm text-slate-800">{row.task}</p>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="tabular-nums text-slate-500">{row.minutes} min</span>
                <Badge tone={row.state === 'in progress' ? 'brand' : 'slate'}>{row.state}</Badge>
              </div>
            </li>
          ))}
        </ul>

        <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900">
          At your current hours, 6 chapters do not fit before the exam. Re-planning can redistribute
          them — the plan tells you which ones rather than dropping them silently.
        </p>
      </Frame>
    </Illustrative>
  )
}
