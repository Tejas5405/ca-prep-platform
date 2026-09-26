/**
 * The student library, driven through the real API client.
 *
 * The bugs these pin are the ones a screenshot does not show:
 *
 *   * the page calls `/content/library`, not the admin list — a student token on the
 *     admin route is a 403, and a helper pointed at the wrong one would render an
 *     empty shelf that looks like "nothing published";
 *   * a search hit's `<mark>` (and a `<script>` inside the excerpt) is rendered as
 *     text, the same rule as question search;
 *   * the original file is not requested until the student asks for it — a signed
 *     URL expires, and fetching one on render burns it before anyone looks;
 *   * a 404 is one message. The server uses 404 for both "missing" and "not yours",
 *     and a page that tried to tell them apart would be inventing information the
 *     API refused to give;
 *   * a 503 from storage is named as storage, not as a broken document. The text
 *     below is still the thing the student can read.
 */

import type { ReactNode } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext } from '../hooks/authContext'
import LibraryPage from '../pages/Library'
import LibraryReader from '../pages/LibraryReader'

import { makeAuth } from './authStub'

vi.mock('../lib/supabase', () => ({
  supabase: {
    auth: {
      getSession: vi.fn().mockResolvedValue({
        data: { session: { access_token: 'test-access-token' } },
        error: null,
      }),
    },
  },
  authRedirectUrl: () => 'http://localhost:3000/auth/callback',
  supabaseDashboardUrl: (path: string) => `https://supabase.com/dashboard/project/test/${path}`,
}))

