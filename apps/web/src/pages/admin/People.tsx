/**
 * Students: search, inspect, and change a role.
 *
 * THE ROLE CHANGE IS THE DANGEROUS PART, AND THE SERVER OWNS THE RULES
 *
 * This screen offers 'every role the caller may assign' and the API refuses anything
 * else, twice: SUPER_ADMIN is not an assignable value at all, and a caller cannot assign
 * a role at or above its own rank unless it is the owner. Those refusals are the control;
 * the truncated dropdown here is a courtesy that stops an operator from wasting a click.
 *
 * SUSPENDING AN ACCOUNT IS A SEPARATE, AUDITED ACT. A suspended student keeps their data
 * and loses access, which is what an account-takeover report needs, and the audit entry
 * records who did it.
 */

import { useCallback, useState } from 'react'

import { Badge, Button, Card, EmptyState, ErrorState, Spinner } from '../../components/ui'
import { useDebounced } from '../../lib/useDebounced'
import { useLoader } from '../../lib/useLoader'
import {
  fetchAdminUser,
  fetchAdminUsers,
  QueryError,
  updateAdminUser,
  type AdminUserDetail,
} from '../../lib/queries'

const ROLE_ORDER = ['STUDENT', 'EDITOR', 'CONTENT_MANAGER', 'MODERATOR', 'ADMIN']

export default function AdminPeople() {
  const [query, setQuery] = useState('')
  const [role, setRole] = useState('')
  const [actionError, setActionError] = useState<QueryError | null>(null)
  const [open, setOpen] = useState<AdminUserDetail | null>(null)
  const [busy, setBusy] = useState(false)

  // The list through the shared loader, which also removes the debounce timer this
  // effect used to hand-roll: the term is debounced as a VALUE, so the request count
  // is the same and "the box is empty" is a render-time fact rather than a state write.
  const term = useDebounced(query.trim(), query ? 250 : 0)
  const list = useLoader(
    (signal) =>
      fetchAdminUsers(
        { ...(term ? { q: term } : {}), ...(role ? { role } : {}), limit: 50 },
        signal,
      ),
    [term, role],
  )
  const users = list.data?.data ?? []
  const total = list.data?.meta.total ?? 0
  const loading = list.loading
  const error = actionError ?? list.error
  const reload = list.reload

  const inspect = useCallback(async (userId: string) => {
    setBusy(true)
    try {
      setOpen(await fetchAdminUser(userId))
    } catch (caught: unknown) {
      setActionError(caught instanceof QueryError ? caught : new QueryError(caught))
    } finally {
      setBusy(false)
    }
  }, [])

  const change = useCallback(
    async (userId: string, changes: { role?: string; isActive?: boolean }) => {
      setBusy(true)
      try {
        await updateAdminUser(userId, changes)
        reload()
        if (open?.user.id === userId) setOpen(await fetchAdminUser(userId))
      } catch (caught: unknown) {
        setActionError(caught instanceof QueryError ? caught : new QueryError(caught))
      } finally {
        setBusy(false)
      }
    },
    [reload, open],
  )

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex-1 min-w-[220px]">
          <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
            Search
          </span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Email or name"
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
        </label>
        <label>
          <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
            Role
          </span>
          <select
            value={role}
            onChange={(event) => setRole(event.target.value)}
            className="mt-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
          >
            <option value="">Any</option>
            {ROLE_ORDER.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && <ErrorState error={error} what="the student list" />}
      {loading && <Spinner label="Loading students…" />}
      {!loading && users.length === 0 && !error && (
        <EmptyState title="No accounts match" body="Try a different search, or clear the role filter." />
      )}

      {users.length > 0 && (
        <p className="text-sm text-slate-600">{total.toLocaleString('en-IN')} accounts</p>
      )}

      <ul className="space-y-2">
        {users.map((user) => (
          <li key={user.id}>
            <Card>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-slate-900">
                      {user.displayName ?? 'Unnamed'}
                    </span>
                    <Badge tone={user.role === 'STUDENT' ? 'slate' : 'brand'}>{user.role}</Badge>
                    {!user.isActive && <Badge tone="amber">Suspended</Badge>}
                    {user.subscription && (
                      <Badge tone="right">
                        {user.subscription.tier}
                        {user.subscription.status !== 'ACTIVE' ? ` · ${user.subscription.status}` : ''}
                      </Badge>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    {user.email}
                    {user.lastActiveAt
                      ? ` · last seen ${new Date(user.lastActiveAt).toLocaleDateString('en-IN')}`
                      : ' · never signed in'}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button tone="quiet" onClick={() => void inspect(user.id)} disabled={busy}>
                    Inspect
                  </Button>
                  <Button
                    tone="quiet"
                    disabled={busy}
                    onClick={() => void change(user.id, { isActive: !user.isActive })}
                    title="Suspending keeps their data and blocks sign-in. Recorded in the audit log."
                  >
                    {user.isActive ? 'Suspend' : 'Reactivate'}
                  </Button>
                </div>
              </div>

              {open?.user.id === user.id && (
                <div className="mt-4 rounded-md bg-slate-50 p-3">
                  <div className="grid gap-3 sm:grid-cols-4">
                    <Metric label="Questions touched" value={open.progress.questionsTouched} />
                    <Metric label="Attempts" value={open.progress.attempts} />
                    <Metric label="Correct" value={open.progress.correct} />
                    <Metric
                      label="Accuracy"
                      value={
                        open.progress.accuracy === null
                          ? '—'
                          : `${Math.round(open.progress.accuracy * 100)}%`
                      }
                    />
                  </div>
                  <p className="mt-3 text-xs text-slate-500">
                    Entitlements: {open.entitlements.entitlements.join(', ') || 'none'} ·{' '}
                    {open.badges.length} badge{open.badges.length === 1 ? '' : 's'}
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <label className="text-xs font-medium uppercase tracking-wide text-slate-500">
                      Change role
                    </label>
                    <select
                      defaultValue={open.user.role}
                      disabled={busy}
                      onChange={(event) => void change(open.user.id, { role: event.target.value })}
                      className="rounded-md border border-slate-300 px-2 py-1 text-sm"
                    >
                      {ROLE_ORDER.map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                    <span className="text-xs text-slate-500">
                      The API refuses SUPER_ADMIN and any role at or above your own.
                    </span>
                  </div>
                </div>
              )}
            </Card>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="text-lg font-semibold tabular-nums text-slate-900">{value}</p>
    </div>
  )
}
