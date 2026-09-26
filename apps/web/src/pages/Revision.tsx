import { useState } from 'react'

import { Badge, Button, Card, EmptyState, ErrorState, SectionHeading, Spinner } from '../components/ui'
import { useLoader } from '../lib/useLoader'
import {
  fetchDueCards,
  fetchRevisionStats,
  gradeCard,
  type RevisionCard,
  type ReviewOutcome,
} from '../lib/queries'
import { QueryError } from '../lib/queries'

/**
 * The revision queue: spaced repetition, graded by the student.
 *
 * WHY THE STUDENT GRADES AND NOT THE APP. The schedule is SM-2: the interval is
 * computed from a 0-5 judgement of how well the answer was recalled. The app could
 * infer a score from whether an MCQ was answered correctly, and for MCQs it does
 * (a wrong answer queues the card). But "I got it right by guessing between two"
 * and "I could have taught it" are the same correct answer and completely
 * different memories, and only the student knows which happened. So the card is
 * revealed first and graded afterwards.
 */

const GRADES: { quality: number; label: string; hint: string }[] = [
  { quality: 0, label: 'Blank', hint: 'No idea at all' },
  { quality: 1, label: 'Wrong', hint: 'Recognised it only once I saw the answer' },
  { quality: 2, label: 'Hard', hint: 'Wrong, but the answer was on the tip of my tongue' },
  { quality: 3, label: 'Shaky', hint: 'Right, with a real struggle' },
  { quality: 4, label: 'Good', hint: 'Right, after a pause' },
  { quality: 5, label: 'Easy', hint: 'Immediate and certain' },
]

export default function RevisionPage() {
  const cards = useLoader((signal) => fetchDueCards(10, signal), [])
  const stats = useLoader((signal) => fetchRevisionStats(signal), [])

  const [revealed, setRevealed] = useState(false)
  const [outcome, setOutcome] = useState<ReviewOutcome | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<QueryError | null>(null)
  const [done, setDone] = useState(0)

  const queue = cards.data?.cards ?? []
  // Grading removes the head of the queue locally. Refetching after every grade
  // would re-render the whole list between two cards, which reads as a flicker on
  // a screen used for fifteen minutes at a stretch.
  const [position, setPosition] = useState(0)
  const card: RevisionCard | null = queue[position] ?? null

  async function grade(quality: number) {
    if (!card || busy) return
    setBusy(true)
    setError(null)
    try {
      const result = await gradeCard(card.questionId, quality)
      setOutcome(result)
      setDone((value) => value + 1)
    } catch (err: unknown) {
      setError(err as QueryError)
    } finally {
      setBusy(false)
    }
  }

  function nextCard() {
    setOutcome(null)
    setRevealed(false)
    setPosition((value) => value + 1)
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Revision</h1>
        <p className="mt-1 text-slate-600">
          Cards are scheduled so that you meet each question just before you would forget it.
        </p>
      </div>

      {stats.error && <ErrorState error={stats.error} what="the queue statistics" />}
      {stats.data && (
        <div className="grid gap-3 sm:grid-cols-4">
          <Stat label="Due now" value={stats.data.due} tone="brand" />
          <Stat label="Learning" value={stats.data.learning} />
          <Stat label="Mature" value={stats.data.mature} />
          <Stat
            label="Average interval"
            value={
              stats.data.averageIntervalDays
                ? `${stats.data.averageIntervalDays.toFixed(1)}d`
                : '—'
            }
          />
        </div>
      )}

      {cards.error && <ErrorState error={cards.error} what="your revision queue" />}
      {cards.loading && <Spinner label="Loading your queue…" />}

      {cards.data && queue.length === 0 && (
        <EmptyState
          title={done > 0 ? `Queue cleared — ${done} reviewed` : 'Nothing due right now'}
          body={
            done > 0
              ? 'Every card in this session has been rescheduled. Come back when the next one is due, or drill a chapter in practice.'
              : 'Answer questions in practice and this fills up: a wrong answer is queued immediately, and a bookmark adds a card on purpose.'
          }
        />
      )}

      {card && (
        <Card>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm text-slate-500">
            <span>
              Card {position + 1} of {queue.length}
            </span>
            <div className="flex items-center gap-2">
              <Badge tone="slate">{card.difficulty}</Badge>
              <Badge tone="brand">
                {card.repetitions === 0 ? 'New' : `${card.repetitions} reviews`}
              </Badge>
            </div>
          </div>

          <p className="text-base leading-relaxed text-slate-900">{card.text}</p>

          <ul className="mt-4 space-y-1.5">
            {card.options.map((option) => (
              <li key={option.label} className="text-slate-700">
                <span className="font-semibold text-slate-500">{option.label}.</span> {option.text}
              </li>
            ))}
          </ul>

          <div className="mt-5 rounded-md border border-dashed border-slate-300 bg-slate-50 p-4">
            <p className="text-sm font-medium text-slate-700">
              Answer it in your head, then say how it went.
            </p>
            <p className="mt-1 text-xs text-slate-500">
              The options are listed without the key on purpose: seeing the answer first is
              recognition, not recall, and recognition is what this system is designed to avoid.
            </p>
          </div>

          {error && (
            <div className="mt-4">
              <ErrorState error={error} what="the review" />
            </div>
          )}

          {!outcome ? (
            <div className="mt-5">
              {!revealed ? (
                <Button onClick={() => setRevealed(true)}>I have answered it — grade me</Button>
              ) : (
                <div>
                  <p className="text-sm font-medium text-slate-700">How did that go?</p>
                  <div className="mt-2 grid gap-2 sm:grid-cols-3">
                    {GRADES.map((option) => (
                      <button
                        key={option.quality}
                        type="button"
                        disabled={busy}
                        onClick={() => void grade(option.quality)}
                        title={option.hint}
                        className="rounded-md border border-slate-300 px-3 py-2 text-left text-sm hover:border-brand-500 hover:bg-brand-50 disabled:opacity-60"
                      >
                        <span className="font-medium text-slate-800">
                          {option.quality} · {option.label}
                        </span>
                        <span className="block text-xs text-slate-500">{option.hint}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="mt-5 rounded-md border border-slate-200 bg-white p-4">
              <SectionHeading
                title={outcome.lapsed ? 'Back to the start of the ladder' : 'Scheduled'}
                subtitle={
                  outcome.lapsed
                    ? 'A grade this low resets the interval — that is the system working, not failing.'
                    : `Next review in ${outcome.intervalDays} day${outcome.intervalDays === 1 ? '' : 's'}.`
                }
              />
              <p className="text-xs text-slate-500">
                Interval {outcome.intervalDays}d · ease factor {outcome.easeFactor.toFixed(2)} ·
                box {outcome.box}
              </p>
              <div className="mt-4">
                <Button onClick={nextCard}>Next card</Button>
              </div>
            </div>
          )}
        </Card>
      )}

      {done > 0 && (
        <p className="text-sm text-slate-500">
          {done} card{done === 1 ? '' : 's'} reviewed in this session.
        </p>
      )}
    </div>
  )
}

function Stat({
  label,
  value,
  tone = 'slate',
}: {
  label: string
  value: number | string
  tone?: 'slate' | 'brand'
}) {
  return (
    <Card>
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p
        className={`mt-1 text-2xl font-semibold ${
          tone === 'brand' ? 'text-brand-600' : 'text-slate-900'
        }`}
      >
        {value}
      </p>
    </Card>
  )
}
