import { Link } from 'react-router-dom'

import { Badge, Card, EmptyState, ErrorState, Spinner } from '../components/ui'
import { fetchLdr } from '../lib/queries'
import { useLoader } from '../lib/useLoader'

/**
 * Marked for later.
 *
 * THIS SCREEN HAS NO WRITE PATH, AND THAT IS THE DESIGN. The flag it shows lives on
 * `user_question_progress.is_marked_for_review`, written by the practice loop at the
 * moment a question is met and not yet mastered. A second way to write it - a toggle
 * on this page - would be a second answer to "is this flagged?", and the two would
 * eventually disagree: the row the practice loop reads is the row that decides
 * whether a question appears in the daily queue.
 *
 * So removing a question from this list happens where it was added: answer it, and
 * clear the flag there. The empty state says so rather than offering a button that
 * does not exist.
 */
export default function LdrPage() {
  const list = useLoader((signal) => fetchLdr(signal), [])
  const questions = list.data?.data ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Marked for later</h1>
        <p className="mt-1 max-w-2xl text-slate-600">
          Questions you flagged while practising, newest first. Flagging happens where you answer,
          so this list is a record rather than a second place to manage it.
        </p>
      </div>

      {list.error && <ErrorState error={list.error} what="your marked questions" />}
      {list.loading && <Spinner label="Loading your marked questions…" />}

      {list.data && questions.length === 0 && (
        <EmptyState
          title="Nothing marked yet"
          body="While practising, mark the questions you want to see again. They collect here with how you did on them, which is usually the useful part."
        />
      )}

      {questions.length > 0 && (
        <>
          <p className="text-sm text-slate-500">
            {list.data?.meta.total} {list.data?.meta.total === 1 ? 'question' : 'questions'} marked.
          </p>
          <ul className="space-y-2">
            {questions.map((question) => (
              <li key={question.questionId}>
                <Card>
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm text-slate-900">{question.text}</p>
                    <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
                      <Badge tone="slate">{question.difficulty}</Badge>
                      {question.isHistorical && <Badge tone="amber">Historical</Badge>}
                    </div>
                  </div>

                  <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm text-slate-500">
                    <div className="flex gap-1">
                      <dt>Attempts</dt>
                      <dd className="font-medium text-slate-700">{question.attempts}</dd>
                    </div>
                    <div className="flex gap-1">
                      <dt>Accuracy</dt>
                      <dd className="font-medium text-slate-700">
                        {/* `null` is not 0%: a question attempted once and skipped has no
                            accuracy to report, and printing "0%" would invent one. */}
                        {question.accuracy === null
                          ? 'not scored yet'
                          : `${Math.round(question.accuracy * 100)}%`}
                      </dd>
                    </div>
                    {question.financeActYear && (
                      <div className="flex gap-1">
                        <dt>Finance Act</dt>
                        <dd className="font-medium text-slate-700">{question.financeActYear}</dd>
                      </div>
                    )}
                  </dl>

                  {question.disclaimer && (
                    <p className="mt-1 text-xs text-amber-700">{question.disclaimer}</p>
                  )}

                  <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
                    <Link
                      to={`/search?q=${encodeURIComponent(question.text.slice(0, 60))}`}
                      className="font-medium text-brand-600 hover:underline"
                    >
                      Open this question
                    </Link>
                    <span className="text-slate-400">·</span>
                    <Link
                      to="/revision"
                      className="font-medium text-brand-600 hover:underline"
                    >
                      Revise flagged questions
                    </Link>
                  </div>
                </Card>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
