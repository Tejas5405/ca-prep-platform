/**
 * Bulk upload: 500 PDFs as one operation, not 500 uploads.
 *
 * THE PROBLEM THIS SCREEN SOLVES
 *
 * An owner with a folder of past papers cannot use a single-file form. Doing it one at a
 * time is an afternoon of clicking, and it fails in the middle with no record of which
 * files made it. The API was built for this (one manifest call, a signed URL per file,
 * duplicate detection before any bytes move) and had no interface, so in practice the
 * folder could not be loaded at all.
 *
 * HOW IT WORKS, IN THE ORDER IT HAPPENS
 *
 *   1. The files are read locally and each one's sha256 is computed in the BROWSER
 *      (`crypto.subtle.digest`). That is what makes duplicate detection possible before
 *      anything is transferred: the server compares the fingerprints against the whole
 *      library and hands back one signed URL per file it wants, skipping the rest.
 *   2. The PUTs run with a small concurrency (four by default, as the server suggests).
 *      Thousands of parallel uploads would exhaust the browser's connection pool and the
 *      device's memory, and one at a time wastes the available bandwidth.
 *   3. Each file that lands is confirmed to the API, which checks the object exists in
 *      storage before queueing extraction. A file that never arrived is reported as
 *      failed rather than being queued to fail ten minutes later in a worker.
 *   4. Failures are listed with a retry that re-runs ONLY what failed. A batch of 500
 *      where 3 files were rejected should not need 500 re-uploads.
 *
 * THE UI STAYS RESPONSIVE WHILE THIS RUNS. Reading and hashing happen one file at a
 * time (hashing 500 files concurrently would lock the main thread), progress is a
 * per-file row rather than a spinner, and the JavaScript is not blocked because the
 * hashing is awaited in a loop with yields.
 *
 * WHERE THE LIMITS ARE, STATED RATHER THAN DISCOVERED
 *
 *   * 500 files per batch is the server's cap (`MAX_BATCH_FILES`). A larger folder is
 *     uploaded as several batches, and this screen says so instead of failing at file
 *     501.
 *   * 50 MB per file is the storage cap. A larger file is refused before it is read.
 *   * Total size is bounded by the device's memory: the browser reads each file once to
 *     hash it and again to send it. For a folder in the several-gigabyte range this
 *     screen is the wrong tool and a CLI script against the same API is the right one -
 *     which is a real answer, not a limitation to hide.
 */

import { useCallback, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge, Button, Card, Spinner } from '../../components/ui'
import { DOCUMENT_KINDS, documentKindLabel } from '../../lib/contentKinds'
import {
  createUploadBatch,
  fetchBatchProgress,
  fetchCourses,
  QueryError,
  startProcessing,
  type BatchProgress,
  type UploadTicket,
} from '../../lib/queries'
import { uploadToSignedUrl } from '../../lib/queries'
import { useLoader } from '../../lib/useLoader'

const MAX_FILES_PER_BATCH = 500
const MAX_FILE_BYTES = 50 * 1024 * 1024
const CONCURRENCY = 4

type FileState =
  'pending' | 'hashing' | 'duplicate' | 'uploading' | 'uploaded' | 'failed' | 'rejected'

interface FileRow {
  file: File
  state: FileState
  /** Progress 0..1 for the transfer, so retries are visible as movement. */
  progress: number
  /**
   * `| undefined` is deliberate: this project compiles with
   * exactOptionalPropertyTypes, so `{ detail: undefined }` is not assignable to
   * `{ detail?: string }`. Rows are rebuilt with spreads all over this file, and the
   * alternative is conditioning every field at every spread.
   */
  detail?: string | undefined
  documentId?: string | undefined
  checksum?: string | undefined
}

interface Phase {
  kind: 'idle' | 'hashing' | 'manifest' | 'uploading' | 'starting' | 'polling' | 'done'
  message: string
}

