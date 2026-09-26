import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { Badge, Button, EmptyState, ErrorState, Spinner } from '../components/ui'
import { useAuth } from '../hooks/authContext'
import { documentKindLabel } from '../lib/contentKinds'
import {
  fetchLibraryDocument,
  fetchLibraryFile,
  fetchLibraryPages,
  libraryErrorKind,
  type LibraryDocument,
  type LibraryFileTicket,
} from '../lib/library'
import { QueryError } from '../lib/queries'
import { hasRole } from '../lib/roles'
import { useLoader } from '../lib/useLoader'

/**
 * Read one document.
 *
 * TWO SURFACES, BECAUSE THE FILE AND THE TEXT FAIL DIFFERENTLY.
 *
 * The extracted text is what the pipeline produced and what search indexes. It is
 * served by the API, so it works when object storage does not — which is the
 * ordinary state of a deployment that has not been given a storage credential.
 * The original PDF is a short-lived signed URL minted on demand. It is requested
 * when the student asks for it, not when the page renders: the URL expires (an
 * hour at most), and holding one in state past that point shows the storage
 * host's error instead of ours.
 *
 * ACCESS IS NOT RE-CHECKED HERE. `GET /content/documents/{id}` returns 404 for
 * both "no such document" and "not yours", on purpose — distinguishing them would
 * tell a student that a document they cannot open exists. This page shows one
 * message for a 404 and does not guess which it was.
 *
 * DOWNLOAD. `allowDownload` is the product rule. When it is false this page does
 * not offer a download action. It does not claim the browser cannot save a file
 * it has already rendered: once the bytes are on the device, that is the device's
 * decision, and pretending otherwise would be a lie next to a working viewer.
 */

const READY = new Set(['INDEXED', 'COMPLETED'])

const STATUS_COPY: Record<string, string> = {
  UPLOADED: 'Uploaded, not processed yet',
  QUEUED: 'Waiting to be processed',
  PROCESSING: 'Being processed',
  EXTRACTING: 'Text is being extracted',
  OCR_REQUIRED: 'Some pages still need a closer read',
  INDEXED: 'Ready to read',
  COMPLETED: 'Ready to read',
  FAILED: 'Processing failed',
  ARCHIVED: 'Archived',
}

