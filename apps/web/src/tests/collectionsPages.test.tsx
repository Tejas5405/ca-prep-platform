/**
 * Tests for the two list screens that are easy to mistake for one another.
 *
 * WHY THESE ARE WORTH HAVING. A collection and a bookmark are both "questions I kept
 * for later", and the ways they get blurred are all failures a screenshot cannot
 * show:
 *
 *   * the counts on the list come from the server, so a stale local increment cannot
 *     pass as a real count;
 *   * creating a collection sends a name, and a duplicate name is refused by the
 *     server - the message a student sees must be the server's, not a generic one;
 *   * deleting asks first, and a system collection offers no delete at all, because
 *     the LDR list is referenced by the practice loop;
 *   * the LDR screen offers NO control that writes the flag, since the flag is owned
 *     by the practice loop and two writers would eventually disagree;
 *   * `accuracy: null` renders as "not scored yet" rather than "0%", which is the
 *     difference between a skipped question and a failed one.
 *
 * `fetch` is stubbed rather than the query helpers, so the URL each call builds is
 * part of what is under test.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext } from '../hooks/authContext'
import CollectionsPage from '../pages/Collections'
import LdrPage from '../pages/Ldr'

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

function failure(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const envelope = <T,>(data: T) => ({ data, meta: { requestId: 'test' } })

const list = <T,>(items: T[]) => ({
  data: items,
  meta: { requestId: 'test', total: items.length, page: 1, limit: 20, hasMore: false },
})

type Route = [string, unknown | (() => unknown)]

/**
 * Route a stub by URL substring, so a test declares only the endpoints it needs.
 *
 * ORDER MATTERS: the FIRST matching substring wins, so a specific path must come
 * before a prefix of it. `'/collections'` placed before `'/collections/c-1/questions'`
 * swallows the add call and the test then fails somewhere else entirely.
 */
