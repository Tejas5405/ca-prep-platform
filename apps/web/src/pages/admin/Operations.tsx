/**
 * Operations: notifications, gamification, settings and the permission matrix.
 *
 * WHAT THEY HAVE IN COMMON: each one changes behaviour for everybody at once, which is
 * why every write here is audited server-side and why the settings screen is owner-only.
 */

import { useCallback, useState } from 'react'

import { useDebounced } from '../../lib/useDebounced'
import { useLoader } from '../../lib/useLoader'

import { Badge, Button, Card, EmptyState, ErrorState, Spinner } from '../../components/ui'
import {
  awardBadge,
  fetchAdminUsers,
  fetchBadges,
  fetchBroadcasts,
  fetchPermissionMatrix,
  fetchMyPermissions,
  fetchSettings,
  QueryError,
  sendBroadcast,
  updateSetting,
  type AdminBadge,
  type AdminUser,
  type PermissionMatrix,
  type PlatformSetting,
} from '../../lib/queries'

// ------------------------------------------------------------------ notifications

export function AdminNotifications() {
  const history = useLoader((signal) => fetchBroadcasts(1, 25, signal), [])
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [audience, setAudience] = useState('ALL_STUDENTS')
  const [role, setRole] = useState('STUDENT')
  const [linkUrl, setLinkUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<QueryError | null>(null)

  const send = useCallback(async () => {
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const result = await sendBroadcast({
        title,
        body,
        audience,
        role: audience === 'ROLE' ? role : null,
        linkUrl: linkUrl || null,
      })
      setMessage(
        `Sent to ${result.recipients} ${result.recipients === 1 ? 'person' : 'people'}.`,
      )
      setTitle('')
      setBody('')
      setLinkUrl('')
      history.reload()
    } catch (caught: unknown) {
      setError(caught instanceof QueryError ? caught : new QueryError(caught))
    } finally {
      setBusy(false)
    }
  }, [title, body, audience, role, linkUrl, history])

  return (
    <div className="space-y-6">
      <Card>
        <h2 className="text-sm font-semibold tracking-tight text-slate-900">Send an announcement</h2>
        <p className="mt-1 text-sm text-slate-600">
          The audience is resolved on the server — this form names a group and never a list of
          recipients.
        </p>
        <div className="mt-4 space-y-3">
          <label className="block">
            <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Title
            </span>
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={120}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="block">
            <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Body
            </span>
            <textarea
              value={body}
              onChange={(event) => setBody(event.target.value)}
              rows={3}
              maxLength={1000}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-3">
            <label>
              <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
                Audience
              </span>
              <select
                value={audience}
                onChange={(event) => setAudience(event.target.value)}
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="ALL_STUDENTS">All active students</option>
                <option value="ROLE">Everyone with a role</option>
                <option value="PREMIUM">Everyone on a paid plan</option>
              </select>
            </label>
            {audience === 'ROLE' && (
              <label>
                <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
                  Role
                </span>
                <select
                  value={role}
                  onChange={(event) => setRole(event.target.value)}
                  className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                >
                  {['STUDENT', 'EDITOR', 'CONTENT_MANAGER', 'MODERATOR', 'ADMIN'].map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label>
              <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
                Link (optional, must be a path on this site)
              </span>
              <input
                value={linkUrl}
                onChange={(event) => setLinkUrl(event.target.value)}
                placeholder="/practice"
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              />
            </label>
          </div>
          <div className="flex items-center gap-3">
            <Button onClick={() => void send()} disabled={busy || !title || !body}>
              Send
            </Button>
            {message && <span className="text-sm text-slate-600">{message}</span>}
          </div>
          <p className="text-xs text-slate-500">
            Delivery is in-app only. Out-of-band email needs a provider key (Resend) that is
            not configured on this deployment.
          </p>
        </div>
        {error && <div className="mt-3 text-sm text-wrong-600">{error.message}</div>}
      </Card>

      <Card>
        <h2 className="text-sm font-semibold tracking-tight text-slate-900">What has been sent</h2>
        {(history.data?.data ?? []).length === 0 ? (
          <p className="mt-2 text-sm text-slate-600">Nothing sent yet.</p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-100">
            {(history.data?.data ?? []).map((row) => (
              <li key={row.broadcastId} className="flex items-center justify-between py-2">
                <div>
                  <p className="text-sm text-slate-800">{row.title}</p>
                  <p className="text-xs text-slate-500">
                    {row.recipients} recipients · {row.read} read ·{' '}
                    {row.sentAt ? new Date(row.sentAt).toLocaleDateString('en-IN') : ''}
                  </p>
                </div>
                <Badge tone="slate">{row.kind}</Badge>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

// ------------------------------------------------------------------- gamification

export function AdminBadges() {
  // The catalogue is read once: nothing in this screen changes it, only awards badges.
  const catalogue = useLoader((signal) => fetchBadges(signal), [])
  const badges = catalogue.data ?? []
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<QueryError | null>(null)

  const term = useDebounced(query.trim(), 250)
  const search = useLoader(
    (signal) => fetchAdminUsers({ q: term, limit: 6 }, signal),
    [term],
    term.length >= 2,
  )
  const users = term.length >= 2 ? (search.data?.data ?? []) : []

  const award = useCallback(async (badge: AdminBadge, user: AdminUser) => {
    setBusy(true)
    setMessage(null)
    try {
      const result = await awardBadge(badge.id, user.id)
      setMessage(
        result.awarded
          ? `${badge.name} awarded to ${user.displayName ?? user.email}.`
          : `${user.displayName ?? user.email} already had ${badge.name}.`,
      )
      // The picked student is cleared by emptying the box; the results are derived from
      // the debounced term, so there is no second list to keep in step.
      setQuery('')
    } catch (caught: unknown) {
      setError(caught instanceof QueryError ? caught : new QueryError(caught))
    } finally {
      setBusy(false)
    }
  }, [])

  if (catalogue.loading) return <Spinner label="Loading badges…" />
  if (error || catalogue.error) {
    return <ErrorState error={(error ?? catalogue.error)!} what="the badge catalogue" />
  }

  return (
    <div className="space-y-6">
      <Card>
        <h2 className="text-sm font-semibold tracking-tight text-slate-900">Award by hand</h2>
        <p className="mt-1 text-sm text-slate-600">
          For the badges no rule can evaluate — a paper sat in a coaching class, a correction
          made to the bank. Automated badges are earned by the student's own activity.
        </p>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Find the student by email or name"
          className="mt-3 w-full max-w-md rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        {message && <p className="mt-2 text-sm text-slate-600">{message}</p>}
        {users.length > 0 && (
          <ul className="mt-2 space-y-2">
            {users.map((user) => (
              <li key={user.id} className="rounded-md border border-slate-200 p-2">
                <p className="text-sm text-slate-800">
                  {user.displayName ?? 'Unnamed'} · {user.email}
                </p>
                <div className="mt-1 flex flex-wrap gap-2">
                  {badges.map((badge) => (
                    <Button
                      key={badge.id}
                      tone="quiet"
                      disabled={busy}
                      onClick={() => void award(badge, user)}
                    >
                      {badge.icon} {badge.name}
                    </Button>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {badges.length === 0 ? (
        <EmptyState
          title="No badges defined"
          body="The catalogue is empty. The default eight are written by the migration that created this table — if you are seeing none, the seed has been cleared."
        />
      ) : (
        <ul className="space-y-2">
          {badges.map((badge) => (
            <li key={badge.id}>
              <Card>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-lg">{badge.icon}</span>
                      <span className="text-sm font-medium text-slate-900">{badge.name}</span>
                      <Badge tone={badge.isActive ? 'right' : 'slate'}>
                        {badge.isActive ? 'Active' : 'Retired'}
                      </Badge>
                    </div>
                    <p className="mt-1 text-xs text-slate-500">
                      {badge.description} · earned by{' '}
                      {badge.criteriaKind === 'MANUAL'
                        ? 'an administrator'
                        : `${badge.criteriaKind.toLowerCase().replace('_', ' ')} ≥ ${badge.criteriaValue}`}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-sm tabular-nums text-slate-900">{badge.awardedCount}</p>
                    <p className="text-xs text-slate-500">awarded</p>
                  </div>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ----------------------------------------------------------------------- settings

export function AdminSettings() {
  const catalogue = useLoader((signal) => fetchSettings(signal), [])
  const settings = catalogue.data ?? []
  const [actionError, setActionError] = useState<QueryError | null>(null)
  const [saving, setSaving] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const error = actionError ?? catalogue.error
  const reload = catalogue.reload

  const write = useCallback(
    async (setting: PlatformSetting, value: unknown) => {
      setSaving(setting.key)
      setSaved(null)
      try {
        await updateSetting(setting.key, value)
        reload()
        setSaved(setting.key)
      } catch (caught: unknown) {
        setActionError(caught instanceof QueryError ? caught : new QueryError(caught))
      } finally {
        setSaving(null)
      }
    },
    [reload],
  )

  if (catalogue.loading) return <Spinner label="Loading settings…" />
  if (error) return <ErrorState error={error} what="platform settings" />

  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-600">
        These change behaviour for everyone immediately — no deploy. Every edit is recorded in
        the audit log with your name on it. Secret-looking keys are refused by the API: a
        credential belongs in the environment, not in a settings row a browser can read.
      </p>
      <ul className="space-y-2">
        {settings.map((setting) => {
          const value = setting.value?.value
          const isBool = typeof value === 'boolean'
          const isNumber = typeof value === 'number'
          return (
            <li key={setting.key}>
              <Card>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-slate-800">{setting.key}</span>
                      {setting.isPublic && <Badge tone="slate">Public</Badge>}
                      {saved === setting.key && <Badge tone="right">Saved</Badge>}
                    </div>
                    {setting.description && (
                      <p className="mt-1 text-xs text-slate-500">{setting.description}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {isBool ? (
                      <Button
                        tone="secondary"
                        disabled={saving === setting.key}
                        onClick={() => void write(setting, !value)}
                      >
                        {value ? 'On — turn off' : 'Off — turn on'}
                      </Button>
                    ) : isNumber ? (
                      <input
                        type="number"
                        defaultValue={value as number}
                        disabled={saving === setting.key}
                        onBlur={(event) => {
                          const next = Number(event.target.value)
                          if (Number.isFinite(next) && next !== value) void write(setting, next)
                        }}
                        className="w-24 rounded-md border border-slate-300 px-2 py-1 text-sm"
                      />
                    ) : (
                      <input
                        type="text"
                        defaultValue={String(value ?? '')}
                        disabled={saving === setting.key}
                        onBlur={(event) => {
                          if (event.target.value !== String(value ?? '')) {
                            void write(setting, event.target.value)
                          }
                        }}
                        className="w-56 rounded-md border border-slate-300 px-2 py-1 text-sm"
                      />
                    )}
                  </div>
                </div>
              </Card>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

// ------------------------------------------------------------------ permissions

export function AdminPermissions() {
  const matrix = useLoader<PermissionMatrix>((signal) => fetchPermissionMatrix(signal), [])
  const mine = useLoader((signal) => fetchMyPermissions(signal), [])

  if (matrix.loading || mine.loading) return <Spinner label="Loading the matrix…" />
  if (matrix.error) return <ErrorState error={matrix.error} what="the permission matrix" />
  if (!matrix.data) return null
  // Held in a local because the guards above narrow `matrix.data`, but that narrowing is
  // lost inside the `.map` callbacks below - which is where the table is built.
  const grid = matrix.data

  const myPermissions = new Set(mine.data?.permissions ?? [])

  return (
    <div className="space-y-6">
      <Card>
        <h2 className="text-sm font-semibold tracking-tight text-slate-900">Your access</h2>
        <p className="mt-1 text-sm text-slate-600">
          {mine.data?.role} · {myPermissions.size} permission
          {myPermissions.size === 1 ? '' : 's'}
          {mine.data?.isOwner ? ' · owner' : ''}. This is the server's own answer, fetched with
          your token.
        </p>
        <div className="mt-3 flex flex-wrap gap-1">
          {[...myPermissions].sort().map((permission) => (
            <Badge key={permission} tone="brand">
              {permission}
            </Badge>
          ))}
          {myPermissions.size === 0 && (
            <span className="text-sm text-slate-500">No administrative permissions.</span>
          )}
        </div>
      </Card>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Role and permission matrix</caption>
          <thead>
            <tr className="border-b border-slate-200">
              <th scope="col" className="py-2 pr-4 font-medium text-slate-700">
                Permission
              </th>
              {grid.roles.map((role) => (
                <th key={role.role} scope="col" className="px-2 py-2 font-medium text-slate-700">
                  {role.role.replace('_', ' ')}
                  <span className="block text-xs font-normal text-slate-500">
                    rank {role.rank}
                    {role.assignable ? '' : ' · not assignable'}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.permissions.map((permission) => (
              <tr key={permission.key} className="border-b border-slate-100">
                <th scope="row" className="py-2 pr-4 font-normal">
                  <span className="font-mono text-xs text-slate-800">{permission.key}</span>
                  {permission.ownerOnly && (
                    <span className="ml-2 text-[10px] uppercase tracking-wide text-amber-700">
                      owner only
                    </span>
                  )}
                </th>
                {grid.roles.map((role) => (
                  <td key={role.role} className="px-2 py-2">
                    {role.permissions.includes(permission.key) ? (
                      <span className="text-right-600" aria-label="allowed">
                        ✓
                      </span>
                    ) : (
                      <span className="text-slate-300" aria-label="not allowed">
                        ·
                      </span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-500">
        Read from the API's own permission table, which is the same table every route checks. A
        role change takes effect on the account's next request, because the API reads the role
        from the database rather than from the token.
      </p>
    </div>
  )
}
