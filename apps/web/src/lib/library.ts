/**
 * The student-facing side of the content library.
 *
 * SEPARATE FROM THE ADMIN TYPES ABOVE, and deliberately so. `AdminDocument` carries
 * pipeline internals - `checksum_sha256`, `batch_id`, `error`, `is_published`,
 * `deleted_at`, the uploader - because the console needs them. A student's payload is
 * `_document_payload(document, viewer=...)`, which is the same function minus nothing
 * today: the server sends the same fields and adds `canRead`.
 *
 * That "minus nothing" is exactly why the types are separate. If the student type were
 * an alias of the admin one, the day somebody trims the student payload (which is the
 * right thing to do - a student has no use for a checksum) every screen would keep
 * compiling while reading a field that had quietly stopped being sent. Two types means
 * that change is a type error at the point of use.
 *
 * WHAT THE SERVER DOES NOT DO FOR US
 *
 * The library endpoint returns every document the caller may read, already filtered by
 * `document_filter` in SQL. Nothing here re-checks access, because a second check in the
 * browser is a check that can disagree with the first - and the browser's copy is not
 * the one that matters. `canRead` is read only to explain a state, never to permit one.
 */

import { api, type ApiList, type ApiSuccess } from './api'
import { QueryError } from './queries'

/** One document as the library list and the reader see it. */
export interface LibraryDocument {
  id: string
  title: string
  kind: string
  status: string
  originalFilename: string
  mimeType: string
  sizeBytes: number
  pageCount: number | null
  extractedChars: number | null
  ocrPages: number | null
  confidence: number | null
  version: number
  accessTier: string
  allowDownload: boolean
  difficulty: string
  module: string | null
  tags: string[]
  courseId: string | null
  subjectId: string | null
  chapterId: string | null
  topicId: string | null
  processedAt: string | null
  createdAt: string | null
  updatedAt: string | null
  /**
   * Present because the server sends it. A student list is already filtered to
   * published, readable rows, so this is informational — never a second access check.
   */
  isPublished: boolean
  /** Pipeline failure, when there is one. Null on a healthy document. */
  error: string | null
  /** The server's own verdict for the CALLER. Shown, never relied on. */
  canRead: boolean
}

/** One page of extracted text. */
export interface LibraryPage {
  pageNumber: number
  text: string
  charCount: number
  usedOcr: boolean
}

/** A hit from `GET /content/search` — search INSIDE the material, not over titles. */
export interface DocumentSearchHit {
  documentId: string
  title: string
  pageNumber: number
  /**
   * The matched fragment, highlighted by PostgreSQL's `ts_headline` with `<mark>` tags.
   *
   * RENDERED AS TEXT, NEVER AS HTML. The excerpt is document content, and document
   * content is attacker-influenced in general (a PDF can contain anything). The
   * study-screens test asserts no `dangerouslySetInnerHTML` reaches this.
   */
  excerpt: string
  score: number
}

export interface LibraryFileTicket {
  url: string
  expiresInSeconds: number
  /** False when the document is view-only. The server refuses to sign a download for it. */
  downloadable: boolean
  filename: string
}

/**
 * A document fetch error worth distinguishing on screen.
 *
 * The server answers 404 for BOTH "no such document" and "not yours", on purpose: telling
 * a student that a document exists but is not theirs leaks the catalogue. So the reader
 * shows one message for a 404 and does not try to guess which it was.
 */
export function libraryErrorKind(
  error: unknown,
): 'NOT_FOUND' | 'NOT_READY' | 'UNAVAILABLE' | 'OTHER' {
  if (!(error instanceof QueryError)) return 'OTHER'
  if (error.status === 404) return 'NOT_FOUND'
  if (error.status === 409) return 'NOT_READY'
  // 503 means this deployment cannot reach object storage. It is the one failure the
  // student can do nothing about, so it is named rather than lumped in with "other".
  if (error.status === 503) return 'UNAVAILABLE'
  return 'OTHER'
}

export function fetchLibrary(
  params: {
    q?: string
    kind?: string
    courseId?: string
    subjectId?: string
    page?: number
    limit?: number
  } = {},
  signal?: AbortSignal,
) {
  const query = new URLSearchParams()
  if (params.q) query.set('q', params.q)
  if (params.kind) query.set('kind', params.kind)
  if (params.courseId) query.set('course_id', params.courseId)
  if (params.subjectId) query.set('subject_id', params.subjectId)
  query.set('page', String(params.page ?? 1))
  query.set('limit', String(params.limit ?? 24))
  return api
    .get<ApiList<LibraryDocument>>(`/content/library?${query}`, signal)
    .catch((error: unknown) => {
      throw new QueryError(error)
    })
}

export function fetchLibraryDocument(documentId: string, signal?: AbortSignal) {
  return api
    .get<ApiSuccess<LibraryDocument>>(
      `/content/documents/${encodeURIComponent(documentId)}`,
      signal,
    )
    .then((body) => body.data)
    .catch((error: unknown) => {
      throw new QueryError(error)
    })
}

export function fetchLibraryPages(documentId: string, page = 1, limit = 25, signal?: AbortSignal) {
  const query = new URLSearchParams({ page: String(page), limit: String(limit) })
  return api
    .get<ApiSuccess<{ pages: LibraryPage[]; total: number; hasMore: boolean }>>(
      `/content/documents/${encodeURIComponent(documentId)}/pages?${query}`,
      signal,
    )
    .then((body) => body.data)
    .catch((error: unknown) => {
      throw new QueryError(error)
    })
}

/**
 * Mint a short-lived URL for the original file.
 *
 * CALLED ON DEMAND, NOT ON RENDER. The URL expires (an hour at most, deliberately), so
 * holding one in component state past its lifetime gives the student a viewer that fails
 * with the storage host's error instead of ours. Fetching it when the reader is opened
 * and again on an explicit retry keeps the window honest.
 */
export function fetchLibraryFile(documentId: string, signal?: AbortSignal) {
  return api
    .get<ApiSuccess<LibraryFileTicket>>(
      `/content/documents/${encodeURIComponent(documentId)}/file`,
      signal,
    )
    .then((body) => body.data)
    .catch((error: unknown) => {
      throw new QueryError(error)
    })
}

export function searchLibrary(q: string, limit = 20, signal?: AbortSignal) {
  const query = new URLSearchParams({ q, limit: String(limit) })
  return api
    .get<ApiSuccess<{ query: string; hits: DocumentSearchHit[]; count: number }>>(
      `/content/search?${query}`,
      signal,
    )
    .then((body) => body.data)
    .catch((error: unknown) => {
      throw new QueryError(error)
    })
}
