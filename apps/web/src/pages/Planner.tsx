import { useState, type FormEvent } from 'react'

import { ApiError, api, type ApiSuccess } from '../lib/api'

/**
 * Study planner.
 *
 * The important UI decision here is the coverage warning.
 *
 * The backend refuses to schedule more study time than the calendar holds, and
 * reports what it could not fit rather than silently producing an
 * unexecutable plan. That honesty is only worth anything if it reaches the
 * student, so the warning is rendered as a prominent, specific panel naming the
 * chapters that were dropped — not a dismissible toast, and never swallowed into
 * a generic "plan created" success message.
 */

interface PlanTask {
  date: string
  subjectId: string | null
  chapterId: string | null
  taskType: string
  plannedMin: number
}

interface PlanDay {
  date: string
  isBuffer: boolean
  isRevision: boolean
  plannedMin: number
  tasks: PlanTask[]
}

interface CoverageWarning {
  coverableFraction: number
  droppedChapterIds: string[]
  message: string
}

interface PlanResponse {
  daysRemaining: number
  coverageDays: number
  bufferDays: number
  scheduledMocks: number
  totalPlannedMinutes: number
  coverageWarning: CoverageWarning | null
  days: PlanDay[]
}

const TASK_LABEL: Record<string, string> = {
  STUDY: 'Study',
  REVISE: 'Revise',
  PRACTICE: 'Practice',
  MOCK_TEST: 'Mock test',
  BUFFER: 'Buffer',
}

function formatMinutes(total: number): string {
  const h = Math.floor(total / 60)
  const m = total % 60
  return m === 0 ? `${h}h` : `${h}h ${m}m`
}

function formatDate(iso: string): string {
  // Parse as UTC noon to avoid an off-by-one day when the browser is behind UTC.
  const date = new Date(`${iso}T12:00:00Z`)
  return date.toLocaleDateString('en-IN', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  })
}