export default function LibraryReader() {
  const { documentId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const ordinal = Math.max(1, Number(params.get('page') ?? '1') || 1)

  const document = useLoader(
    (signal) => fetchLibraryDocument(documentId, signal),
    [documentId],
    Boolean(documentId),
  )
  const pages = useLoader(
    (signal) => fetchLibraryPages(documentId, ordinal, 1, signal),
    [documentId, ordinal],
    Boolean(documentId) && document.data !== null,
  )

  if (!documentId) {
    return (
      <EmptyState
        title="No document selected"
        body="Open something from the library."
        action={
          <Link to="/library" className="text-sm font-medium text-brand-600 hover:underline">
            Back to the library
          </Link>
        }
      />
    )
  }

  if (document.loading) return <Spinner label="Opening the document…" />
  if (document.error) return <ReaderError error={document.error} />
  if (!document.data) return null

  const current = pages.data?.pages[0] ?? null
  const total = pages.data?.total ?? document.data.pageCount ?? 0

  function goTo(next: number) {
    const paramsNext = new URLSearchParams(params)
    if (next <= 1) paramsNext.delete('page')
    else paramsNext.set('page', String(next))
    setParams(paramsNext)
  }

  return (
    <div className="space-y-6">
      <div>
        <Link to="/library" className="text-sm font-medium text-brand-600 hover:underline">
          ← Library
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Badge tone="brand">{documentKindLabel(document.data.kind)}</Badge>
          {document.data.accessTier !== 'FREE' && (
            <Badge>{document.data.accessTier.replace('_', ' ')}</Badge>
          )}
          <Badge tone={READY.has(document.data.status) ? 'right' : 'amber'}>
            {STATUS_COPY[document.data.status] ?? document.data.status}
          </Badge>
          {!document.data.allowDownload && <Badge>View only</Badge>}
        </div>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-900">
          {document.data.title}
        </h1>
        {document.data.error && (
          <p className="mt-2 text-sm text-wrong-500" role="alert">
            {document.data.error}
          </p>
        )}
      </div>

      <OriginalFile document={document.data} />

      <section aria-labelledby="extracted-text" className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="extracted-text" className="text-lg font-medium text-slate-900">
              Extracted text
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              The text the search index was built from. It stays readable when the original file is
              not available.
            </p>
          </div>
          {total > 0 && (
            <PageControls ordinal={ordinal} total={total} shown={current?.pageNumber} onGo={goTo} />
          )}
        </div>

        {pages.loading && <Spinner label="Loading the page…" />}
        {pages.error && <ErrorState error={pages.error} what="this page" />}

        {!pages.loading && !pages.error && total === 0 && (
          <EmptyState
            title="No extracted text yet"
            body={
              READY.has(document.data.status)
                ? 'This document finished processing without any text. The original file, if it can be opened above, is the only copy.'
                : 'Text appears here once processing finishes. A document that is still queued has nothing to read yet.'
            }
          />
        )}

        {current && (
          <article className="rounded-lg border border-slate-200 bg-white p-5">
            <div className="mb-3 flex items-center justify-between text-xs text-slate-500">
              <span>Page {current.pageNumber}</span>
              {current.usedOcr && <span>Read by OCR — check it against the original</span>}
            </div>
            {current.text.trim() ? (
              <p className="text-sm leading-relaxed whitespace-pre-wrap text-slate-800">
                {current.text}
              </p>
            ) : (
              <p className="text-sm text-slate-500">This page has no extracted text.</p>
            )}
          </article>
        )}
      </section>
    </div>
  )
}

function ReaderError({ error }: { error: QueryError }) {
  const kind = libraryErrorKind(error)
  if (kind === 'NOT_FOUND') {
    return (
      <EmptyState
        title="Not available"
        body="That document is not available to you. It may not exist, or it may not be part of your plan — the two look the same on purpose, so a catalogue you cannot read is not confirmed by the error."
        action={
          <Link to="/library" className="text-sm font-medium text-brand-600 hover:underline">
            Back to the library
          </Link>
        }
      />
    )
  }
  return <ErrorState error={error} what="this document" />
}

function PageControls({
  ordinal,
  total,
  shown,
  onGo,
}: {
  ordinal: number
  total: number
  shown: number | undefined
  onGo: (page: number) => void
}) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <Button tone="secondary" disabled={ordinal <= 1} onClick={() => onGo(ordinal - 1)}>
        Previous
      </Button>
      {/*
        Keyed on the page the server actually returned. Remounting resets the jump
        box when the page changes, which is the same result as copying the new
        number into state from an effect, without a setState-in-effect.
      */}
      <JumpBox key={shown ?? ordinal} ordinal={ordinal} total={total} shown={shown} onGo={onGo} />
      <Button tone="secondary" disabled={ordinal >= total} onClick={() => onGo(ordinal + 1)}>
        Next
      </Button>
    </div>
  )
}

function JumpBox({
  ordinal,
  total,
  shown,
  onGo,
}: {
  ordinal: number
  total: number
  shown: number | undefined
  onGo: (page: number) => void
}) {
  const [draft, setDraft] = useState(String(shown ?? ordinal))
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        const next = Number(draft)
        if (!Number.isFinite(next)) return
        onGo(Math.min(total, Math.max(1, Math.trunc(next))))
      }}
      className="flex items-center gap-1 text-slate-600"
    >
      <label htmlFor="library-page" className="sr-only">
        Page number
      </label>
      <input
        id="library-page"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        inputMode="numeric"
        className="w-14 rounded-md border border-slate-300 px-2 py-1 text-center"
      />
      <span>of {total}</span>
    </form>
  )
}

