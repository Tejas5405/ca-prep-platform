import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { Badge, Button, Card, EmptyState, ErrorState, SectionHeading, Spinner } from '../components/ui'
import { useLoader } from '../lib/useLoader'
import {
  fetchAttempts,
  fetchMocks,
  fetchReport,
  startAttempt,
  submitAttempt,
} from '../lib/queries'
import { QueryError } from '../lib/queries'
import { recordExamMode, recordProctorEvent } from '../lib/campus'

/**
 * Mock papers: the list, the sitting, and the report.
 *
 * THE THREE STATES ARE ONE FEATURE. A paper that can be started but not submitted,
 * or submitted but not reviewed, is not a mock exam. This page therefore handles
 * the whole arc with the URL as the state machine: `/mocks` lists, `/mocks/:id`
 * sits, and the report is fetched by attempt id after submission.
 *
 * THE TIMER IS THE SERVER'S. `expiresAt` comes from the API and is rendered from a
 * countdown computed against the local clock; the server re-checks it at
 * submission and records a late paper as auto-submitted. A student who changes
 * their system clock gains a more confident-looking countdown and nothing else,
 * which is the correct amount of trust to place in a browser.
 */
export default function MocksPage() {
  const { mockId } = useParams<{ mockId: string }>()

  // Keyed by paper id: opening a different paper remounts the sitting, which is
  // how the answer map, the question index and the timer are reset. Doing that
  // with setState calls inside an effect is what the React compiler (rightly)
  // objects to, and a remount is both cleaner and impossible to get half-right.
  if (mockId) return <Sitting key={mockId} mockId={mockId} />

  return <MockList />
}

