import { useSearchParams } from 'react-router-dom'

import { Badge, Card, EmptyState, ErrorState, Meter, Spinner } from '../components/ui'
import {
  fetchAchievements,
  fetchBadgeBoard,
  fetchLeaderboard,
  fetchPoints,
  fetchStreak,
  pointsReasonLabel,
  type LeaderboardWindow,
} from '../lib/gamification'
import { useLoader } from '../lib/useLoader'

/**
 * Badges, the points ledger and the leaderboard.
 *
 * Progress already answers "where should I study". This page answers "what has the
 * work earned". The two are separate on purpose: a progress screen that leads with
 * a streak optimises for the streak, and a badge screen that recomputes accuracy
 * would be a second implementation of the progress rules.
 *
 * NOTHING IS COMPUTED HERE. Rank, totals and progress toward a badge come from the
 * API. A bar is drawn only when the server sent a number. `progress: null` means
 * the criterion cannot be reconstructed (a streak badge, a mock score), and drawing
 * 0% for that would tell the student they had not started.
 *
 * The leaderboard can be turned off. That is a 403 with a specific title, not a
 * broken page, and it must not take the badges down with it — they are a different
 * request. Names are rendered as text. Avatars are not loaded: the URL is whatever
 * was stored on the account, and a leaderboard that fetches arbitrary image URLs
 * is a tracking pixel with a rank next to it.
 */

const WINDOWS: { id: LeaderboardWindow; label: string }[] = [
  { id: 'week', label: 'This week' },
  { id: 'month', label: 'This month' },
  { id: 'all', label: 'All time' },
]

function windowFrom(value: string | null): LeaderboardWindow {
  if (value === 'month' || value === 'all' || value === 'week') return value
  return 'week'
}

