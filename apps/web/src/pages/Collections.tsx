import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge, Button, Card, EmptyState, ErrorState, SectionHeading, Spinner } from '../components/ui'
import { QueryError } from '../lib/queries'
import {
  createCollection,
  deleteCollection,
  fetchCollection,
  fetchCollections,
  removeQuestionFromCollection,
  renameCollection,
  type Collection,
  type CollectionDetail,
} from '../lib/queries'
import { useLoader } from '../lib/useLoader'

/**
 * Collections: named groups of questions a student builds and keeps.
 *
 * WHAT THIS IS NOT. It is not the LDR list. "Marked for later" is a single flag on
 * the progress row, set at the moment a question is answered, and it has its own
 * screen (`/ldr`). A collection is a container the student names - "before mock 3",
 * "weak in Ind AS 116" - that questions can be added to whether or not they have
 * been attempted yet. They answer different questions, so they are different
 * screens, and the one thing this page must never do is imply that flagging a
 * question and adding it to a collection are the same act.
 *
 * WHY THE COUNTS COME FROM THE SERVER. `questionCount` is an aggregate over the
 * membership rows, not a number this screen increments locally. A list that counts
 * optimistically drifts, and a student who sees "8 questions" on a list that opens
 * with 6 learns not to trust the number.
 */

export default function CollectionsPage() {
  // Bumped after any write to force the loader to refetch. `useLoader` keys on its
  // dependency list, so an integer is enough - and it keeps the refetch in one
  // place rather than scattered through the mutation handlers.
  const [version, setVersion] = useState(0)
  const reload = useCallback(() => setVersion((value) => value + 1), [])

  const list = useLoader((signal) => fetchCollections(signal), [version])
  const [openId, setOpenId] = useState<string | null>(null)
  const detail = useLoader(
    (signal) => fetchCollection(openId ?? '', signal),
    [openId, version],
    openId !== null,
  )

  const collections = list.data?.data ?? []

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Collections</h1>
          <p className="mt-1 max-w-2xl text-slate-600">
            Keep questions together for a reason of your own — a chapter you keep getting wrong, the
            ones a mentor set, the ones you want in the last week. Questions you have flagged while
            practising are in{' '}
            <Link to="/ldr" className="font-medium text-brand-600 hover:underline">
              Marked for later
            </Link>
            .
          </p>
        </div>
        <CreateCollectionForm onCreated={reload} />
      </div>

      {list.error && <ErrorState error={list.error} what="your collections" />}
      {list.loading && <Spinner label="Loading your collections…" />}

      {list.data && collections.length === 0 && (
        <EmptyState
          title="No collections yet"
          body="A collection is a place to put questions you will want again — one per chapter you struggle with is a good way to start."
        />
      )}

      {collections.length > 0 && (
        <ul className="grid gap-3 sm:grid-cols-2">
          {collections.map((collection) => (
            <li key={collection.id}>
              <CollectionCard
                collection={collection}
                open={openId === collection.id}
                onOpen={() => setOpenId(openId === collection.id ? null : collection.id)}
                onChanged={reload}
              />
            </li>
          ))}
        </ul>
      )}

      {openId && detail.data && <CollectionDetailPanel detail={detail.data} onChanged={reload} />}
      {openId && detail.error && <ErrorState error={detail.error} what="that collection" />}
      {openId && detail.loading && <Spinner label="Opening the collection…" />}
    </div>
  )
}

