/**
 * Access control: grant, deny, see why.
 *
 * THE THREE QUESTIONS THIS SCREEN ANSWERS
 *
 *   1. "Who can see this?"  - the grant list, filtered by student, course or kind.
 *   2. "How do I open this up for someone?" - one form, whose axes are the spec's list:
 *      a person, a role, a subscription tier or a plan, optionally narrowed to a course,
 *      a subject or a content type.
 *   3. "Why can't this student read this?" - the explain panel, which runs the STUDENT'S
 *      OWN access decision on the server and prints the reason. Without it an operator
 *      ticks a box, sees nothing change, and cannot tell whether the grant failed, a
 *      DENY is outranking it, the document is unpublished, or the subscription lapsed.
 *
 * WHY GRANTS ARE LIBRARY-WIDE
 *
 * A per-document rule cannot express "open Financial Reporting for this student" without
 * one row per document - and every document uploaded afterwards would fall outside it.
 * A grant is evaluated at query time against the document's own course/subject/kind, so
 * it covers material that does not exist yet. That is the difference between a grant and
 * a fan-out.
 *
 * REVOKING SETS A TIMESTAMP
 *
 * The row stays. "Who could read this in March, and who took it away" is the first
 * question asked after a leak, and a deleted row cannot answer it.
 */

import { useCallback, useState } from 'react'

import { useDebounced } from '../../lib/useDebounced'
import { useLoader } from '../../lib/useLoader'

import { Badge, Button, Card, EmptyState, ErrorState, Spinner } from '../../components/ui'
import { DOCUMENT_KINDS, documentKindLabel } from '../../lib/contentKinds'
import {
  createGrant,
  explainAccess,
  fetchAdminUsers,
  fetchGrants,
  fetchStudentAccess,
  QueryError,
  revokeGrant,
  type AdminUser,
  type StudentAccess,
} from '../../lib/queries'

const WHO_SCOPES = [
  { value: 'USER', label: 'One student' },
  { value: 'ROLE', label: 'Everyone with a role' },
  { value: 'TIER', label: 'Everyone on a subscription tier' },
  { value: 'PLAN', label: 'Everyone who bought a plan' },
] as const

const ROLES = ['STUDENT', 'EDITOR', 'CONTENT_MANAGER', 'MODERATOR', 'ADMIN']
const TIERS = ['FREE', 'PREMIUM', 'PREMIUM_PLUS']
const PLANS = ['PREMIUM_YEARLY', 'PREMIUM_PLUS_YEARLY']