function stubRoutes(routes: Route[]) {
  const calls: { url: string; method: string; body: unknown }[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? JSON.parse(String(init.body)) : null
    calls.push({ url, method, body })
    for (const [needle, response] of routes) {
      if (url.includes(needle)) {
        const value = typeof response === 'function' ? response() : response
        return value instanceof Response ? value : ok(value)
      }
    }
    return failure(404, { title: `no stub for ${url}`, status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, calls }
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderAt(element: React.ReactNode) {
  return render(
    <MemoryRouter>
      <AuthContext.Provider value={makeAuth()}>
        <Routes>
          <Route path="*" element={element} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}

const COLLECTION = {
  id: 'c-1',
  name: 'Before mock 3',
  description: null,
  kind: 'MANUAL',
  filters: null,
  isSystem: false,
  questionCount: 2,
  createdAt: '2026-09-01T10:00:00Z',
  updatedAt: '2026-09-01T10:00:00Z',
}

const SYSTEM_COLLECTION = {
  ...COLLECTION,
  id: 'c-sys',
  name: 'Marked for later',
  kind: 'MANUAL',
  isSystem: true,
  questionCount: 0,
}

const DETAIL = {
  ...COLLECTION,
  questions: [
    {
      questionId: 'q-1',
      text: 'Which section governs the transfer of a capital asset?',
      questionType: 'MCQ',
      difficulty: 'HARD',
      marks: 2,
      subjectId: 's-1',
      chapterId: null,
      isHistorical: false,
      financeActYear: null,
      disclaimer: null,
      note: 'lost 4 marks here in mock 2',
      options: [
        { label: 'A', text: 'Section 45' },
        { label: 'B', text: 'Section 48' },
      ],
    },
    {
      questionId: 'q-2',
      text: 'Compute the deduction under section 80C for the assessment year 2018-19.',
      questionType: 'MCQ',
      difficulty: 'MEDIUM',
      marks: 4,
      subjectId: 's-1',
      chapterId: null,
      isHistorical: true,
      financeActYear: '2018-19',
      disclaimer: 'Decided under the law as it stood for that year — check the current provision.',
      note: null,
      options: [{ label: 'A', text: '₹1,50,000' }],
    },
  ],
  count: 2,
  total: 2,
  page: 1,
  limit: 50,
  hasMore: false,
}

describe('Collections', () => {
  it('lists collections with the counts the server reports', async () => {
    stubRoutes([['/collections', list([COLLECTION, SYSTEM_COLLECTION])]])

    renderAt(<CollectionsPage />)

    expect(await screen.findByRole('heading', { name: 'Before mock 3' })).toBeInTheDocument()
    // The count is the server's `questionCount`, rendered as a sentence rather than
    // a bare number, so "2 questions" cannot be mistaken for a page count.
    expect(screen.getByText(/2 questions/)).toBeInTheDocument()
    // A system collection says so, because it was not built by the student.
    expect(screen.getByText(/built by CA Prep/)).toBeInTheDocument()
  })

  it('offers no delete for a system collection', async () => {
    stubRoutes([['/collections', list([SYSTEM_COLLECTION])]])

    renderAt(<CollectionsPage />)

    await screen.findByRole('heading', { name: 'Marked for later' })
    // Absent, not disabled: the LDR list is referenced by the practice loop, and a
    // disabled delete invites a bug report.
    expect(screen.queryByRole('button', { name: /^delete$/i })).not.toBeInTheDocument()
  })

  it('creates a collection with the typed name and refreshes the list', async () => {
    const user = userEvent.setup()
    let created = false
    const { calls } = stubRoutes([
      [
        '/collections',
        () => (created ? list([COLLECTION]) : list([])),
      ],
    ])

    renderAt(<CollectionsPage />)

    await screen.findByText('No collections yet')
    await user.type(screen.getByLabelText(/new collection/i), 'Before mock 3')
    created = true
    await user.click(screen.getByRole('button', { name: /^create$/i }))

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'POST')).toBe(true)
    })
    const post = calls.find((call) => call.method === 'POST')
    // A MANUAL collection, sent by name: the kind is explicit so the server does not
    // have to guess, and no filters are invented.
    expect(post?.url).toContain('/collections')
    expect(post?.body).toEqual({ name: 'Before mock 3', kind: 'MANUAL', description: null })
    // And the list is refetched rather than patched locally.
    expect(await screen.findByRole('heading', { name: 'Before mock 3' })).toBeInTheDocument()
  })

  it('shows the server message when the name is already used', async () => {
    const user = userEvent.setup()
    // The 409 is on the POST only. `stubRoutes` matches on the URL, and the GET and
    // the POST share one, so this test needs the method as well - which is what the
    // real client distinguishes them by too.
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input.toString()
        if ((init?.method ?? 'GET').toUpperCase() === 'POST' && url.includes('/collections')) {
          return failure(409, {
            type: 'https://api.caprep.in/errors/collections',
            title: 'Name already used',
            status: 409,
            detail: 'You already have a collection called “Before mock 3”.',
          })
        }
        return ok(list([]))
      }),
    )

    renderAt(<CollectionsPage />)

    await screen.findByText('No collections yet')
    await user.type(screen.getByLabelText(/new collection/i), 'Before mock 3')
    await user.click(screen.getByRole('button', { name: /^create$/i }))

    // The server's sentence, quoted, because it names the collection that collided.
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/already have a collection called/i)
  })

  it('asks before deleting, and sends nothing until it is confirmed', async () => {
    const user = userEvent.setup()
    const { calls } = stubRoutes([
      ['/collections/c-1', ok(envelope({ deleted: true, collectionId: 'c-1' }))],
      ['/collections', list([COLLECTION])],
    ])

    renderAt(<CollectionsPage />)

    await screen.findByRole('heading', { name: 'Before mock 3' })
    await user.click(screen.getByRole('button', { name: /^delete$/i }))

    // Confirmation first: a collection can hold a term's worth of work.
    expect(screen.getByText(/delete this collection\?/i)).toBeInTheDocument()
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)

    await user.click(screen.getByRole('button', { name: /yes, delete/i }))
    await waitFor(() => {
      expect(calls.some((call) => call.method === 'DELETE')).toBe(true)
    })
  })

  it('opens a collection and shows each question with its note and disclaimer', async () => {
    const user = userEvent.setup()
    stubRoutes([
      ['/collections/c-1', ok(envelope(DETAIL))],
      ['/collections', list([COLLECTION])],
    ])

    renderAt(<CollectionsPage />)

    await screen.findByRole('heading', { name: 'Before mock 3' })
    // The detail request must not be made before it is asked for.
    expect(screen.queryByText(/which section governs/i)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /^open$/i }))

    expect(await screen.findByText(/which section governs/i)).toBeInTheDocument()
    expect(screen.getByText(/lost 4 marks here in mock 2/i)).toBeInTheDocument()
    // The repealed-provision warning travels with the question into this list too.
    expect(screen.getByText(/check the current provision/i)).toBeInTheDocument()
    expect(screen.getByText('Historical')).toBeInTheDocument()
  })

  it('removes a question from a collection without deleting the question', async () => {
    const user = userEvent.setup()
    // The detail is reduced only AFTER the delete, so the first render really does
    // contain the question under test - a stub that returns the reduced list from
    // the start passes for the wrong reason and would keep passing if the page
    // never rendered the question at all.
    let removed = false
    const { calls } = stubRoutes([
      [
        '/collections/c-1/questions/q-1',
        () => {
          removed = true
          return envelope({ removed: true })
        },
      ],
      [
        '/collections/c-1',
        () =>
          envelope(
            removed
              ? { ...DETAIL, questions: [DETAIL.questions[1]], count: 1, total: 1 }
              : DETAIL,
          ),
      ],
      ['/collections', list([COLLECTION])],
    ])

    renderAt(<CollectionsPage />)

    await screen.findByRole('heading', { name: 'Before mock 3' })
    await user.click(screen.getByRole('button', { name: /^open$/i }))
    await screen.findByText(/which section governs/i)

    const first = screen.getByText(/which section governs/i).closest('li')
    expect(first).not.toBeNull()
    await user.click(within(first as HTMLElement).getByRole('button', { name: /^remove$/i }))

    await waitFor(() => {
      const call = calls.find((entry) => entry.method === 'DELETE')
      expect(call).toBeTruthy()
      // A membership delete, not a question delete: the last segment is the question
      // id and the collection id is still in the path.
      expect(call?.url).toContain('/collections/c-1/questions/q-1')
    })
  })

  it('links the two "for later" ideas to each other without merging them', async () => {
    stubRoutes([['/collections', list([])]])

    renderAt(<CollectionsPage />)

    await screen.findByText('No collections yet')
    const link = screen.getByRole('link', { name: /marked for later/i })
    expect(link).toHaveAttribute('href', '/ldr')
  })
})