function ok(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function problem(status: number, title: string, detail: string) {
  return new Response(JSON.stringify({ type: 'about:blank', title, status, detail }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const list = <T,>(data: T[], total = data.length) => ({
  data,
  meta: { requestId: 'test', total, page: 1, limit: 24, hasMore: false },
})

const envelope = <T,>(data: T) => ({ data, meta: { requestId: 'test' } })

const DOC = {
  id: 'doc-1',
  title: 'Ind AS 116 — leases',
  kind: 'STUDY_MATERIAL',
  status: 'INDEXED',
  originalFilename: 'leases.pdf',
  mimeType: 'application/pdf',
  sizeBytes: 1200,
  pageCount: 2,
  extractedChars: 40,
  ocrPages: 0,
  confidence: 0.9,
  version: 1,
  accessTier: 'FREE',
  isPublished: true,
  allowDownload: false,
  difficulty: 'MEDIUM',
  module: 'Financial Reporting',
  tags: [],
  courseId: null,
  subjectId: null,
  chapterId: null,
  topicId: null,
  batchId: null,
  processedAt: null,
  createdAt: null,
  updatedAt: null,
  error: null,
  canRead: true,
}

function renderAt(entry: string, element: ReactNode, pattern?: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <AuthContext.Provider value={makeAuth()}>
        <Routes>
          <Route path={pattern ?? entry.split('?')[0]} element={element} />
          <Route path="/library/:documentId" element={<LibraryReader />} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}

function stubRoutes(routes: [string, Response | unknown][]) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    for (const [needle, body] of routes) {
      if (url.includes(needle)) {
        return Promise.resolve(body instanceof Response ? body : ok(body))
      }
    }
    return Promise.resolve(problem(404, 'no stub', url))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Library', () => {
  it('lists documents from the student route, not the admin one', async () => {
    const fetchMock = stubRoutes([
      ['/curriculum/courses', envelope({ courses: [] })],
      ['/content/library', list([DOC])],
    ])

    renderAt('/library', <LibraryPage />)

    expect(await screen.findByRole('link', { name: 'Ind AS 116 — leases' })).toHaveAttribute(
      'href',
      '/library/doc-1',
    )
    const urls = fetchMock.mock.calls.map((call) => String(call[0]))
    expect(urls.some((url) => url.includes('/content/library'))).toBe(true)
    expect(urls.some((url) => url.includes('/admin/content'))).toBe(false)
  })

  it('renders a page hit as text, so markup in an excerpt cannot execute', async () => {
    const user = userEvent.setup()
    stubRoutes([
      ['/curriculum/courses', envelope({ courses: [] })],
      [
        '/content/search',
        envelope({
          query: 'lease',
          count: 1,
          hits: [
            {
              documentId: 'doc-1',
              title: 'Ind AS 116 — leases',
              pageNumber: 2,
              excerpt: 'A <mark>lease</mark> liability <script>alert(1)</script>',
              score: 0.4,
            },
          ],
        }),
      ],
      ['/content/library', list([])],
    ])

    renderAt('/library', <LibraryPage />)
    await user.type(screen.getByLabelText('Search your library'), 'lease')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(await screen.findByText('lease', { selector: 'mark' })).toBeInTheDocument()
    expect(document.querySelector('script')).toBeNull()
    expect(document.body.textContent).toContain('<script>alert(1)</script>')
    expect(screen.getByRole('link', { name: /page 2/i })).toHaveAttribute(
      'href',
      '/library/doc-1?page=2',
    )
  })

  it('says the shelf is empty rather than inventing material', async () => {
    stubRoutes([
      ['/curriculum/courses', envelope({ courses: [] })],
      ['/content/library', list([])],
    ])

    renderAt('/library', <LibraryPage />)
    expect(await screen.findByText('Nothing in your library yet')).toBeInTheDocument()
  })
})

describe('Library reader', () => {
  it('shows the extracted text and does not mint a file URL until asked', async () => {
    const fetchMock = stubRoutes([
      [
        '/pages',
        envelope({
          pages: [
            {
              pageNumber: 1,
              text: 'A lease liability is recognised.',
              charCount: 34,
              usedOcr: false,
            },
          ],
          total: 2,
          hasMore: true,
        }),
      ],
      ['/content/documents/doc-1', envelope(DOC)],
    ])

    renderAt('/library/doc-1', <LibraryReader />, '/library/:documentId')

    expect(await screen.findByText('A lease liability is recognised.')).toBeInTheDocument()
    expect(screen.getByText(/downloading is turned off/i)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /download/i })).not.toBeInTheDocument()

    const urls = fetchMock.mock.calls.map((call) => String(call[0]))
    expect(urls.some((url) => url.includes('/file'))).toBe(false)
  })

  it('names a storage outage instead of a broken document', async () => {
    const user = userEvent.setup()
    stubRoutes([
      [
        '/pages',
        envelope({
          pages: [{ pageNumber: 1, text: 'Still readable.', charCount: 15, usedOcr: false }],
          total: 1,
          hasMore: false,
        }),
      ],
      [
        '/file',
        problem(503, 'Storage unavailable', 'Storage is not configured for this deployment.'),
      ],
      ['/content/documents/doc-1', envelope(DOC)],
    ])

    renderAt('/library/doc-1', <LibraryReader />, '/library/:documentId')
    await screen.findByText('Still readable.')
    await user.click(screen.getByRole('button', { name: 'Open original PDF' }))

    expect(await screen.findByText('Original file unavailable')).toBeInTheDocument()
    expect(screen.getByText('Still readable.')).toBeInTheDocument()
  })

  it('uses one message for a document the server will not confirm', async () => {
    stubRoutes([
      [
        '/content/documents/missing',
        problem(404, 'Not found', 'That document is not available to you.'),
      ],
    ])

    renderAt('/library/missing', <LibraryReader />, '/library/:documentId')
    expect(await screen.findByText('Not available')).toBeInTheDocument()
    expect(screen.queryByText(/upgrade/i)).not.toBeInTheDocument()
  })

  it('offers a download only when the document allows one', async () => {
    const user = userEvent.setup()
    stubRoutes([
      [
        '/pages',
        envelope({
          pages: [{ pageNumber: 1, text: 'Page one.', charCount: 9, usedOcr: false }],
          total: 1,
          hasMore: false,
        }),
      ],
      [
        '/file',
        envelope({
          url: 'https://storage.example/signed',
          expiresInSeconds: 3600,
          downloadable: true,
          filename: 'leases.pdf',
        }),
      ],
      ['/content/documents/doc-1', envelope({ ...DOC, allowDownload: true })],
    ])

    renderAt('/library/doc-1', <LibraryReader />, '/library/:documentId')
    await screen.findByText('Page one.')
    expect(screen.queryByText(/downloading is turned off/i)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Open original PDF' }))
    const link = await screen.findByRole('link', { name: /download leases\.pdf/i })
    expect(link).toHaveAttribute('href', 'https://storage.example/signed')
  })
})

describe('file URL is minted on demand', () => {
  it('calls /file only after Open original', async () => {
    const user = userEvent.setup()
    const fetchMock = stubRoutes([
      [
        '/pages',
        envelope({
          pages: [{ pageNumber: 1, text: 'Page one.', charCount: 9, usedOcr: false }],
          total: 1,
          hasMore: false,
        }),
      ],
      [
        '/file',
        envelope({
          url: 'https://storage.example/signed',
          expiresInSeconds: 3600,
          downloadable: false,
          filename: 'leases.pdf',
        }),
      ],
      ['/content/documents/doc-1', envelope(DOC)],
    ])

    renderAt('/library/doc-1', <LibraryReader />, '/library/:documentId')
    await screen.findByText('Page one.')
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]))
      expect(urls.some((url) => url.includes('/file'))).toBe(false)
    })
    await user.click(screen.getByRole('button', { name: 'Open original PDF' }))
    await screen.findByTitle('Original PDF')
    const urls = fetchMock.mock.calls.map((call) => String(call[0]))
    expect(urls.some((url) => url.includes('/content/documents/doc-1/file'))).toBe(true)
  })
})