function MockList() {
  const mocks = useLoader((signal) => fetchMocks(20, signal), [])
  const attempts = useLoader((signal) => fetchAttempts(10, signal), [])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Mock exams</h1>
        <p className="mt-1 text-slate-600">
          Timed papers, the same way ICAI sets them. Chapter tests and full-length papers.
        </p>
      </div>

      {attempts.data && attempts.data.length > 0 && (
        <Card>
          <SectionHeading title="Your recent attempts" />
          <ul className="divide-y divide-slate-100">
            {attempts.data.map((attempt) => (
              <li key={attempt.attemptId} className="flex items-center justify-between gap-3 py-3">
                <span className="min-w-0">
                  <span className="block truncate font-medium text-slate-800">{attempt.title}</span>
                  <span className="mt-0.5 block text-xs text-slate-500">
                    {attempt.status === 'IN_PROGRESS' ? (
                      'In progress'
                    ) : (
                      <>
                        Scored {attempt.score ?? 0}/{attempt.maxScore ?? 0} · {attempt.correctCount}{' '}
                        correct · {attempt.wrongCount} wrong
                        {attempt.autoSubmitted ? ' · auto-submitted at the deadline' : ''}
                      </>
                    )}
                  </span>
                </span>
                {attempt.status === 'IN_PROGRESS' ? (
                  <Link
                    to={`/mocks/${attempt.mockTestId}`}
                    className="shrink-0 text-sm font-medium text-brand-600 hover:underline"
                  >
                    Resume
                  </Link>
                ) : (
                  <Link
                    to={`/reports/${attempt.attemptId}`}
                    className="shrink-0 text-sm font-medium text-brand-600 hover:underline"
                  >
                    Report
                  </Link>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {mocks.error && <ErrorState error={mocks.error} what="the mock papers" />}
      {mocks.loading && <Spinner />}

      {mocks.data && mocks.data.data.length === 0 && (
        <EmptyState
          title="No papers published"
          body="Papers appear here once a reviewer has verified the questions in them. A draft paper with an unchecked answer key is not shown to students on purpose."
        />
      )}

      <div className="grid gap-3 md:grid-cols-2">
        {(mocks.data?.data ?? []).map((mock) => (
          <Card key={mock.id}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-medium text-slate-900">{mock.title}</p>
                <p className="mt-1 text-xs text-slate-500">
                  {mock.durationMin} minutes · {mock.questionCount} questions ·{' '}
                  {mock.totalMarks} marks
                </p>
              </div>
              <Badge tone={mock.kind === 'FULL_LENGTH' ? 'amber' : 'slate'}>
                {mock.kind.replace('_', ' ').toLowerCase()}
              </Badge>
            </div>

            <div className="mt-4">
              {mock.locked ? (
                <p className="text-sm text-slate-500">
                  Included in a paid plan.{' '}
                  <Link to="/subscription" className="font-medium text-brand-600 hover:underline">
                    See plans
                  </Link>
                </p>
              ) : (
                <>
                  <Link
                    to={`/mocks/${mock.id}`}
                    className="inline-block rounded-md bg-brand-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-brand-700"
                  >
                    Start paper
                  </Link>
                  <Link
                    to={`/mocks/${mock.id}?exam=1`}
                    className="ml-3 inline-block text-sm font-medium text-brand-700 underline"
                  >
                    Exam mode
                  </Link>
                </>
              )}
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}

/**
 * Seconds until the deadline, recomputed on a one-second tick.
 *
 * `onExpire` fires from the INTERVAL, not from an effect body. An effect that
 * called the submission directly would set state synchronously during the commit,
 * which the React compiler flags as a cascading render - and, more to the point,
 * the timer is the thing that knows the deadline passed, so the timer is what
 * should say so.
 */
function useCountdown(expiresAt: string | undefined, onExpire: () => void) {
  const target = useMemo(() => (expiresAt ? new Date(expiresAt).getTime() : null), [expiresAt])
  const [now, setNow] = useState(() => Date.now())
  const fired = useRef(false)
  const expire = useRef(onExpire)
  useEffect(() => {
    expire.current = onExpire
  })

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now())
      if (target !== null && Date.now() >= target && !fired.current) {
        fired.current = true
        expire.current()
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [target])

  if (target === null) return { remaining: 0, label: '--:--:--', expired: false }
  const remaining = Math.max(0, Math.floor((target - now) / 1000))
  const hours = String(Math.floor(remaining / 3600)).padStart(2, '0')
  const minutes = String(Math.floor((remaining % 3600) / 60)).padStart(2, '0')
  const seconds = String(remaining % 60).padStart(2, '0')
  return { remaining, label: `${hours}:${minutes}:${seconds}`, expired: remaining === 0 }
}

function Sitting({ mockId }: { mockId: string }) {
  const [search] = useSearchParams()
  const examMode = search.get('exam') === '1'
  const navigate = useNavigate()
  // Opening the paper is a POST (it starts or resumes a server-side attempt), so it
  // runs through the loader's effect rather than during render.
  const { data: attempt, error: loadError, loading } = useLoader(
    (signal) => startAttempt(mockId, signal),
    [mockId],
  )
  const [submitError, setSubmitError] = useState<QueryError | null>(null)
  const [busy, setBusy] = useState(false)
  const [answers, setAnswers] = useState<Record<string, number>>({})
  const [index, setIndex] = useState(0)
  const countdown = useCountdown(attempt?.expiresAt, () => {
    // The timer expiring submits the paper rather than trusting the student to
    // notice. This is the one place a client clock acts - and it can only submit
    // EARLIER than the server would, never later, because the server judges the
    // stored deadline itself.
    void submitRef.current(true)
  })
  useEffect(() => {
    if (!examMode || !attempt?.attemptId) return
    const attemptId = attempt.attemptId
    void recordExamMode(attemptId).catch(() => undefined)
    function onHide() {
      if (document.visibilityState === 'hidden') {
        void recordProctorEvent(attemptId, 'TAB_HIDDEN').catch(() => undefined)
      }
    }
    document.addEventListener('visibilitychange', onHide)
    return () => document.removeEventListener('visibilitychange', onHide)
  }, [examMode, attempt?.attemptId])
  const error = submitError ?? loadError

  const questions = attempt?.questions ?? []
  const question = questions[index] ?? null
  const answered = Object.keys(answers).length

  // The countdown calls the CURRENT submit, because the interval is created once
  // and would otherwise hold the first render's closure - which sees an empty
  // answer map and would submit a blank paper.
  const submitRef = useRef<(auto: boolean) => Promise<void>>(async () => {})
  submitRef.current = submit

  async function submit(auto: boolean) {
    if (!attempt || busy) return
    setBusy(true)
    setSubmitError(null)
    try {
      await submitAttempt(attempt.attemptId, {
        startedAt: attempt.startedAt,
        // The server records its own lateness from the stored deadline; this value
        // is only the paper's nominal length, used for its own bookkeeping.
        durationMin: attempt.durationMin,
        answers: questions.map((item) => ({
          question_id: item.id,
          chosen_option: answers[item.id] ?? null,
        })),
      })
      navigate(`/reports/${attempt.attemptId}${auto ? '?auto=1' : ''}`)
    } catch (err: unknown) {
      setSubmitError(err as QueryError)
      setBusy(false)
    }
  }

  if (error) {
    return (
      <div className="space-y-4">
        <ErrorState error={error} what="this paper" />
        <Link to="/mocks" className="text-sm font-medium text-brand-600 hover:underline">
          Back to mock exams
        </Link>
      </div>
    )
  }

  if (loading && !attempt) return <Spinner label="Opening the paper…" />
  if (!attempt) return null

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">{attempt.title}</h1>
          {examMode && (
            <p className="mt-1 text-xs text-slate-500">
              Exam mode. Answers stay hidden until submit. Leaving this tab is recorded. No camera is used, and the browser is not locked.
            </p>
          )}
          <p className="mt-0.5 text-sm text-slate-600">
            {questions.length} questions · {attempt.totalMarks} marks
            {attempt.resumed ? ' · resumed, your timer did not restart' : ''}
          </p>
        </div>
        <div className="text-right">
          <p
            className={`font-mono text-2xl font-semibold ${
              countdown.remaining < 300 ? 'text-wrong-500' : 'text-slate-900'
            }`}
            aria-label="Time remaining"
          >
            {countdown.label}
          </p>
          <p className="text-xs text-slate-500">{answered} answered</p>
        </div>
      </div>

      <div className="flex flex-wrap gap-1">
        {questions.map((item, position) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setIndex(position)}
            title={`Question ${position + 1}`}
            className={`h-8 w-8 rounded-md border text-xs font-medium ${
              position === index
                ? 'border-brand-600 bg-brand-600 text-white'
                : answers[item.id] !== undefined
                  ? 'border-right-500 bg-right-50 text-right-600'
                  : 'border-slate-300 text-slate-600 hover:bg-slate-50'
            }`}
          >
            {position + 1}
          </button>
        ))}
      </div>

      {question && (
        <Card>
          <div className="mb-3 flex items-center justify-between text-sm text-slate-500">
            <span>
              Question {index + 1} of {questions.length}
            </span>
            <span>
              {question.marks} marks
              {question.negativeMarks > 0 ? ` · -${question.negativeMarks} for a wrong answer` : ''}
            </span>
          </div>

          <p className="text-base leading-relaxed text-slate-900">{question.text}</p>

          {question.questionType !== 'MCQ' && (
            <p className="mt-3 rounded-md bg-amber-100 px-3 py-2 text-sm text-amber-800">
              This is a {question.questionType.toLowerCase()} question. It is marked by a
              person, so it will show as pending review in your report rather than scored
              automatically.
            </p>
          )}

          <ul className="mt-4 space-y-2">
            {question.options.map((option, position) => {
              const selected = answers[question.id] === position
              return (
                <li key={option.label}>
                  <button
                    type="button"
                    onClick={() =>
                      setAnswers((current) => {
                        const next = { ...current }
                        if (selected) delete next[question.id]
                        else next[question.id] = position
                        return next
                      })
                    }
                    aria-pressed={selected}
                    className={`flex w-full items-start gap-3 rounded-md border px-3 py-2.5 text-left ${
                      selected
                        ? 'border-brand-600 bg-brand-50'
                        : 'border-slate-200 hover:border-brand-500'
                    }`}
                  >
                    <span className="mt-0.5 font-semibold text-slate-500">{option.label}</span>
                    <span className="text-slate-800">{option.text}</span>
                  </button>
                </li>
              )
            })}
          </ul>

          <div className="mt-5 flex items-center justify-between">
            <Button
              tone="secondary"
              disabled={index === 0}
              onClick={() => setIndex((value) => Math.max(0, value - 1))}
            >
              Previous
            </Button>
            {index < questions.length - 1 ? (
              <Button onClick={() => setIndex((value) => value + 1)}>Next</Button>
            ) : (
              <Button disabled={busy} onClick={() => void submit(false)}>
                {busy ? 'Submitting…' : 'Submit paper'}
              </Button>
            )}
          </div>
        </Card>
      )}

      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">
          The answer key is not in this page. It arrives when you submit, so a peek at the network
          tab is not a way to pass a timed paper.
        </p>
        <Button tone="secondary" disabled={busy} onClick={() => void submit(false)}>
          {busy ? 'Submitting…' : 'Submit paper'}
        </Button>
      </div>
    </div>
  )
}

export function ReportPage() {
  const { attemptId } = useParams<{ attemptId: string }>()
  const [showAll, setShowAll] = useState(false)

  // `useLoader` rather than a hand-rolled effect: an abort raised by the cleanup
  // (which StrictMode's double mount does on every visit) must not be rendered as a
  // failed request, and telling the two apart is what this hook exists for.
  const state = useLoader(
    (signal) => fetchReport(attemptId as string, signal),
    [attemptId],
    Boolean(attemptId),
  )
  const report = state.data
  const error = state.error

  if (state.loading) return <Spinner label="Loading your report…" />
  if (error) return <ErrorState error={error} what="your report" />
  if (!report) return null

  const total = report.questions.length || 1
  const scorePercent = report.maxScore ? Math.round(((report.score ?? 0) / report.maxScore) * 100) : 0
  const visible = showAll ? report.questions : report.questions.slice(0, 10)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{report.title}</h1>
        <p className="mt-1 text-slate-600">
          {report.autoSubmitted
            ? 'Submitted automatically when the timer ran out.'
            : 'Submitted by you.'}{' '}
          {report.timeTakenSeconds
            ? `You took ${Math.round(report.timeTakenSeconds / 60)} minutes.`
            : ''}
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-4">
        <Summary label="Score" value={`${report.score ?? 0}/${report.maxScore ?? 0}`} accent />
        <Summary label="Percentage" value={`${scorePercent}%`} />
        <Summary label="Correct" value={report.correct ?? 0} />
        <Summary label="Wrong" value={report.wrong ?? 0} />
      </div>

      {(report.unattempted ?? 0) > 0 && (
        <p className="text-sm text-slate-500">
          {report.unattempted} question{report.unattempted === 1 ? '' : 's'} left blank. Unanswered
          carries no penalty, which matters when the alternative is a guess between four options.
        </p>
      )}

      {(report.pendingReview ?? 0) > 0 && (
        <p className="rounded-md bg-amber-100 px-3 py-2 text-sm text-amber-800">
          {report.pendingReview} descriptive answer
          {report.pendingReview === 1 ? '' : 's'} awaiting a human. The score above is provisional.
        </p>
      )}

      <SectionHeading
        title="Question by question"
        subtitle={`${total} questions · showing ${visible.length}`}
        action={
          report.questions.length > 10 ? (
            <Button tone="quiet" onClick={() => setShowAll((value) => !value)}>
              {showAll ? 'Show fewer' : `Show all ${report.questions.length}`}
            </Button>
          ) : undefined
        }
      />

      <ol className="space-y-4">
        {visible.map((item, position) => (
          <li key={item.questionId}>
            <Card>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="text-sm text-slate-500">Q{position + 1}</span>
                <Badge
                  tone={
                    item.outcome === 'CORRECT'
                      ? 'right'
                      : item.outcome === 'WRONG'
                        ? 'wrong'
                        : item.outcome === 'PENDING_REVIEW'
                          ? 'amber'
                          : 'slate'
                  }
                >
                  {item.outcome.replace('_', ' ').toLowerCase()}
                </Badge>
                <Badge tone="slate">{item.marks} marks</Badge>
              </div>

              <p className="text-slate-900">{item.text}</p>

              <ul className="mt-3 space-y-1.5">
                {item.options.map((option, position2) => {
                  const isChosen = item.chosenOption === position2
                  const isCorrect = option.isCorrect
                  return (
                    <li
                      key={option.label}
                      className={`rounded-md px-3 py-1.5 text-sm ${
                        isCorrect
                          ? 'bg-right-50 text-right-600'
                          : isChosen
                            ? 'bg-wrong-50 text-wrong-500'
                            : 'text-slate-700'
                      }`}
                    >
                      <span className="font-semibold">{option.label}.</span> {option.text}
                      {isCorrect && <span className="ml-2 text-xs">correct answer</span>}
                      {isChosen && !isCorrect && <span className="ml-2 text-xs">you chose this</span>}
                      {isChosen && isCorrect && <span className="ml-2 text-xs">you chose this</span>}
                    </li>
                  )
                })}
              </ul>

              {item.explanation && (
                <p className="mt-3 border-l-2 border-brand-500 pl-3 text-sm text-slate-700">
                  {item.explanation}
                </p>
              )}
            </Card>
          </li>
        ))}
      </ol>

      {report.questions.length === 0 && (
        <EmptyState
          title="Nothing to review"
          body="This paper had no questions when it was submitted, which normally means a question was unpublished while you were sitting it."
        />
      )}
    </div>
  )
}

function Summary({
  label,
  value,
  accent = false,
}: {
  label: string
  value: string | number
  accent?: boolean
}) {
  return (
    <Card>
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p
        className={`mt-1 text-2xl font-semibold ${
          accent ? 'text-brand-600' : 'text-slate-900'
        }`}
      >
        {value}
      </p>
    </Card>
  )
}
