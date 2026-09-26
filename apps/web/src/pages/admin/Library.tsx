/**
 * The content library: every document, its pipeline state, and what can be done to it.
 *
 * WHAT AN OPERATOR COMES HERE TO DO, IN ORDER OF HOW OFTEN
 *
 *   * find out why a document is not showing up for students (status, published flag,
 *     access tier - all three are columns here, because all three cause that symptom);
 *   * read the extracted text to check the pipeline did its job;
 *   * preview or download the ORIGINAL file, which is never modified by extraction;
 *   * reprocess after a failure, archive something that should not be public, or
 *     restore something archived by mistake.
 *
 * WHAT IT WILL NOT DO
 *
 * There is no delete. Questions generated from a document reference its row, and the file
 * is the evidence a question was reviewed against, so an endpoint that removed either
 * would break the traceability the library exists for. Archive is the strongest action
 * here and it is reversible, which is stated on the button rather than discovered.
 *
 * THE FAILURE COLUMN IS THE POINT
 *
 * A failed ingestion leaves `error` on the row. Showing it inline, rather than behind a
 * click, is the difference between "the pipeline is broken" being noticed in a minute and
 * being noticed when a student complains.
 */

import { useCallback, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { Badge, Button, Card, EmptyState, ErrorState, Spinner } from '../../components/ui'
import {
  archiveDocument,
  fetchAdminDocuments,
  fetchDocumentText,
  QueryError,
  reprocessDocument,
  restoreDocument,
} from '../../lib/queries'
import { useLoader } from '../../lib/useLoader'

const STATUS_TONE: Record<string, 'slate' | 'brand' | 'right' | 'wrong' | 'amber'> = {
  UPLOADED: 'slate',
  QUEUED: 'amber',
  PROCESSING: 'amber',
  EXTRACTING: 'amber',
  COMPLETED: 'right',
  INDEXED: 'right',
  FAILED: 'wrong',
  ARCHIVED: 'slate',
}

export default function AdminLibrary() {
  const [params, setParams] = useSearchParams()
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<QueryError | null>(null)
  const [openText, setOpenText] = useState<{
    id: string
    text: string | null
    error: string | null
  } | null>(null)

  const query = params.get('q') ?? ''
  const status = params.get('status') ?? ''
  const includeArchived = params.get('archived') === 'true'
  const page = Number(params.get('page') ?? '1')

  // The loader owns fetching, aborting and the loading flag; `reload` re-reads the same
  // list after a write. Rolling this by hand is what the hooks lint rule (correctly)
  // refuses, and it is where "state set after unmount" comes from.
  const state = useLoader(
    (signal) =>
      fetchAdminDocuments(
        // Spread rather than `q: query || undefined`: with exactOptionalPropertyTypes an
        // explicit undefined is not an absent key, and the filter type is `q?: string`.
        { includeArchived, page, ...(query ? { q: query } : {}), ...(status ? { status } : {}) },
        signal,
      ),
    [query, status, includeArchived, page],
  )
  const documents = state.data?.data ?? []
  const total = state.data?.meta.total ?? 0
  const error = actionError ?? state.error

  const act = useCallback(
    async (id: string, action: 'archive' | 'restore' | 'reprocess') => {
      setBusy(id)
      setActionError(null)
      try {
        if (action === 'archive') await archiveDocument(id)
        if (action === 'restore') await restoreDocument(id)
        if (action === 'reprocess') await reprocessDocument(id)
        state.reload()
      } catch (caught: unknown) {
        setActionError(caught instanceof QueryError ? caught : new QueryError(caught))
      } finally {
        setBusy(null)
      }
    },
    [state],
  )

  const showText = useCallback(async (id: string) => {
    setOpenText({ id, text: null, error: null })
    try {
      const body = await fetchDocumentText(id)
      setOpenText({
        id,
        text: body.pages
          .map((p) => `— page ${p.pageNumber} —\n${p.text}`)
          .join('\n\n')
          .slice(0, 20000),
        error: null,
      })
    } catch (caught: unknown) {
      const queryError = caught instanceof QueryError ? caught : new QueryError(caught)
      setOpenText({ id, text: null, error: queryError.message })
    }
  }, [])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex-1 min-w-[220px]">
          <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
            Search titles, filenames and the text inside the documents
          </span>
          <input
            type="search"
            defaultValue={query}
            onChange={(event) => {
              const next = new URLSearchParams(params)
              if (event.target.value) next.set('q', event.target.value)
              else next.delete('q')
              next.delete('page')
              setParams(next, { replace: true })
            }}
            placeholder="e.g. Financial Reporting Nov 2024"
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
        </label>
        <label>
          <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
            Status
          </span>
          <select
            value={status}
            onChange={(event) => {
              const next = new URLSearchParams(params)
              if (event.target.value) next.set('status', event.target.value)
              else next.delete('status')
              next.delete('page')
              setParams(next, { replace: true })
            }}
            className="mt-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
          >
            <option value="">Any</option>
            {['UPLOADED', 'QUEUED', 'PROCESSING', 'COMPLETED', 'INDEXED', 'FAILED', 'ARCHIVED'].map(
              (value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ),
            )}
          </select>
        </label>
        <label className="flex items-center gap-2 pb-2">
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(event) => {
              const next = new URLSearchParams(params)
              if (event.target.checked) next.set('archived', 'true')
              else next.delete('archived')
              next.delete('page')
              setParams(next, { replace: true })
            }}
          />
          <span className="text-sm text-slate-600">Include archived</span>
        </label>
        <div className="pb-2">
          <Link
            to="/admin/uploads"
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white"
          >
            Upload PDFs
          </Link>
        </div>
      </div>

      {error && <ErrorState error={error} what="the content library" />}
      {state.loading && <Spinner label="Loading documents…" />}

      {!state.loading && documents.length === 0 && !error && (
        <EmptyState
          title="No documents match"
          body={
            includeArchived
              ? 'Nothing here yet. Upload a PDF to start the pipeline.'
              : 'Nothing active matches. An archived document is hidden unless you include it.'
          }
        />
      )}

      {documents.length > 0 && (
        <>
          <p className="text-sm text-slate-600">
            {total.toLocaleString('en-IN')} document{total === 1 ? '' : 's'}
            {status ? ` with status ${status}` : ''}.
          </p>
          <ul className="space-y-3">
            {documents.map((document) => (
              <li key={document.id}>
                <Card>
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="truncate text-sm font-semibold tracking-tight text-slate-900">
                          {document.title}
                        </h3>
                        <Badge tone={STATUS_TONE[document.status] ?? 'slate'}>
                          {document.status}
                        </Badge>
                        {document.isPublished ? (
                          <Badge tone="right">Published</Badge>
                        ) : (
                          <Badge tone="slate">Not published</Badge>
                        )}
                        <Badge tone="slate">{document.accessTier}</Badge>
                        {document.deletedAt && <Badge tone="amber">Archived</Badge>}
                        {/*
                          The search reads the extracted TEXT, so a result whose title
                          looks unrelated is correct - and without this badge it reads as
                          a bug in the filter. The page number makes the hit checkable.
                        */}
                        {document.matchSource === 'TEXT' && (
                          <Badge tone="brand">
                            Found in text
                            {document.matchedPage ? ` · page ${document.matchedPage}` : ''}
                          </Badge>
                        )}
                      </div>
                      <p className="mt-1 text-xs text-slate-500">
                        {document.originalFilename} ·{' '}
                        {(document.sizeBytes / 1024 / 1024).toFixed(1)} MB
                        {document.pageCount ? ` · ${document.pageCount} pages` : ''}
                        {' · '}
                        {new Date(document.createdAt ?? '').toLocaleDateString('en-IN')}
                      </p>
                      {document.error && (
                        <p className="mt-2 rounded-md bg-wrong-50 px-3 py-2 text-xs text-wrong-600">
                          {document.error}
                        </p>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button tone="quiet" onClick={() => void showText(document.id)}>
                        Extracted text
                      </Button>
                      {document.deletedAt ? (
                        <Button
                          tone="secondary"
                          disabled={busy === document.id}
                          onClick={() => void act(document.id, 'restore')}
                        >
                          Restore
                        </Button>
                      ) : (
                        <>
                          {document.status === 'FAILED' && (
                            <Button
                              tone="secondary"
                              disabled={busy === document.id}
                              onClick={() => void act(document.id, 'reprocess')}
                            >
                              Reprocess
                            </Button>
                          )}
                          <Button
                            tone="secondary"
                            disabled={busy === document.id}
                            // Says what it does, not what it deletes: archive keeps the
                            // row and the file, so labelling it "Delete" would be wrong
                            // in the direction that matters.
                            title="Hidden from students. Still visible here, and reversible."
                            onClick={() => void act(document.id, 'archive')}
                          >
                            Archive
                          </Button>
                        </>
                      )}
                    </div>
                  </div>
                </Card>
              </li>
            ))}
          </ul>

          {total > documents.length && (
            <nav aria-label="Pagination" className="flex items-center justify-between">
              <Button
                tone="quiet"
                disabled={page <= 1}
                onClick={() => {
                  const next = new URLSearchParams(params)
                  next.set('page', String(page - 1))
                  setParams(next)
                }}
              >
                Previous
              </Button>
              <span className="text-sm text-slate-500">
                Page {page} of {Math.max(1, Math.ceil(total / documents.length))}
              </span>
              <Button
                tone="quiet"
                onClick={() => {
                  const next = new URLSearchParams(params)
                  next.set('page', String(page + 1))
                  setParams(next)
                }}
              >
                Next
              </Button>
            </nav>
          )}
        </>
      )}

      {openText && (
        <Card>
          <div className="flex items-start justify-between gap-3">
            <h3 className="text-sm font-semibold tracking-tight text-slate-900">Extracted text</h3>
            <Button tone="quiet" onClick={() => setOpenText(null)}>
              Close
            </Button>
          </div>
          {openText.error ? (
            <p className="mt-2 text-sm text-wrong-600">{openText.error}</p>
          ) : openText.text ? (
            <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-xs leading-relaxed text-slate-700">
              {openText.text}
            </pre>
          ) : (
            <Spinner label="Reading pages…" />
          )}
        </Card>
      )}
    </div>
  )
}