export default function AchievementsPage() {
  const [params, setParams] = useSearchParams()
  const window = windowFrom(params.get('window'))

  const summary = useLoader((signal) => fetchAchievements(signal), [])
  const streak = useLoader((signal) => fetchStreak(signal), [])
  const points = useLoader((signal) => fetchPoints(signal), [])
  const badges = useLoader((signal) => fetchBadgeBoard(signal), [])
  const board = useLoader((signal) => fetchLeaderboard(window, signal), [window])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Achievements</h1>
        <p className="mt-1 max-w-2xl text-slate-600">
          Points and badges from practice. The board shows names and totals only — not email
          addresses, and not who is behind a name.
        </p>
      </div>

      {summary.loading && <Spinner label="Adding up your points…" />}
      {summary.error && <ErrorState error={summary.error} what="your achievements" />}
      {summary.data && (
        <div className="grid gap-3 sm:grid-cols-4">
          <Stat label="Points" value={summary.data.points} />
          <Stat
            label="Badges"
            value={`${summary.data.badgesEarned}/${summary.data.badgesAvailable}`}
          />
          <Stat
            label="All-time rank"
            value={summary.data.totalRanked === 0 ? '—' : summary.data.rank}
          />
          <Stat label="Current streak" value={streak.data ? streak.data.current : '—'} />
        </div>
      )}

      <section aria-labelledby="leaderboard">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 id="leaderboard" className="text-lg font-medium text-slate-900">
            Leaderboard
          </h2>
          <div className="flex gap-1" role="group" aria-label="Leaderboard window">
            {WINDOWS.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-pressed={window === item.id}
                onClick={() => {
                  const next = new URLSearchParams(params)
                  if (item.id === 'week') next.delete('window')
                  else next.set('window', item.id)
                  setParams(next)
                }}
                className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                  window === item.id
                    ? 'bg-brand-50 text-brand-700'
                    : 'text-slate-600 hover:bg-slate-100'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        {board.loading && <Spinner label="Loading the board…" />}
        {board.error && board.error.isForbidden && (
          <EmptyState
            title="Leaderboard is off"
            body="An administrator has turned the board off. Your points and badges are unaffected."
          />
        )}
        {board.error && !board.error.isForbidden && (
          <ErrorState error={board.error} what="the leaderboard" />
        )}
        {board.data && (
          <Card className="mt-3">
            <p className="text-sm text-slate-600">
              You are {ordinal(board.data.you.rank)} {windowLabel(window)}, with{' '}
              {board.data.you.points} point{board.data.you.points === 1 ? '' : 's'}.
              {typeof board.data.you.pointsToNext === 'number' &&
                board.data.you.pointsToNext > 0 &&
                ` ${board.data.you.pointsToNext} more to the next rank.`}
            </p>
            {board.data.entries.length === 0 ? (
              <p className="mt-3 text-sm text-slate-500">
                No points have been earned in this window yet.
              </p>
            ) : (
              <ol className="mt-3 divide-y divide-slate-100">
                {board.data.entries.map((entry) => (
                  <li
                    key={`${entry.rank}-${entry.name}`}
                    className={`flex items-center justify-between py-2 text-sm ${
                      entry.isYou ? 'font-medium text-brand-700' : 'text-slate-800'
                    }`}
                  >
                    <span>
                      <span className="inline-block w-8 text-slate-500">{entry.rank}</span>
                      {entry.name}
                      {entry.isYou && <span className="ml-2 text-xs">You</span>}
                    </span>
                    <span>{entry.points}</span>
                  </li>
                ))}
              </ol>
            )}
          </Card>
        )}
      </section>

      <section aria-labelledby="badges">
        <h2 id="badges" className="text-lg font-medium text-slate-900">
          Badges
        </h2>
        {badges.loading && <Spinner label="Loading badges…" />}
        {badges.error && <ErrorState error={badges.error} what="your badges" />}
        {badges.data && badges.data.badges.length === 0 && (
          <EmptyState
            title="No badges yet"
            body="The catalogue is empty. Badges appear here once they have been created."
          />
        )}
        {badges.data && badges.data.badges.length > 0 && (
          <ul className="mt-3 grid gap-3 sm:grid-cols-2">
            {badges.data.badges.map((badge) => {
              const ratio =
                badge.progress !== null && badge.criteriaValue
                  ? Math.min(1, badge.progress / badge.criteriaValue)
                  : null
              return (
                <li key={badge.code}>
                  <Card>
                    <div className="flex items-center justify-between gap-2">
                      <h3 className="font-medium text-slate-900">{badge.name}</h3>
                      {badge.earned ? <Badge tone="right">Earned</Badge> : <Badge>Not yet</Badge>}
                    </div>
                    <p className="mt-1 text-sm text-slate-600">{badge.description}</p>
                    {ratio !== null && !badge.earned && (
                      <div className="mt-3">
                        <Meter
                          value={ratio}
                          label={`${badge.progress} of ${badge.criteriaValue}`}
                        />
                      </div>
                    )}
                    {badge.progress === null && !badge.earned && badge.criteriaValue !== null && (
                      <p className="mt-2 text-xs text-slate-500">
                        Progress toward this one is not counted on this screen.
                      </p>
                    )}
                  </Card>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <section aria-labelledby="points-ledger">
        <h2 id="points-ledger" className="text-lg font-medium text-slate-900">
          How the points were earned
        </h2>
        {points.loading && <Spinner label="Loading the ledger…" />}
        {points.error && <ErrorState error={points.error} what="your points" />}
        {points.data && points.data.recent.length === 0 && (
          <p className="mt-2 text-sm text-slate-500">
            Nothing in the ledger yet. Correct answers in practice are what write the first row.
          </p>
        )}
        {points.data && points.data.recent.length > 0 && (
          <ul className="mt-3 divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
            {points.data.recent.map((entry, index) => (
              <li
                key={`${entry.createdAt ?? 'row'}-${index}`}
                className="flex items-center justify-between px-4 py-2 text-sm"
              >
                <span className="text-slate-700">{pointsReasonLabel(entry.reason)}</span>
                <span className="font-medium text-slate-900">+{entry.points}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <p className="text-xs font-medium tracking-wide text-slate-500 uppercase">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  )
}

function windowLabel(window: LeaderboardWindow): string {
  if (window === 'month') return 'this month'
  if (window === 'all') return 'all time'
  return 'this week'
}

function ordinal(rank: number): string {
  const mod100 = rank % 100
  if (mod100 >= 11 && mod100 <= 13) return `${rank}th`
  switch (rank % 10) {
    case 1:
      return `${rank}st`
    case 2:
      return `${rank}nd`
    case 3:
      return `${rank}rd`
    default:
      return `${rank}th`
  }
}
