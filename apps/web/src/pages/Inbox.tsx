import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge, Button, EmptyState, ErrorState, Spinner } from '../components/ui'
import {
  fetchInbox,
  markInboxAllRead,
  markInboxRead,
  safeInboxPath,
  type InboxItem,
} from '../lib/inbox'
import { QueryError } from '../lib/queries'
import { useLoader } from '../lib/useLoader'

/**
 * In-app notifications.
 *
 * Email is not sent. The worker that would deliver out of band is still a stub, so
 * this page is the only place an announcement arrives, and the empty state says so
 * rather than implying a message was also emailed.
 *
 * Marking read is a write against the student's own rows. The screen re-reads after
 * it, instead of flipping a local flag: the unread count comes back in the list
 * envelope, and a local decrement can disagree with a second device.
 */

const KIND_LABEL: Record<string, string> = {
  SYSTEM: 'System',
  ANNOUNCEMENT: 'Announcement',
  CONTENT: 'Content',
  PAYMENT: 'Payment',
  ACHIEVEMENT: 'Achievement',
}

export default function InboxPage() {
  const [page, setPage] = useState(1)
  const [actionError, setActionError] = useState<QueryError | null>(null)
  const [busy, setBusy] = useState(false)
  const inbox = useLoader((signal) => fetchInbox(page, signal), [page])

  async function markOne(item: InboxItem) {
    if (item.read) return
    setBusy(true)
    setActionError(null)
    try {
      await markInboxRead(item.id)
      inbox.reload()
    } catch (error: unknown) {
      setActionError(error instanceof QueryError ? error : new QueryError(error))
    } finally {
      setBusy(false)
    }
  }

  async function markAll() {
    setBusy(true)
    setActionError(null)
    try {
      await markInboxAllRead()
      inbox.reload()
    } catch (error: unknown) {
      setActionError(error instanceof QueryError ? error : new QueryError(error))
    } finally {
      setBusy(false)
    }
  }

  const items = inbox.data?.items ?? []

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Inbox</h1>
          <p className="mt-1 max-w-2xl text-slate-600">
            Announcements and account notices. These stay in the app — nothing here is emailed.
          </p>
        </div>
        {inbox.data && inbox.data.unread > 0 && (
          <Button tone="secondary" disabled={busy} onClick={() => void markAll()}>
            Mark all read
          </Button>
        )}
      </div>

      {inbox.loading && <Spinner label="Loading your inbox…" />}
      {inbox.error && <ErrorState error={inbox.error} what="your inbox" />}
      {actionError && <ErrorState error={actionError} what="that update" />}

      {!inbox.loading && !inbox.error && items.length === 0 && (
        <EmptyState
          title="Nothing here yet"
          body="When the platform announces something, or a payment or achievement needs your attention, it lands here. It is not emailed."
        />
      )}

      {items.length > 0 && (
        <ul className="space-y-2">
          {items.map((item) => (
            <InboxRow key={item.id} item={item} busy={busy} onRead={() => void markOne(item)} />
          ))}
        </ul>
      )}

      {inbox.data && inbox.data.total > 30 && (
        <div className="flex items-center justify-between text-sm text-slate-600">
          <span>
            {inbox.data.unread} unread of {inbox.data.total}
          </span>
          <div className="flex gap-2">
            <Button
              tone="secondary"
              disabled={page <= 1}
              onClick={() => setPage((current) => current - 1)}
            >
              Previous
            </Button>
            <Button
              tone="secondary"
              disabled={!inbox.data.hasMore}
              onClick={() => setPage((current) => current + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

function InboxRow({ item, busy, onRead }: { item: InboxItem; busy: boolean; onRead: () => void }) {
  const path = safeInboxPath(item.linkUrl)
  return (
    <li
      className={`rounded-lg border bg-white px-4 py-3 ${
        item.read ? 'border-slate-200' : 'border-brand-200 bg-brand-50/40'
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={item.read ? 'slate' : 'brand'}>{KIND_LABEL[item.kind] ?? item.kind}</Badge>
        {!item.read && <span className="text-xs font-medium text-brand-700">Unread</span>}
      </div>
      <h2 className="mt-1 text-base font-medium text-slate-900">{item.title}</h2>
      {item.body && <p className="mt-1 text-sm whitespace-pre-wrap text-slate-700">{item.body}</p>}
      {item.linkUrl && !path && (
        <p className="mt-2 text-xs text-slate-500">
          This notice included a link that leaves the app, so it is not shown.
        </p>
      )}
      <div className="mt-2 flex flex-wrap gap-3 text-sm">
        {path && (
          <Link to={path} className="font-medium text-brand-600 hover:underline" onClick={onRead}>
            Open
          </Link>
        )}
        {!item.read && (
          <button
            type="button"
            disabled={busy}
            onClick={onRead}
            className="font-medium text-slate-600 hover:underline disabled:text-slate-400"
          >
            Mark read
          </button>
        )}
      </div>
    </li>
  )
}
