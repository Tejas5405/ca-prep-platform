import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { Badge, Button, Card, EmptyState, ErrorState, Spinner } from '../components/ui'
import { DOCUMENT_KINDS, documentKindLabel } from '../lib/contentKinds'
import { Headline } from '../lib/headline'
import {
  fetchLibrary,
  searchLibrary,
  type DocumentSearchHit,
  type LibraryDocument,
} from '../lib/library'
import { fetchCourses } from '../lib/queries'
import { useLoader } from '../lib/useLoader'

/**
 * The student's library.
 *
 * WHAT THIS PAGE DOES NOT DO
 *
 * It does not decide which documents the student may read. `GET /content/library`
 * already applied `document_filter` in SQL: unpublished rows, rows above the
 * student's tier, denied grants and soft-deleted files were never selected. A second
 * check here would be a check that can disagree with the first, and the browser's
 * copy is not the one that matters.
 *
 * Search has two answers, because the server has two:
 *
 *   * the list's `q` finds documents whose title, filename, tags OR extracted text
 *     match — one row per document, so a 40-page hit is still one card;
 *   * `GET /content/search` finds the page, with the matched words marked, so a
 *     student looking for "Ind AS 116" lands on the paragraph rather than on a
 *     filename that does not mention it.
 *
 * The second one is what "search inside your material" means. The first one is how
 * the shelf stays browsable.
 */