function CollectionCard({
  collection,
  open,
  onOpen,
  onChanged,
}: {
  collection: Collection
  open: boolean
  onOpen: () => void
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<QueryError | null>(null)
  const [confirming, setConfirming] = useState(false)

  async function remove() {
    setBusy(true)
    setError(null)
    try {
      await deleteCollection(collection.id)
      setConfirming(false)
      onChanged()
    } catch (err: unknown) {
      setError(err as QueryError)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate font-medium text-slate-900">{collection.name}</h2>
          <p className="mt-1 text-sm text-slate-500">
            {collection.questionCount} {collection.questionCount === 1 ? 'question' : 'questions'}
            {collection.isSystem && ' · built by CA Prep'}
            {collection.kind === 'SMART' && ' · smart'}
          </p>
        </div>
        <Badge tone={collection.kind === 'SMART' ? 'brand' : 'slate'}>{collection.kind}</Badge>
      </div>

      {collection.description && (
        <p className="mt-2 text-sm text-slate-600">{collection.description}</p>
      )}

      {error && (
        <p role="alert" className="mt-2 text-sm text-wrong-500">
          {error.message}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button tone="secondary" onClick={onOpen}>
          {open ? 'Close' : 'Open'}
        </Button>
        {
          /**
           * A system collection is the product's, not the student's: the LDR list is
           * referenced by the practice loop, so deleting or renaming it would break a
           * feature to satisfy a whim. The button is absent rather than disabled,
           * because a disabled delete invites a bug report.
           */
          !collection.isSystem && !confirming && (
            <Button tone="quiet" onClick={() => setConfirming(true)}>
              Delete
            </Button>
          )
        }
        {confirming && (
          <>
            <span className="text-sm text-slate-600">Delete this collection?</span>
            <Button
              tone="secondary"
              className="border-wrong-500/40 text-wrong-500 hover:bg-wrong-50"
              onClick={() => void remove()}
              disabled={busy}
            >
              {busy ? 'Deleting…' : 'Yes, delete'}
            </Button>
            <Button tone="quiet" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </>
        )}
      </div>
    </Card>
  )
}

function CreateCollectionForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<QueryError | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!name.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      await createCollection({ name: name.trim() })
      setName('')
      onCreated()
    } catch (err: unknown) {
      setError(err as QueryError)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="w-full sm:w-80">
      <label htmlFor="new-collection" className="text-sm font-medium text-slate-700">
        New collection
      </label>
      <div className="mt-1 flex gap-2">
        <input
          id="new-collection"
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={120}
          placeholder="Weak in Ind AS 116"
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none"
        />
        <Button type="submit" disabled={busy || !name.trim()}>
          {busy ? 'Creating…' : 'Create'}
        </Button>
      </div>
      {error && (
        <p role="alert" className="mt-1 text-sm text-wrong-500">
          {/* A duplicate name is the one failure worth explaining: the server refuses
              it (uq_collection_user_name) because two collections with one name is
              exactly the state this feature exists to prevent. */}
          {error.message}
        </p>
      )}
    </form>
  )
}

function CollectionDetailPanel({
  detail,
  onChanged,
}: {
  detail: CollectionDetail
  onChanged: () => void
}) {
  const [busyId, setBusyId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<QueryError | null>(null)
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(detail.name)

  async function removeQuestion(questionId: string) {
    setBusyId(questionId)
    setError(null)
    try {
      await removeQuestionFromCollection(detail.id, questionId)
      onChanged()
    } catch (err: unknown) {
      setError(err as QueryError)
    } finally {
      setBusyId(null)
    }
  }

  async function submitRename(event: React.FormEvent) {
    event.preventDefault()
    if (!name.trim() || saving) return
    setSaving(true)
    setError(null)
    try {
      await renameCollection(detail.id, { name: name.trim() })
      setRenaming(false)
      onChanged()
    } catch (err: unknown) {
      setError(err as QueryError)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section aria-label={`Collection ${detail.name}`}>
      <SectionHeading
        title={detail.name}
        subtitle={
          detail.kind === 'SMART'
            ? 'A smart collection resolves its questions from a filter.'
            : `${detail.total} ${detail.total === 1 ? 'question' : 'questions'} kept here.`
        }
        action={
          !detail.isSystem && !renaming ? (
            <Button tone="quiet" onClick={() => setRenaming(true)}>
              Rename
            </Button>
          ) : null
        }
      />

      {renaming && (
        <form onSubmit={(event) => void submitRename(event)} className="mb-3 flex gap-2">
          <label htmlFor="rename-collection" className="sr-only">
            Collection name
          </label>
          <input
            id="rename-collection"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={120}
            className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <Button type="submit" disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
          <Button
            tone="quiet"
            onClick={() => {
              setName(detail.name)
              setRenaming(false)
            }}
          >
            Cancel
          </Button>
        </form>
      )}

      {error && (
        <p role="alert" className="mb-2 text-sm text-wrong-500">
          {error.message}
        </p>
      )}

      {detail.questions.length === 0 ? (
        <EmptyState
          title="Nothing in here yet"
          body="Open a question from practice or search and add it to this collection. Questions you have not attempted yet can be added — that is the point of a reading list."
        />
      ) : (
        <ul className="space-y-2">
          {detail.questions.map((question) => (
            <li key={question.questionId}>
              <Card>
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm text-slate-900">{question.text}</p>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge tone="slate">{question.difficulty}</Badge>
                    {question.isHistorical && <Badge tone="amber">Historical</Badge>}
                  </div>
                </div>

                {question.note && (
                  <p className="mt-1 text-sm italic text-slate-500">{question.note}</p>
                )}

                {/* The disclaimer travels with the question into this list too: a
                    collection is where revision happens, and revising a repealed
                    provision without noticing is the failure it prevents. */}
                {question.disclaimer && (
                  <p className="mt-1 text-xs text-amber-700">{question.disclaimer}</p>
                )}

                <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
                  <Link
                    to={`/search?q=${encodeURIComponent(question.text.slice(0, 60))}`}
                    className="font-medium text-brand-600 hover:underline"
                  >
                    Practise this question
                  </Link>
                  <span className="text-slate-400">·</span>
                  <span className="text-slate-500">{question.marks} marks</span>
                  <span className="text-slate-400">·</span>
                  <Button
                    tone="quiet"
                    onClick={() => void removeQuestion(question.questionId)}
                    disabled={busyId === question.questionId}
                  >
                    {busyId === question.questionId ? 'Removing…' : 'Remove'}
                  </Button>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}

      {detail.hasMore && (
        <p className="mt-2 text-sm text-slate-500">
          Showing {detail.count} of {detail.total}. The rest appear as you page through.
        </p>
      )}
    </section>
  )
}
