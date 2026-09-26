import { Link } from 'react-router-dom'

import {
  Badge,
  Card,
  EmptyState,
  ErrorState,
  Meter,
  SectionHeading,
  Spinner,
} from '../components/ui'
import { useLoader } from '../lib/useLoader'
import { fetchProjection } from '../lib/campus'
import { fetchProgress } from '../lib/queries'

/**
 * Progress: what the numbers say about where to spend the next hour.
 *
 * THE ORDER IS THE POINT. Totals first because they answer "am I doing the work",
 * then focus areas because they answer "what should I do next", then activity
 * because it answers "am I consistent". A dashboard that leads with a streak
 * number optimises for the streak instead of for the exam.
 *
 * NOTHING HERE IS COMPUTED IN THE BROWSER. Accuracy, focus areas and the level
 * ladder all come from the API already resolved. Recomputing them client-side
 * would be a second implementation of the same rules, and the first page to
 * disagree with the backend would be the one a student trusts.
 */
export default function ProgressPage() {
  const { data, error, loading } = useLoader((signal) => fetchProgress(30, signal), [])
  const projection = useLoader((signal) => fetchProjection(signal), [])

  if (loading) return <Spinner label="Crunching your practice history…" />
  if (error) return <ErrorState error={error} what="your progress" />
  if (!data) return null

  const accuracy = data.profile.overallAccuracy
  const hasWork = data.totals.attempts > 0

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Progress</h1>
        <p className="mt-1 text-slate-600">Last 30 days of practice, revision and mock exams.</p>
      </div>

      {projection.data && (
        <Card>
          <p className="text-sm font-medium text-slate-900">Practice trend</p>
          <p className="mt-1 text-sm text-slate-600">{projection.data.note}</p>
        </Card>
      )}

      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Questions attempted" value={data.totals.attempts} />
        <Stat label="Correct" value={data.totals.correct} />
        <Stat
          label="Accuracy"
          value={accuracy === null ? '—' : `${Math.round(accuracy * 100)}%`}
          accent
        />
        <Stat label="Revision due" value={data.revision.due} />
      </div>

      {!hasWork && (
        <EmptyState
          title="Nothing to measure yet"
          body="Answer a few questions in practice and this page fills in: accuracy by paper, the chapters costing you marks, and a study streak."
          action={
            <Link to="/practice" className="text-sm font-medium text-brand-600 hover:underline">
              Start practising
            </Link>
          }
        />
      )}

      {hasWork && (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <SectionHeading
                title="Where the marks are going"
                subtitle="Chapters with the most wrong answers, weighted by how often they are examined."
              />
              {data.focusAreas.length === 0 ? (
                <p className="text-sm text-slate-500">
                  No weak chapters yet — five attempts in a chapter is what it takes to judge one
                  fairly, and judging earlier than that would just be noise.
                </p>
              ) : (
                <ul className="divide-y divide-slate-100">
                  {data.focusAreas.map((area) => (
                    <li key={area.entityId} className="py-2.5">
                      <div className="flex items-center justify-between gap-3">
                        <span className="min-w-0 truncate text-sm font-medium text-slate-800">
                          {area.name}
                        </span>
                        <Badge tone="wrong">{Math.round(area.accuracy * 100)}%</Badge>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card>
              <SectionHeading
                title="Strong areas"
                subtitle="Chapters you can leave alone until the final revision."
              />
              {data.strongAreas.length === 0 ? (
                <p className="text-sm text-slate-500">
                  A chapter counts as strong at 80% accuracy over at least five attempts.
                </p>
              ) : (
                <ul className="divide-y divide-slate-100">
                  {data.strongAreas.map((area) => (
                    <li key={area.entityId} className="flex items-center justify-between py-2.5">
                      <span className="min-w-0 truncate text-sm font-medium text-slate-800">
                        {area.name}
                      </span>
                      <Badge tone="right">{Math.round(area.accuracy * 100)}%</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          {data.bySubject.length > 0 && (
            <Card>
              <SectionHeading title="By paper" />
              <ul className="space-y-3">
                {data.bySubject.map((subject) => (
                  <li key={subject.subjectId}>
                    <Meter
                      value={subject.accuracy}
                      label={`${subject.name} — ${subject.attempts} attempted`}
                      tone={subject.accuracy >= 0.6 ? 'right' : 'wrong'}
                    />
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <SectionHeading
                title="Level"
                subtitle={`${data.profile.totalPoints} points · ${data.profile.nextLevel ? `${data.profile.pointsToNextLevel} to ${data.profile.nextLevel.toLowerCase()}` : 'highest level reached'}`}
              />
              <Meter value={data.profile.levelProgress} label={data.profile.level} />
              <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-slate-600">
                <span>
                  Current streak{' '}
                  <strong className="text-slate-900">{data.profile.currentStreak}</strong>
                </span>
                <span>
                  Longest <strong className="text-slate-900">{data.profile.longestStreak}</strong>
                </span>
                <Link to="/achievements" className="font-medium text-brand-600 hover:underline">
                  Badges and leaderboard
                </Link>
              </div>
            </Card>

            <Card>
              <SectionHeading
                title="Study activity"
                subtitle="Minutes studied and questions attempted, by day."
              />
              {data.recentActivity.length === 0 ? (
                <p className="text-sm text-slate-500">No activity logged in this window.</p>
              ) : (
                <ul className="divide-y divide-slate-100 text-sm">
                  {data.recentActivity.map((day) => (
                    <li key={day.date} className="flex items-center justify-between py-2">
                      <span className="text-slate-600">
                        {new Date(day.date).toLocaleDateString(undefined, {
                          day: 'numeric',
                          month: 'short',
                        })}
                      </span>
                      <span className="text-slate-800">
                        {day.questionsAttempted} questions · {day.correctCount} correct
                        {day.minutesStudied ? ` · ${day.minutesStudied} min` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </>
      )}

      <Card>
        <SectionHeading
          title="Revision queue health"
          subtitle="Cards you are learning versus cards you have not forgotten yet."
        />
        <div className="grid gap-3 sm:grid-cols-3">
          <Stat label="Due now" value={data.revision.due} accent />
          <Stat label="Learning" value={data.revision.learning} />
          <Stat label="Mature" value={data.revision.mature} />
        </div>
        {data.revision.total > 0 && (
          <p className="mt-3 text-sm text-slate-500">
            Average interval {data.revision.averageIntervalDays.toFixed(1)} days. A rising average
            is the sign the queue is working.
          </p>
        )}
      </Card>
    </div>
  )
}

function Stat({
  label,
  value,
  accent = false,
}: {
  label: string
  value: number | string
  accent?: boolean
}) {
  return (
    <Card>
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${accent ? 'text-brand-600' : 'text-slate-900'}`}>
        {value}
      </p>
    </Card>
  )
}