export default function PlannerPage() {
  const [examDate, setExamDate] = useState('')
  const [dailyHours, setDailyHours] = useState(4)
  const [subjectInput, setSubjectInput] = useState('')
  const [plan, setPlan] = useState<PlanResponse | null>(null)
  const [error, setError] = useState<ApiError | string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleGenerate(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const response = await api.post<ApiSuccess<PlanResponse>>('/planner/generate', {
        exam_date: examDate,
        daily_hours: dailyHours,
        level: 'INTERMEDIATE',
        subjects: [
          {
            subject_id: subjectInput.trim() || 'GENERAL',
            syllabus_weight: 100,
            accuracy: null,
            chapters: [{ chapter_id: 'c1', weight: 1 }],
          },
        ],
      })
      setPlan(response.data)
    } catch (err) {
      setError(err instanceof ApiError ? err : 'Could not generate a plan.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
          Study planner
        </h1>
        <p className="mt-1 text-slate-600">
          Build a day-by-day plan from your exam date and available hours.
        </p>
      </div>

      <form
        onSubmit={handleGenerate}
        className="grid gap-4 rounded-lg border border-slate-200 bg-white p-5 sm:grid-cols-3"
      >
        <div>
          <label htmlFor="examDate" className="block text-sm font-medium text-slate-700">
            Exam date
          </label>
          <input
            id="examDate"
            type="date"
            required
            value={examDate}
            onChange={(e) => setExamDate(e.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
          />
        </div>

        <div>
          <label htmlFor="dailyHours" className="block text-sm font-medium text-slate-700">
            Study hours per day
          </label>
          <input
            id="dailyHours"
            type="number"
            required
            min={0.5}
            max={12}
            step={0.5}
            value={dailyHours}
            onChange={(e) => setDailyHours(Number(e.target.value))}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
          />
        </div>

        <div>
          <label htmlFor="subjects" className="block text-sm font-medium text-slate-700">
            Subject
          </label>
          <input
            id="subjects"
            type="text"
            placeholder="e.g. Advanced Accounting"
            value={subjectInput}
            onChange={(e) => setSubjectInput(e.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
          />
        </div>

        <div className="sm:col-span-3">
          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-brand-600 px-4 py-2 font-medium text-white transition-colors hover:bg-brand-700 disabled:opacity-60"
          >
            {busy ? 'Generating…' : 'Generate plan'}
          </button>
        </div>
      </form>

      {error && (
        <div role="alert" className="rounded-lg border border-wrong-500/30 bg-wrong-50 p-4">
          <p className="font-medium text-wrong-500">Could not generate a plan</p>
          {typeof error === 'string' ? (
            <p className="mt-1 text-sm text-slate-700">{error}</p>
          ) : (
            <>
              <p className="mt-1 text-sm text-slate-700">{error.message}</p>
              {Object.entries(error.fieldErrors).map(([field, message]) => (
                <p key={field} className="mt-1 text-sm text-slate-700">
                  <span className="font-medium">{field}</span>: {message}
                </p>
              ))}
              {error.requestId && (
                <p className="mt-2 font-mono text-xs text-slate-500">
                  Request id: {error.requestId}
                </p>
              )}
            </>
          )}
        </div>
      )}

      {plan?.coverageWarning && (
        <div
          role="alert"
          className="rounded-lg border border-amber-500/40 bg-amber-50 p-5"
        >
          <h2 className="font-medium text-amber-900">
            This plan cannot cover the whole syllabus
          </h2>
          <p className="mt-1 text-sm text-amber-900">{plan.coverageWarning.message}</p>
          <p className="mt-2 text-sm text-amber-900">
            Covering about{' '}
            <strong>{Math.round(plan.coverageWarning.coverableFraction * 100)}%</strong> of
            the syllabus in the time available.
          </p>
          {plan.coverageWarning.droppedChapterIds.length > 0 && (
            <details className="mt-3">
              <summary className="cursor-pointer text-sm font-medium text-amber-900">
                {plan.coverageWarning.droppedChapterIds.length} chapter
                {plan.coverageWarning.droppedChapterIds.length === 1 ? '' : 's'} left out
              </summary>
              <ul className="mt-2 list-inside list-disc text-sm text-amber-900">
                {plan.coverageWarning.droppedChapterIds.map((id) => (
                  <li key={id}>{id}</li>
                ))}
              </ul>
            </details>
          )}
          <p className="mt-3 text-sm text-amber-900">
            Options: increase your daily hours, move the exam date, or reduce the
            subject list. We will not produce a schedule that pretends to fit.
          </p>
        </div>
      )}

      {plan && !plan.coverageWarning && (
        <div className="rounded-lg border border-correct-500/30 bg-correct-50 p-4">
          <p className="text-sm font-medium text-correct-500">
            The syllabus fits: {plan.coverageDays} study days, {plan.bufferDays} buffer
            days and {plan.scheduledMocks} mock tests.
          </p>
        </div>
      )}

      {plan && plan.days.length > 0 && (
        <section>
          <h2 className="mb-3 text-lg font-medium text-slate-900">Your schedule</h2>
          <ol className="space-y-2">
            {plan.days.map((day) => (
              <li
                key={day.date}
                className={`rounded-lg border p-4 ${
                  day.isBuffer
                    ? 'border-slate-200 bg-slate-100'
                    : 'border-slate-200 bg-white'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium text-slate-900">{formatDate(day.date)}</span>
                  <span className="text-sm text-slate-500">
                    {formatMinutes(day.plannedMin)}
                  </span>
                </div>
                <ul className="mt-2 flex flex-wrap gap-2">
                  {day.tasks.map((task, index) => (
                    <li
                      key={`${task.chapterId ?? 'x'}-${task.taskType}-${index}`}
                      className="rounded-md bg-slate-100 px-2 py-1 text-xs text-slate-700"
                    >
                      {TASK_LABEL[task.taskType] ?? task.taskType}
                      {task.chapterId ? ` · ${task.chapterId}` : ''} ·{' '}
                      {formatMinutes(task.plannedMin)}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  )
}