export default function AdminAccess() {
  const [includeRevoked, setIncludeRevoked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<QueryError | null>(null)

  const state = useLoader((signal) => fetchGrants({ includeRevoked }, signal), [includeRevoked])
  const grants = state.data?.data ?? []
  const total = state.data?.meta.total ?? 0
  const error = actionError ?? state.error
  const reload = state.reload

  // The form.
  const [whoScope, setWhoScope] = useState<string>('USER')
  const [effect, setEffect] = useState<'ALLOW' | 'DENY'>('ALLOW')
  const [userQuery, setUserQuery] = useState('')
  const [selectedUser, setSelectedUser] = useState<AdminUser | null>(null)
  const [role, setRole] = useState('STUDENT')
  const [tier, setTier] = useState('PREMIUM')
  const [planCode, setPlanCode] = useState('PREMIUM_YEARLY')
  const [kind, setKind] = useState('')
  const [reason, setReason] = useState('')
  const [expiresAt, setExpiresAt] = useState('')

  // The explain panel.
  const [explainUserId, setExplainUserId] = useState('')
  const [explainDocumentId, setExplainDocumentId] = useState('')
  const [explanation, setExplanation] = useState<{ decision: string; reason: string } | null>(null)
  const [studentView, setStudentView] = useState<StudentAccess | null>(null)

  // Student search. The debounce is a value (see useDebounced), so "the box is empty"
  // is a render-time fact rather than a state write inside an effect - and the request
  // still only fires once the typing settles.
  const userTerm = useDebounced(userQuery.trim(), 250)
  const search = useLoader(
    (signal) => fetchAdminUsers({ q: userTerm, limit: 8 }, signal),
    [userTerm],
    userTerm.length >= 2,
  )
  const candidates = userTerm.length >= 2 ? (search.data?.data ?? []) : []

  const submit = useCallback(async () => {
    setBusy(true)
    setActionError(null)
    try {
      await createGrant({
        whoScope,
        effect,
        userId: whoScope === 'USER' ? (selectedUser?.id ?? null) : null,
        role: whoScope === 'ROLE' ? role : null,
        tier: whoScope === 'TIER' ? tier : null,
        planCode: whoScope === 'PLAN' ? planCode : null,
        kind: kind || null,
        reason: reason || null,
        expiresAt: expiresAt ? new Date(expiresAt).toISOString() : null,
      })
      setReason('')
      setExpiresAt('')
      reload()
    } catch (caught: unknown) {
      setActionError(caught instanceof QueryError ? caught : new QueryError(caught))
    } finally {
      setBusy(false)
    }
  }, [whoScope, effect, selectedUser, role, tier, planCode, kind, reason, expiresAt, reload])

  const revoke = useCallback(
    async (grantId: string) => {
      setBusy(true)
      try {
        await revokeGrant(grantId)
        reload()
      } catch (caught: unknown) {
        setActionError(caught instanceof QueryError ? caught : new QueryError(caught))
      } finally {
        setBusy(false)
      }
    },
    [reload],
  )

  const explain = useCallback(async () => {
    setBusy(true)
    setExplanation(null)
    setStudentView(null)
    try {
      const answer = await explainAccess(explainUserId, explainDocumentId)
      setExplanation({ decision: answer.decision, reason: answer.reason })
      setStudentView(await fetchStudentAccess(explainUserId))
    } catch (caught: unknown) {
      setActionError(caught instanceof QueryError ? caught : new QueryError(caught))
    } finally {
      setBusy(false)
    }
  }, [explainUserId, explainDocumentId])

  const canSubmit = whoScope === 'USER' ? Boolean(selectedUser) : true
  const emptyOfWho =
    (whoScope === 'ROLE' && !role) ||
    (whoScope === 'TIER' && !tier) ||
    (whoScope === 'PLAN' && !planCode)

  return (
    <div className="space-y-6">
      <Card>
        <h2 className="text-sm font-semibold tracking-tight text-slate-900">New grant</h2>
        <p className="mt-1 text-sm text-slate-600">
          A grant applies to material by <strong>course, subject or content type</strong> — and to
          everything uploaded into that scope later. Leave all three empty to cover the whole
          library.
        </p>

        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label>
            <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Who
            </span>
            <select
              value={whoScope}
              onChange={(event) => setWhoScope(event.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              {WHO_SCOPES.map((scope) => (
                <option key={scope.value} value={scope.value}>
                  {scope.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Effect
            </span>
            <select
              value={effect}
              onChange={(event) => setEffect(event.target.value as 'ALLOW' | 'DENY')}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="ALLOW">Allow</option>
              <option value="DENY">Deny (beats every allow)</option>
            </select>
          </label>
          <label className="sm:col-span-2">
            <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Content type
            </span>
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">Any kind</option>
              {DOCUMENT_KINDS.map((value) => (
                <option key={value} value={value}>
                  {documentKindLabel(value)}
                </option>
              ))}
            </select>
          </label>
        </div>

        {whoScope === 'USER' && (
          <div className="mt-3">
            {selectedUser ? (
              <div className="flex items-center justify-between gap-3 rounded-md bg-slate-50 px-3 py-2">
                <span className="text-sm text-slate-800">
                  {selectedUser.displayName ?? 'Unnamed'} · {selectedUser.email}
                </span>
                <Button tone="quiet" onClick={() => setSelectedUser(null)}>
                  Change
                </Button>
              </div>
            ) : (
              <>
                <label>
                  <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
                    Find the student
                  </span>
                  <input
                    type="search"
                    value={userQuery}
                    onChange={(event) => setUserQuery(event.target.value)}
                    placeholder="Email or name"
                    className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                  />
                </label>
                {candidates.length > 0 && (
                  <ul className="mt-2 divide-y divide-slate-100 rounded-md border border-slate-200">
                    {candidates.map((candidate) => (
                      <li key={candidate.id}>
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedUser(candidate)
                            setExplainUserId(candidate.id)
                            // Choosing a student empties the box; the result list is
                            // derived from the debounced term, so it clears with it.
                            setUserQuery('')
                          }}
                          className="w-full px-3 py-2 text-left text-sm hover:bg-slate-50"
                        >
                          {candidate.displayName ?? 'Unnamed'} · {candidate.email}
                          <span className="ml-2 text-xs text-slate-500">{candidate.role}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        )}

        {whoScope === 'ROLE' && (
          <Select label="Role" value={role} onChange={setRole} options={ROLES} />
        )}
        {whoScope === 'TIER' && (
          <Select label="Tier" value={tier} onChange={setTier} options={TIERS} />
        )}
        {whoScope === 'PLAN' && (
          <Select label="Plan" value={planCode} onChange={setPlanCode} options={PLANS} />
        )}

        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label>
            <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Reason (shown in the audit log)
            </span>
            <input
              type="text"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="e.g. Scholarship cohort, Sept intake"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label>
            <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Expires (optional)
            </span>
            <input
              type="date"
              value={expiresAt}
              onChange={(event) => setExpiresAt(event.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button onClick={() => void submit()} disabled={busy || !canSubmit || emptyOfWho}>
            {effect === 'ALLOW' ? 'Grant access' : 'Deny access'}
          </Button>
          {effect === 'DENY' && (
            <p className="text-xs text-slate-500">
              A denial outranks any allow, including one created later.
            </p>
          )}
        </div>
      </Card>

      <Card>
        <h2 className="text-sm font-semibold tracking-tight text-slate-900">
          Why can a student read something?
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          Runs the student's own access check on the server, so the answer cannot differ from what
          the student experiences.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <label className="sm:col-span-1">
            <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Student id
            </span>
            <input
              type="text"
              value={explainUserId}
              onChange={(event) => setExplainUserId(event.target.value)}
              placeholder="uuid"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-xs"
            />
          </label>
          <label className="sm:col-span-1">
            <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Document id
            </span>
            <input
              type="text"
              value={explainDocumentId}
              onChange={(event) => setExplainDocumentId(event.target.value)}
              placeholder="uuid"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-xs"
            />
          </label>
          <div className="flex items-end">
            <Button
              tone="secondary"
              onClick={() => void explain()}
              disabled={busy || !explainUserId || !explainDocumentId}
            >
              Explain
            </Button>
          </div>
        </div>

        {explanation && (
          <div className="mt-4 rounded-md bg-slate-50 p-3">
            <Badge tone={explanation.decision === 'GRANTED' ? 'right' : 'amber'}>
              {explanation.decision}
            </Badge>
            <p className="mt-2 text-sm text-slate-700">{explanation.reason}</p>
            {studentView && (
              <p className="mt-2 text-xs text-slate-500">
                {studentView.displayName ?? studentView.email} · tier {studentView.tier} ·{' '}
                {studentView.allowGrants} allow / {studentView.denyGrants} deny grant
                {studentView.allowGrants + studentView.denyGrants === 1 ? '' : 's'}
              </p>
            )}
          </div>
        )}
      </Card>

      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold tracking-tight text-slate-900">
          {total.toLocaleString('en-IN')} grant{total === 1 ? '' : 's'}
        </h2>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={includeRevoked}
            onChange={(event) => setIncludeRevoked(event.target.checked)}
          />
          <span className="text-sm text-slate-600">Include revoked</span>
        </label>
      </div>

      {error && <ErrorState error={error} what="access grants" />}
      {state.loading && <Spinner label="Loading grants…" />}

      {!state.loading && grants.length === 0 && (
        <EmptyState
          title="No grants"
          body="Nothing has been granted or denied beyond the subscription tiers. Create one above to give a student material their tier does not reach."
        />
      )}

      <ul className="space-y-2">
        {grants.map((grant) => (
          <li key={grant.id}>
            <Card>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={grant.effect === 'ALLOW' ? 'right' : 'wrong'}>
                      {grant.effect}
                    </Badge>
                    <span className="text-sm text-slate-800">
                      {grant.whoScope === 'USER'
                        ? `Student ${grant.userId?.slice(0, 8)}`
                        : grant.whoScope === 'ROLE'
                          ? `Role ${grant.role}`
                          : grant.whoScope === 'TIER'
                            ? `Tier ${grant.tier}`
                            : `Plan ${grant.planCode}`}
                    </span>
                    <span className="text-slate-400">→</span>
                    <span className="text-sm text-slate-700">
                      {grant.covers.wholeLibrary
                        ? 'the whole library'
                        : [
                            grant.covers.courseId && `course ${grant.covers.courseId.slice(0, 8)}`,
                            grant.covers.subjectId &&
                              `subject ${grant.covers.subjectId.slice(0, 8)}`,
                            grant.covers.kind &&
                              `all ${grant.covers.kind.replace('_', ' ').toLowerCase()}`,
                          ]
                            .filter(Boolean)
                            .join(' + ')}
                    </span>
                    {!grant.isLive && <Badge tone="slate">Revoked</Badge>}
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    {grant.reason ?? 'No reason recorded'}
                    {grant.expiresAt
                      ? ` · expires ${new Date(grant.expiresAt).toLocaleDateString('en-IN')}`
                      : ''}
                    {grant.createdAt
                      ? ` · created ${new Date(grant.createdAt).toLocaleDateString('en-IN')}`
                      : ''}
                  </p>
                </div>
                {grant.isLive ? (
                  <Button
                    tone="quiet"
                    disabled={busy}
                    title="Revoking keeps the record: who had access, and when it stopped."
                    onClick={() => void revoke(grant.id)}
                  >
                    Revoke
                  </Button>
                ) : (
                  <span className="text-xs text-slate-400">
                    revoked{' '}
                    {grant.revokedAt ? new Date(grant.revokedAt).toLocaleDateString('en-IN') : ''}
                  </span>
                )}
              </div>
            </Card>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: readonly string[]
}) {
  return (
    <label className="mt-3 block sm:max-w-xs">
      <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  )
}
