import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { Badge, Button, Card, EmptyState, ErrorState, SectionHeading, Spinner } from '../components/ui'
import { useLoader } from '../lib/useLoader'
import {
  acceptReply,
  createDoubt,
  fetchDoubtThread,
  fetchDoubts,
  replyToDoubt,
  type Doubt,
  type DoubtThread,
} from '../lib/queries'
import { QueryError } from '../lib/queries'

/**
 * Doubts: ask, answer, and mark the answer that resolved it.
 *
 * WHY A THREAD RATHER THAN A QUESTION-AND-ANSWER PAIR. The message that actually
 * resolves a doubt is rarely the first reply - it is "I still don't follow step 3",
 * followed by a second explanation. A single answer field cannot represent that, so
 * a doubt is a thread, and the student marks which reply resolved it. That mark is
 * also the only signal in the product that says whether support is working.
 */
export default function DoubtsPage() {
  const { doubtId } = useParams<{ doubtId: string }>()
  if (doubtId) return <Thread doubtId={doubtId} />
  return <DoubtList />
}

const STATUS_TONE: Record<string, 'brand' | 'right' | 'slate'> = {
  OPEN: 'brand',
  ANSWERED: 'right',
  RESOLVED: 'right',
  CLOSED: 'slate',
}

function DoubtList() {
  const doubts = useLoader((signal) => fetchDoubts(1, 20, signal), [])
  const [asking, setAsking] = useState(false)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<QueryError | null>(null)
  const [created, setCreated] = useState<Doubt | null>(null)

  async function submit() {
    if (!title.trim() || !body.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      setCreated(await createDoubt({ title: title.trim(), body: body.trim() }))
      setTitle('')
      setBody('')
      setAsking(false)
    } catch (err: unknown) {
      setError(err as QueryError)
    } finally {
      setBusy(false)
    }
  }

  const rows = doubts.data?.data ?? []

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Doubts</h1>
          <p className="mt-1 text-slate-600">
            Ask about anything you are stuck on. Attach the chapter or the question, and an editor
            picks it up.
          </p>
        </div>
        <Button onClick={() => setAsking((value) => !value)}>
          {asking ? 'Cancel' : 'Ask a doubt'}
        </Button>
      </div>

      {created && (
        <div role="status" className="rounded-md border border-right-500/30 bg-right-50 p-3 text-sm">
          Asked. <Link to={`/doubts/${created.id}`} className="font-medium underline">Open the thread</Link>
        </div>
      )}

      {asking && (
        <Card>
          <SectionHeading title="Ask a doubt" />
          <label className="block text-sm">
            <span className="font-medium text-slate-700">What are you stuck on?</span>
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={200}
              placeholder="e.g. How is depreciation treated in the final accounts?"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="mt-3 block text-sm">
            <span className="font-medium text-slate-700">Say more</span>
            <textarea
              value={body}
              onChange={(event) => setBody(event.target.value)}
              rows={4}
              placeholder="What have you tried, and where does it break down? A question that shows your working gets a much better answer."
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          {error && (
            <div className="mt-3">
              <ErrorState error={error} what="your doubt" />
            </div>
          )}
          <div className="mt-4">
            <Button disabled={busy || !title.trim() || !body.trim()} onClick={() => void submit()}>
              {busy ? 'Sending…' : 'Submit doubt'}
            </Button>
          </div>
        </Card>
      )}

      {doubts.error && <ErrorState error={doubts.error} what="your doubts" />}
      {doubts.loading && <Spinner />}

      {!doubts.loading && rows.length === 0 && (
        <EmptyState
          title="No doubts yet"
          body="When a question does not make sense, ask it here. It reaches a human editor, not a queue nobody reads."
        />
      )}

      {rows.length > 0 && (
        <ul className="space-y-2">
          {rows.map((doubt) => (
            <li key={doubt.id}>
              <Link to={`/doubts/${doubt.id}`} className="block">
                <Card className="transition-colors hover:border-brand-500">
                  <div className="flex items-start justify-between gap-3">
                    <span className="min-w-0">
                      <span className="block truncate font-medium text-slate-900">
                        {doubt.title}
                      </span>
                      <span className="mt-0.5 block truncate text-sm text-slate-600">
                        {doubt.body}
                      </span>
                      <span className="mt-1 block text-xs text-slate-500">
                        {doubt.replyCount} repl{doubt.replyCount === 1 ? 'y' : 'ies'} ·{' '}
                        {new Date(doubt.lastActivityAt).toLocaleDateString()}
                      </span>
                    </span>
                    <Badge tone={STATUS_TONE[doubt.status] ?? 'slate'}>
                      {doubt.status.toLowerCase()}
                    </Badge>
                  </div>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Thread({ doubtId }: { doubtId: string }) {
  const [thread, setThread] = useState<DoubtThread | null>(null)
  const [error, setError] = useState<QueryError | null>(null)
  const [loading, setLoading] = useState(true)
  const [reply, setReply] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<QueryError | null>(null)

  function reload() {
    fetchDoubtThread(doubtId)
      .then(setThread)
      .catch((err: unknown) => setError(err as QueryError))
      .finally(() => setLoading(false))
  }

  // Loaded imperatively rather than through useLoader so a reply can refresh the
  // thread in place. A full refetch is deliberate: the reply count, the status and
  // the accepted-reply marker all change together, and stitching those by hand in
  // the client is how a thread ends up disagreeing with the server.
  useLoader(
    () =>
      fetchDoubtThread(doubtId)
        .then((data) => {
          setThread(data)
          setLoading(false)
          return data
        })
        .catch((err: unknown) => {
          setError(err as QueryError)
          setLoading(false)
          throw err
        }),
    [doubtId],
  )

  async function send() {
    if (!reply.trim() || busy) return
    setBusy(true)
    setActionError(null)
    try {
      await replyToDoubt(doubtId, reply.trim())
      setReply('')
      reload()
    } catch (err: unknown) {
      setActionError(err as QueryError)
    } finally {
      setBusy(false)
    }
  }

  async function accept(replyId: string) {
    setActionError(null)
    try {
      await acceptReply(doubtId, replyId)
      reload()
    } catch (err: unknown) {
      setActionError(err as QueryError)
    }
  }

  if (loading) return <Spinner label="Loading the thread…" />
  if (error) return <ErrorState error={error} what="this doubt" />
  if (!thread) return null

  return (
    <div className="space-y-5">
      <Link to="/doubts" className="text-sm font-medium text-brand-600 hover:underline">
        ← All doubts
      </Link>

      <Card>
        <div className="flex items-start justify-between gap-3">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">{thread.title}</h1>
          <Badge tone={STATUS_TONE[thread.status] ?? 'slate'}>
            {thread.status.toLowerCase()}
          </Badge>
        </div>
        <p className="mt-3 whitespace-pre-wrap text-slate-700">{thread.body}</p>
        <p className="mt-3 text-xs text-slate-500">
          Asked {new Date(thread.createdAt).toLocaleString()}
        </p>
        {thread.resolutionNote && (
          <p className="mt-3 rounded-md bg-right-50 p-3 text-sm text-slate-700">
            {thread.resolutionNote}
          </p>
        )}
      </Card>

      <SectionHeading
        title={`${thread.replies.length} repl${thread.replies.length === 1 ? 'y' : 'ies'}`}
      />

      {thread.replies.length === 0 && (
        <p className="text-sm text-slate-500">
          No replies yet. An editor sees this in their queue; adding what you already tried usually
          gets a faster, better answer.
        </p>
      )}

      <ul className="space-y-3">
        {thread.replies.map((item) => (
          <li key={item.id}>
            <Card
              className={item.isAccepted ? 'border-right-500 bg-right-50' : undefined}
            >
              <div className="flex items-start justify-between gap-3">
                <p className="whitespace-pre-wrap text-slate-700">
                  <span className="mr-2 text-xs font-medium uppercase tracking-wide text-slate-500">
                    {item.authorRole?.toLowerCase() ?? 'you'}
                  </span>
                  {item.body}
                </p>
                {item.isAccepted ? (
                  <Badge tone="right">accepted</Badge>
                ) : (
                  <Button tone="quiet" onClick={() => void accept(item.id)}>
                    Accept
                  </Button>
                )}
              </div>
              <p className="mt-2 text-xs text-slate-500">
                {new Date(item.createdAt).toLocaleString()}
              </p>
            </Card>
          </li>
        ))}
      </ul>

      {actionError && <ErrorState error={actionError} what="that action" />}

      <Card>
        <label className="block text-sm">
          <span className="font-medium text-slate-700">
            {thread.status === 'RESOLVED' ? 'Add a note' : 'Reply'}
          </span>
          <textarea
            value={reply}
            onChange={(event) => setReply(event.target.value)}
            rows={3}
            placeholder="Add what you tried, or answer if you know it."
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
          />
        </label>
        <div className="mt-3">
          <Button disabled={busy || !reply.trim()} onClick={() => void send()}>
            {busy ? 'Sending…' : 'Post reply'}
          </Button>
        </div>
      </Card>
    </div>
  )
}