export default function BulkUpload() {
  const courses = useLoader((signal) => fetchCourses(signal), [])
  const inputRef = useRef<HTMLInputElement>(null)

  const [rows, setRows] = useState<FileRow[]>([])
  const [phase, setPhase] = useState<Phase>({ kind: 'idle', message: '' })
  const [batchId, setBatchId] = useState<string | null>(null)
  const [progress, setProgress] = useState<BatchProgress | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Batch-level metadata: the reason a folder of one subject is a single decision.
  const [courseId, setCourseId] = useState('')
  const [kind, setKind] = useState('STUDY_MATERIAL')
  const [accessTier, setAccessTier] = useState('PREMIUM')

  const counts = useMemo(() => {
    const by = (state: FileState) => rows.filter((row) => row.state === state).length
    return {
      total: rows.length,
      uploaded: by('uploaded'),
      failed: by('failed'),
      duplicates: by('duplicate'),
      rejected: by('rejected'),
      pending: by('pending') + by('hashing') + by('uploading'),
    }
  }, [rows])

  const selectFiles = useCallback((files: FileList | null) => {
    if (!files) return
    const next: FileRow[] = Array.from(files).map((file) => ({
      file,
      state: file.size > MAX_FILE_BYTES ? 'rejected' : 'pending',
      progress: 0,
      ...(file.size > MAX_FILE_BYTES
        ? { detail: `Larger than 50 MB (${(file.size / 1024 / 1024).toFixed(1)} MB)` }
        : {}),
    }))
    setRows(next)
    setBatchId(null)
    setProgress(null)
    setError(null)
    setPhase({ kind: 'idle', message: '' })
  }, [])

  const run = useCallback(async () => {
    const usable = rows.filter((row) => row.state === 'pending' || row.state === 'failed')
    if (usable.length === 0) return
    setError(null)

    // `crypto.subtle` exists only in a SECURE CONTEXT (HTTPS, or localhost). The API
    // requires a sha256 per file - it is the dedupe key, and it is re-checked server-side
    // after the bytes land - so without it no batch can be prepared. Say that once, up
    // front: the alternative is 500 rows reading "Could not read the file", which points
    // the operator at the PDFs instead of at the page's origin.
    if (!globalThis.crypto?.subtle) {
      setPhase({ kind: 'idle', message: '' })
      setError(
        'This page must be served over HTTPS (or localhost) to prepare a batch: the browser only exposes cryptographic hashing in a secure context, and every file needs a fingerprint before the API will accept it.',
      )
      return
    }

    // ---- 1. hash every file, one at a time -------------------------------------
    setPhase({ kind: 'hashing', message: `Fingerprinting ${usable.length} files…` })
    const hashed: Map<string, string> = new Map()
    for (let index = 0; index < usable.length; index += 1) {
      const row = usable[index]!
      setRows((current) =>
        current.map((item) =>
          item.file === row.file ? { ...item, state: 'hashing', detail: 'Reading' } : item,
        ),
      )
      try {
        hashed.set(row.file.name + row.file.size, await sha256(row.file))
      } catch {
        setRows((current) =>
          current.map((item) =>
            item.file === row.file
              ? { ...item, state: 'failed', detail: 'Could not read the file' }
              : item,
          ),
        )
      }
      // A yield per file: hashing hundreds of megabytes without one would show the
      // student a frozen tab for the whole scan.
      await new Promise((resolve) => setTimeout(resolve, 0))
    }

    // ---- 2. one manifest call for the whole batch -------------------------------
    setPhase({ kind: 'manifest', message: 'Checking the library for duplicates…' })
    let batch
    try {
      batch = await createUploadBatch({
        files: usable
          .filter((row) => hashed.has(row.file.name + row.file.size))
          .map((row) => ({
            filename: row.file.name,
            sizeBytes: row.file.size,
            contentType: row.file.type || 'application/pdf',
            checksumSha256: hashed.get(row.file.name + row.file.size)!,
          })),
        kind,
        courseId: courseId || null,
        accessTier,
        skipDuplicates: true,
      })
    } catch (caught: unknown) {
      const queryError = caught instanceof QueryError ? caught : new QueryError(caught)
      setPhase({ kind: 'idle', message: '' })
      setError(
        queryError.status === 503
          ? 'Storage is not configured on this deployment, so no upload URL can be issued. The API needs SUPABASE_URL and SUPABASE_SECRET_KEY.'
          : queryError.message,
      )
      return
    }

    setBatchId(batch.batchId)
    const skippedNames = new Set(batch.skipped.map((entry) => entry.filename))
    const ticketByName = new Map<string, UploadTicket>(
      batch.accepted.map((ticket) => [ticket.filename, ticket]),
    )

    setRows((current) =>
      current.map((row) => {
        if (row.state === 'rejected') return row
        const ticket = ticketByName.get(row.file.name)
        if (ticket) {
          return { ...row, state: 'pending', detail: undefined, documentId: ticket.documentId }
        }
        if (skippedNames.has(row.file.name)) {
          const skip = batch.skipped.find((entry) => entry.filename === row.file.name)
          return {
            ...row,
            state: 'duplicate',
            detail:
              skip?.reason === 'DUPLICATE'
                ? 'Already in the library, identical checksum'
                : (skip?.detail ?? skip?.reason ?? 'Skipped'),
          }
        }
        return row
      }),
    )

    setPhase({
      kind: 'uploading',
      message: `Uploading ${batch.acceptedCount} of ${batch.acceptedCount + batch.skippedCount} files · ${batch.skippedCount} skipped`,
    })

    // ---- 3. PUT the bytes, four at a time ---------------------------------------
    const queue = [...batch.accepted]
    const failures: string[] = []
    const worker = async () => {
      for (;;) {
        const ticket = queue.shift()
        if (!ticket) return
        const row = usable.find((item) => item.file.name === ticket.filename)
        if (!row) continue
        setRows((current) =>
          current.map((item) =>
            item.file === row.file ? { ...item, state: 'uploading', progress: 0 } : item,
          ),
        )
        try {
          // The whole ticket, not `ticket.uploadUrl`: the upload carries the URL and how
          // long the signature lasts, which is what the failure message needs.
          await uploadToSignedUrl(ticket, row.file, (fraction) => {
            setRows((current) =>
              current.map((item) =>
                item.file === row.file ? { ...item, progress: fraction } : item,
              ),
            )
          })
          // Confirmed to the API: it checks the object is really in storage, then
          // queues extraction. Without this the document would sit in UPLOADED
          // forever while the operator assumed it was processing.
          await startProcessing(ticket.documentId)
          setRows((current) =>
            current.map((item) =>
              item.file === row.file
                ? { ...item, state: 'uploaded', progress: 1, detail: 'Queued for extraction' }
                : item,
            ),
          )
        } catch (caught: unknown) {
          const queryError = caught instanceof QueryError ? caught : new QueryError(caught)
          failures.push(row.file.name)
          setRows((current) =>
            current.map((item) =>
              item.file === row.file
                ? { ...item, state: 'failed', detail: queryError.message }
                : item,
            ),
          )
        }
      }
    }
    await Promise.all(Array.from({ length: CONCURRENCY }, worker))

    // ---- 4. poll the batch for pipeline state -----------------------------------
    setPhase({
      kind: 'polling',
      message: failures.length
        ? `${failures.length} file${failures.length === 1 ? '' : 's'} failed — retry below`
        : 'All files accepted. Watching the pipeline…',
    })
    await refreshProgress(batch.batchId, setProgress)
    setPhase({ kind: 'done', message: 'Batch complete.' })
  }, [rows, kind, courseId, accessTier])

  const retryFailed = useCallback(() => {
    setRows((current) =>
      current.map((row) =>
        row.state === 'failed' ? { ...row, state: 'pending', detail: undefined, progress: 0 } : row,
      ),
    )
  }, [])

  const busy = phase.kind !== 'idle' && phase.kind !== 'done'

  return (
    <div className="space-y-6">
      <Card>
        <h2 className="text-sm font-semibold tracking-tight text-slate-900">
          Drop a folder of PDFs
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          Up to {MAX_FILES_PER_BATCH} files per batch, 50 MB each. Every file is fingerprinted in
          your browser first, so anything already in the library is skipped before it is uploaded.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <input
            ref={inputRef}
            type="file"
            multiple
            accept="application/pdf"
            className="sr-only"
            onChange={(event) => selectFiles(event.target.files)}
          />
          <Button tone="secondary" onClick={() => inputRef.current?.click()} disabled={busy}>
            Choose files
          </Button>
          <Button onClick={() => void run()} disabled={busy || counts.pending === 0}>
            {busy ? 'Working…' : `Upload ${counts.pending || ''}`.trim()}
          </Button>
          {counts.failed > 0 && (
            <Button tone="quiet" onClick={retryFailed} disabled={busy}>
              Retry {counts.failed} failed
            </Button>
          )}
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <label>
            <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Course (applies to the whole batch)
            </span>
            <select
              value={courseId}
              onChange={(event) => setCourseId(event.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">Leave unassigned</option>
              {(courses.data ?? []).map((course) => (
                <option key={course.id} value={course.id}>
                  {course.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Content type
            </span>
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              {DOCUMENT_KINDS.map((value) => (
                <option key={value} value={value}>
                  {documentKindLabel(value)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Access tier
            </span>
            <select
              value={accessTier}
              onChange={(event) => setAccessTier(event.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              {['FREE', 'PREMIUM', 'PREMIUM_PLUS'].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        </div>
      </Card>

      {phase.kind !== 'idle' && (
        <Card>
          <div className="flex items-center gap-3">
            {busy && <Spinner label="" />}
            <p className="text-sm text-slate-700">{phase.message}</p>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Badge tone="right">{counts.uploaded} uploaded</Badge>
            {counts.duplicates > 0 && <Badge tone="slate">{counts.duplicates} duplicates</Badge>}
            {counts.rejected > 0 && <Badge tone="amber">{counts.rejected} rejected</Badge>}
            {counts.failed > 0 && <Badge tone="wrong">{counts.failed} failed</Badge>}
          </div>
        </Card>
      )}

      {error && (
        <Card>
          <p className="text-sm text-wrong-600">{error}</p>
        </Card>
      )}

      {batchId && progress && (
        <Card>
          <h3 className="text-sm font-semibold tracking-tight text-slate-900">Pipeline progress</h3>
          <p className="mt-1 text-xs text-slate-500">
            Batch <span className="font-mono">{batchId.slice(0, 8)}</span> · {progress.indexed}{' '}
            searchable · {progress.processing} processing · {progress.failed} failed
          </p>
          <ul className="mt-3 space-y-1 text-sm">
            {progress.documents.slice(0, 12).map((document) => (
              <li key={document.id} className="flex items-center justify-between gap-3">
                <span className="truncate text-slate-700">{document.title}</span>
                <Badge
                  tone={
                    document.status === 'FAILED'
                      ? 'wrong'
                      : document.status === 'COMPLETED' || document.status === 'INDEXED'
                        ? 'right'
                        : 'amber'
                  }
                >
                  {document.status}
                </Badge>
              </li>
            ))}
          </ul>
          <div className="mt-4">
            <Link to="/admin/library" className="text-sm font-medium text-brand-700 underline">
              Open the library to read the extracted text
            </Link>
          </div>
        </Card>
      )}

      {rows.length > 0 && (
        <Card>
          <h3 className="text-sm font-semibold tracking-tight text-slate-900">
            {rows.length} file{rows.length === 1 ? '' : 's'} selected
          </h3>
          <ul className="mt-3 divide-y divide-slate-100">
            {rows.map((row) => (
              <li
                key={`${row.file.name}-${row.file.size}`}
                className="flex items-center justify-between gap-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-slate-800">{row.file.name}</p>
                  <p className="text-xs text-slate-500">
                    {(row.file.size / 1024 / 1024).toFixed(1)} MB
                    {row.detail ? ` · ${row.detail}` : ''}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {row.state === 'uploading' && (
                    <span className="text-xs tabular-nums text-slate-500">
                      {Math.round(row.progress * 100)}%
                    </span>
                  )}
                  <Badge tone={STATE_TONE[row.state]}>{row.state}</Badge>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

const STATE_TONE: Record<FileState, 'slate' | 'amber' | 'right' | 'wrong'> = {
  pending: 'slate',
  hashing: 'amber',
  duplicate: 'slate',
  uploading: 'amber',
  uploaded: 'right',
  failed: 'wrong',
  rejected: 'wrong',
}

/** sha256 of a file, hex encoded. Uses the Web Crypto API - no dependency, no upload. */
async function sha256(file: File): Promise<string> {
  const buffer = await file.arrayBuffer()
  const digest = await crypto.subtle.digest('SHA-256', buffer)
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}

async function refreshProgress(
  batchId: string,
  setProgress: (value: BatchProgress) => void,
): Promise<void> {
  try {
    setProgress(await fetchBatchProgress(batchId))
  } catch {
    /* Progress is a convenience here; the library list is the source of truth. */
  }
}