/**
 * The original file, fetched only when asked for.
 *
 * Staff may open a file that has not finished processing — the API allows it,
 * because an operator debugging a failed extraction needs the bytes. A student
 * is told the file is not ready rather than shown a 409 they cannot act on.
 */
function OriginalFile({ document }: { document: LibraryDocument }) {
  const { role } = useAuth()
  const ready = READY.has(document.status)
  const staff = hasRole(role, 'EDITOR')
  const [open, setOpen] = useState(false)

  if (!ready && !staff) {
    const statusCopy = STATUS_COPY[document.status]
    return (
      <p className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-3 text-sm text-slate-600">
        The original file is not ready yet
        {statusCopy ? ` (${statusCopy.toLowerCase()})` : ''}. The extracted text below is what is
        available.
      </p>
    )
  }

  return (
    <section aria-labelledby="original-file" className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id="original-file" className="sr-only">
          Original file
        </h2>
        <Button onClick={() => setOpen((value) => !value)}>
          {open ? 'Close original' : 'Open original PDF'}
        </Button>
        {!document.allowDownload && (
          <p className="text-sm text-slate-500">
            Downloading is turned off for this document. You can still read it here.
          </p>
        )}
      </div>
      {open && (
        <FileFrame
          key={document.id}
          documentId={document.id}
          downloadable={document.allowDownload}
        />
      )}
    </section>
  )
}

function FileFrame({ documentId, downloadable }: { documentId: string; downloadable: boolean }) {
  const [ticket, setTicket] = useState<LibraryFileTicket | null>(null)
  const [error, setError] = useState<QueryError | null>(null)

  // Loading is "neither answer has arrived", not a flag set at the top of the
  // effect. Setting it there is a cascading render, and the lint rule is right
  // to refuse it. The frame is keyed on the document id, so a different document
  // remounts rather than needing the effect to clear the previous ticket.
  useEffect(() => {
    const controller = new AbortController()
    fetchLibraryFile(documentId, controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) setTicket(next)
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return
        setError(caught instanceof QueryError ? caught : new QueryError(caught))
      })
    return () => controller.abort()
  }, [documentId])

  if (ticket === null && error === null) return <Spinner label="Preparing the original file…" />
  if (error) return <FileError error={error} />
  if (!ticket) return null

  return (
    <div className="space-y-2">
      {/*
        An iframe, not a PDF library. The URL is cross-origin and short-lived;
        rendering it in the browser's own viewer does not require the storage host
        to send CORS headers, which a client-side PDF parser would. `no-referrer`
        keeps the signed token out of the Referer of anything the viewer then loads.
      */}
      <iframe
        title="Original PDF"
        src={ticket.url}
        referrerPolicy="no-referrer"
        className="h-[70vh] w-full rounded-lg border border-slate-200 bg-white"
      />
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-slate-500">
        <span>
          Link expires in {Math.round(ticket.expiresInSeconds / 60)} minutes. Close and reopen if it
          stops loading.
        </span>
        {downloadable && (
          <a
            href={ticket.url}
            download={ticket.filename}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-brand-600 hover:underline"
          >
            Download {ticket.filename}
          </a>
        )}
      </div>
    </div>
  )
}

function FileError({ error }: { error: QueryError }) {
  const kind = libraryErrorKind(error)
  if (kind === 'UNAVAILABLE') {
    return (
      <EmptyState
        title="Original file unavailable"
        body="This deployment cannot reach file storage, so the original PDF cannot be opened. The extracted text on this page is still readable, and it is the copy search uses."
      />
    )
  }
  if (kind === 'NOT_READY') {
    return (
      <EmptyState
        title="Not ready"
        body="This document has not finished processing, so there is no file to open yet."
      />
    )
  }
  return <ErrorState error={error} what="the original file" />
}