describe('Marked for later', () => {
  const MARKED = {
    questionId: 'q-7',
    text: 'State whether the supply is inter-state for IGST purposes.',
    questionType: 'MCQ',
    difficulty: 'MEDIUM',
    marks: 4,
    subjectId: 's-2',
    chapterId: null,
    isHistorical: false,
    financeActYear: null,
    disclaimer: null,
    attempts: 2,
    accuracy: 0.5,
    markedAt: '2026-09-20T09:00:00Z',
  }

  it('shows each flagged question with the accuracy the server reports', async () => {
    stubRoutes([['/ldr', list([MARKED])]])

    renderAt(<LdrPage />)

    expect(await screen.findByText(/inter-state for IGST/i)).toBeInTheDocument()
    expect(screen.getByText('50%')).toBeInTheDocument()
    expect(screen.getByText(/1 question marked/i)).toBeInTheDocument()
  })

  it('says a question is not scored yet rather than showing 0%', async () => {
    stubRoutes([
      // `accuracy: null` is a question attempted but skipped: reporting 0% would
      // invent a result the student never got.
      ['/ldr', list([{ ...MARKED, attempts: 1, accuracy: null }])],
    ])

    renderAt(<LdrPage />)

    expect(await screen.findByText(/not scored yet/i)).toBeInTheDocument()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
  })

  it('offers no control that writes the flag', async () => {
    stubRoutes([['/ldr', list([MARKED])]])

    renderAt(<LdrPage />)

    await screen.findByText(/inter-state for IGST/i)
    // The flag belongs to the practice loop; a toggle here would be a second writer
    // and the two would eventually disagree.
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('explains what the empty list means instead of showing a bare blank', async () => {
    stubRoutes([['/ldr', list([])]])

    renderAt(<LdrPage />)

    expect(await screen.findByText(/nothing marked yet/i)).toBeInTheDocument()
    expect(screen.getByText(/mark the questions you want to see again/i)).toBeInTheDocument()
  })

  it('surfaces a failed request as an error rather than an empty list', async () => {
    stubRoutes([
      [
        '/ldr',
        failure(503, {
          type: 'https://api.caprep.in/errors/about:blank',
          title: 'Service unavailable',
          status: 503,
          detail: 'The database is not reachable.',
        }),
      ],
    ])

    renderAt(<LdrPage />)

    // An empty page and a broken page look identical to a student, which is exactly
    // how a broken feature survives for weeks.
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load/i)
    expect(screen.queryByText(/nothing marked yet/i)).not.toBeInTheDocument()
  })
})
