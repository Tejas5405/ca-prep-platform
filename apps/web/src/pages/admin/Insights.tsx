/**
 * Analytics and the audit log — the two read-only screens an owner checks.
 *
 * ANALYTICS SHOWS ONLY WHAT IS RECORDED
 *
 * The event log is written by the API (`record_event`) at the moments that matter -
 * sign-ins, answers, mock submissions, documents published - and this screen is a
 * grouped count over that table with a daily series. There is no client-side tracking
 * and no third-party analytics script: a chart of nothing would be worse than a chart
 * of the truth, and the truth is that front-end instrumentation has not been added yet.
 * That absence is stated on the screen rather than papered over with a placeholder line.
 *
 * THE AUDIT LOG IS APPEND-ONLY AND COPY-DENORMALISED
 *
 * Each row carries the actor's email and role AS THEY WERE at the time, which is why the
 * table has no foreign key to users: an audit entry that changes when a user is renamed
 * is not an audit entry. Suspensions, role changes, grants, revocations, settings changes
 * and publications all land here.
 */

import { useState } from 'react'

import { Badge, Card, ErrorState, EmptyState, Spinner } from '../../components/ui'
import { useLoader } from '../../lib/useLoader'
import { fetchAnalytics, fetchAuditLog } from '../../lib/queries'

export function AdminAnalytics() {
  const state = useLoader((signal) => fetchAnalytics(30, signal), [])
  if (state.loading) return <Spinner label="Counting events…" />
  if (state.error) return <ErrorState error={state.error} what="analytics" />
  if (!state.data) return null

  const { totals, daily, totalEvents, windowDays } = state.data
  const peak = daily.reduce((max, day) => Math.max(max, day.count), 0)

  return (
    <div className="space-y-6">
      <Card>
        <h2 className="text-sm font-semibold tracking-tight text-slate-900">
          {totalEvents.toLocaleString('en-IN')} events in {windowDays} days
        </h2>
        {totalEvents === 0 ? (
          <p className="mt-2 text-sm text-slate-600">
            Nothing recorded yet. Events are written server-side as students practise, sit
            mocks and sign in — there is no browser tracking script, so an empty chart here
            means an empty log, not a broken screen.
          </p>
        ) : (
          <div className="mt-4 flex h-32 items-end gap-1" role="img" aria-label="Events per day">
            {daily.map((day) => (
              <div
                key={day.date}
                className="flex-1 rounded-t bg-brand-500/70"
                style={{ height: `${peak === 0 ? 0 : Math.max(2, (day.count / peak) * 100)}%` }}
                title={`${day.date}: ${day.count}`}
              />
            ))}
          </div>
        )}
      </Card>

      {totals.length > 0 && (
        <Card>
          <h2 className="text-sm font-semibold tracking-tight text-slate-900">By event</h2>
          <ul className="mt-3 divide-y divide-slate-100">
            {totals.map((row) => (
              <li key={row.event} className="flex items-center justify-between py-2">
                <span className="font-mono text-xs text-slate-700">{row.event}</span>
                <span className="text-sm tabular-nums text-slate-900">
                  {row.count.toLocaleString('en-IN')}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

export function AdminAudit() {
  const [action, setAction] = useState('')
  // Spread rather than `action: action || undefined`: the client's params type is
  // `action?: string`, and with exactOptionalPropertyTypes an explicit undefined is not
  // the same as an absent key.
  const state = useLoader(
    (signal) => fetchAuditLog({ ...(action ? { action } : {}), limit: 100 }, signal),
    [action],
  )

  if (state.loading) return <Spinner label="Reading the log…" />
  if (state.error) return <ErrorState error={state.error} what="the audit log" />

  const entries = state.data?.data ?? []

  return (
    <div className="space-y-4">
      <label className="block max-w-sm">
        <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
          Filter by action prefix
        </span>
        <input
          type="search"
          value={action}
          onChange={(event) => setAction(event.target.value)}
          placeholder="e.g. user.role_changed, access., document."
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
      </label>

      {entries.length === 0 ? (
        <EmptyState
          title="Nothing logged"
          body="Administrative actions appear here as they happen: role changes, suspensions, grants, revocations, document archives and settings edits."
        />
      ) : (
        <ul className="space-y-2">
          {entries.map((entry) => (
            <li key={entry.id}>
              <Card>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="slate">{entry.action}</Badge>
                      {entry.actorRole && <span className="text-xs text-slate-500">{entry.actorRole}</span>}
                    </div>
                    <p className="mt-1 text-sm text-slate-800">
                      {entry.summary ?? 'No summary recorded'}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      {entry.actorEmail ?? 'unknown actor'}
                      {entry.targetType ? ` · ${entry.targetType} ${entry.targetId?.slice(0, 8)}` : ''}
                      {entry.ipAddress ? ` · ${entry.ipAddress}` : ''}
                    </p>
                  </div>
                  <span className="text-xs tabular-nums text-slate-500">
                    {entry.createdAt ? new Date(entry.createdAt).toLocaleString('en-IN') : ''}
                  </span>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