export default function LibraryPage() {
  const [params, setParams] = useSearchParams()
  const urlTerm = params.get('q') ?? ''
  const kind = params.get('kind') ?? ''
  const courseId = params.get('course') ?? ''
  const page = Math.max(1, Number(params.get('page') ?? '1') || 1)

  // `term` is what has been searched; `draft` is what is in the box. Typing must
  // not fire a request per keystroke — the library query joins extracted text, and
  // a request per character is how a search box takes the API down.
  const [term, setTerm] = useState(urlTerm)
  const [draft, setDraft] = useState(urlTerm)

  const library = useLoader(
    (signal) =>
      fetchLibrary(
        {
          page,
          limit: 24,
          ...(term ? { q: term } : {}),
          ...(kind ? { kind } : {}),
          ...(courseId ? { courseId } : {}),
        },
        signal,
      ),
    [term, kind, courseId, page],
  )

  // Page hits only exist once there is a term the search endpoint will accept
  // (it rejects fewer than two characters). A shelf browse is not a search.
  const hits = useLoader(
    (signal) => searchLibrary(term, 20, signal),
    [term],
    term.trim().length >= 2,
  )

  // The course filter is a convenience. If the curriculum endpoint fails, the
  // shelf still has to load — hiding every document because the dropdown could
  // not be filled would be the wrong failure.
  const courses = useLoader((signal) => fetchCourses(signal), [])

  function run(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = draft.trim()
    const next = new URLSearchParams(params)
    if (trimmed) next.set('q', trimmed)
    else next.delete('q')
    next.delete('page')
    setParams(next)
    setTerm(trimmed)
  }

  function setFilter(key: 'kind' | 'course', value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    next.delete('page')
    setParams(next)
  }

  function setPage(nextPage: number) {
    const next = new URLSearchParams(params)
    if (nextPage <= 1) next.delete('page')
    else next.set('page', String(nextPage))
    setParams(next)
  }

  const documents = library.data?.data ?? []
  const meta = library.data?.meta

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Library</h1>
        <p className="mt-1 max-w-2xl text-slate-600">
          Study material you can read. The list is already limited to your plan — a document you
          cannot open is not hiding further down the page, it was never returned.
        </p>
      </div>

      <form onSubmit={run} className="flex flex-col gap-2 sm:flex-row">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Search inside the material, not just the filenames"
          aria-label="Search your library"
          className="w-full rounded-md border border-slate-300 px-3 py-2"
        />
        <Button type="submit" disabled={library.loading || !draft.trim()}>
          {library.loading ? 'Searching…' : 'Search'}
        </Button>
      </form>

      <div className="flex flex-wrap gap-2">
        <FilterSelect
          label="Kind"
          value={kind}
          onChange={(value) => setFilter('kind', value)}
          options={[
            { value: '', label: 'Any kind' },
            ...DOCUMENT_KINDS.map((item) => ({ value: item, label: documentKindLabel(item) })),
          ]}
        />
        {courses.data && courses.data.length > 0 && (
          <FilterSelect
            label="Course"
            value={courseId}
            onChange={(value) => setFilter('course', value)}
            options={[
              { value: '', label: 'Any course' },
              ...courses.data.map((course) => ({ value: course.id, label: course.name })),
            ]}
          />
        )}
      </div>

      {library.error && <ErrorState error={library.error} what="your library" />}
      {library.loading && <Spinner label="Loading your library…" />}

      {term.trim().length >= 2 && (
        <section aria-labelledby="library-hits">
          <h2 id="library-hits" className="text-lg font-medium text-slate-900">
            Inside the text
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Matches in the extracted pages, not in the filenames.
          </p>
          {hits.loading && <Spinner label="Searching the pages…" />}
          {hits.error && <ErrorState error={hits.error} what="that search" />}
          {hits.data && hits.data.hits.length === 0 && !hits.loading && (
            <p className="mt-3 text-sm text-slate-600">
              No page contains “{term}”. The shelf below still lists documents whose names match, if
              any do.
            </p>
          )}
          {hits.data && hits.data.hits.length > 0 && (
            <ul className="mt-3 space-y-2">
              {hits.data.hits.map((hit) => (
                <HitRow key={`${hit.documentId}-${hit.pageNumber}`} hit={hit} />
              ))}
            </ul>
          )}
        </section>
      )}

      {!library.loading && !library.error && documents.length === 0 && (
        <EmptyState
          title={term || kind || courseId ? 'Nothing matches' : 'Nothing in your library yet'}
          body={
            term || kind || courseId
              ? 'No document you can read matches those filters. Clearing them shows the whole shelf.'
              : 'Material appears here once it has been published for your plan. Uploading a PDF does not put it on this page — a reviewer publishes it first.'
          }
          action={
            term || kind || courseId ? (
              <button
                type="button"
                onClick={() => {
                  setParams(new URLSearchParams())
                  setTerm('')
                  setDraft('')
                }}
                className="text-sm font-medium text-brand-600 hover:underline"
              >
                Clear filters
              </button>
            ) : undefined
          }
        />
      )}

      {documents.length > 0 && (
        <ul className="grid gap-3 sm:grid-cols-2">
          {documents.map((document) => (
            <li key={document.id}>
              <DocumentCard document={document} />
            </li>
          ))}
        </ul>
      )}

      {meta && meta.total > meta.limit && (
        <div className="flex items-center justify-between text-sm text-slate-600">
          <span>
            {meta.total} document{meta.total === 1 ? '' : 's'}
          </span>
          <div className="flex gap-2">
            <Button tone="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              Previous
            </Button>
            <Button tone="secondary" disabled={!meta.hasMore} onClick={() => setPage(page + 1)}>
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

function HitRow({ hit }: { hit: DocumentSearchHit }) {
  return (
    <li>
      <Link
        to={`/library/${hit.documentId}?page=${hit.pageNumber}`}
        className="block rounded-lg border border-slate-200 bg-white px-4 py-3 hover:border-brand-300"
      >
        <p className="text-sm font-medium text-slate-900">
          {hit.title} <span className="font-normal text-slate-500">· page {hit.pageNumber}</span>
        </p>
        <p className="mt-1 text-sm leading-relaxed text-slate-700">
          <Headline text={hit.excerpt} />
        </p>
      </Link>
    </li>
  )
}

function DocumentCard({ document }: { document: LibraryDocument }) {
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="brand">{documentKindLabel(document.kind)}</Badge>
        {document.accessTier !== 'FREE' && <Badge>{document.accessTier.replace('_', ' ')}</Badge>}
        {!document.allowDownload && <Badge>View only</Badge>}
      </div>
      <h2 className="mt-2 text-base font-medium text-slate-900">
        <Link to={`/library/${document.id}`} className="hover:underline">
          {document.title}
        </Link>
      </h2>
      <p className="mt-1 text-sm text-slate-500">
        {document.pageCount === null
          ? 'Pages not counted yet'
          : `${document.pageCount} page${document.pageCount === 1 ? '' : 's'}`}
        {document.module ? ` · ${document.module}` : ''}
      </p>
    </Card>
  )
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-slate-600">
      {label}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-label={label}
        className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-slate-800"
      >
        {options.map((option) => (
          <option key={option.value || 'any'} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}
