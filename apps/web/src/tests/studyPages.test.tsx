/**
 * Tests for the study screens, driven through the real API client.
 *
 * WHY THESE ARE WORTH HAVING. Every one of them asserts a behaviour that is
 * invisible in a screenshot and expensive in production:
 *
 *   * the answer key arrives only in the RESPONSE to an answer, so an option is
 *     never marked correct before the student has committed (a leaked key is a
 *     leaked exam);
 *   * a multiple-choice option that the server reports as correct is styled from
 *     the server's flag rather than from a second local computation;
 *   * a search snippet from PostgreSQL's `ts_headline` is rendered as TEXT, so a
 *     question containing markup is displayed rather than executed;
 *   * a mock report shows the stored outcome per question, including the
 *     un-attempted and pending-review states, which are the two that get lost
 *     when a report is written as "correct or wrong".
 *
 * `fetch` is stubbed rather than the query helpers, because stubbing the helpers
 * would skip the client that builds the URLs and reads the envelope - and a
 * mistyped query parameter is exactly the kind of bug these tests should catch.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext } from '../hooks/authContext'
import MocksPage, { ReportPage } from '../pages/Mocks'
import PracticePage from '../pages/Practice'
import SearchPage from '../pages/SearchPage'

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

const envelope = <T,>(data: T) => ({ data, meta: { requestId: 'test' } })

/**
 * Mount a page at a URL, optionally with a route pattern of its own.
 *
 * The pattern is a separate argument because the two must be allowed to differ: a
 * `Route path` is a matcher, not a location, so it cannot carry a query string and
 * cannot match `/reports/att-1` unless it says `:attemptId`. Passing the entry
 * straight in as the path is the bug this helper exists to prevent - it renders an
 * empty tree (query string) or leaves `useParams` undefined (literal segment), and
 * both failures look like a page that simply never finished loading.
 */
function renderAt(entry: string, element: React.ReactNode, pattern?: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <AuthContext.Provider value={makeAuth()}>
        <Routes>
          <Route path={pattern ?? entry.split('?')[0]} element={element} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}

/** Route a stub by URL substring, so a test only declares the endpoints it needs. */
function stubRoutes(routes: [string, unknown][]) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    for (const [needle, body] of routes) {
      if (url.includes(needle)) return Promise.resolve(ok(body))
    }
    return Promise.resolve(
      new Response(JSON.stringify({ title: `no stub for ${url}`, status: 404 }), { status: 404 }),
    )
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

const CHAPTER = '579ca727-7c63-408b-a4c8-c01e33a3b867'

const QUESTION = {
  id: 'q-1',
  text: 'Which accounting concept recognises revenue when it is earned?',
  questionType: 'MCQ',
  difficulty: 'EASY',
  marks: 2,
  negativeMarks: 0,
  subjectId: 's-1',
  chapterId: CHAPTER,
  source: 'Platform seed content',
  isPremium: false,
  options: [
    { label: 'A', text: 'Cash basis' },
    { label: 'B', text: 'Accrual basis' },
  ],
}

describe('Practice', () => {
  it('does not mark any option correct before the student answers', async () => {
    // The question payload carries no `isCorrect` and the UI must not invent one.
    stubRoutes([
      ['/curriculum/courses', envelope({ courses: [] })],
      ['/practice/questions', envelope({ questions: [QUESTION] })],
    ])

    renderAt(`/practice?chapter=${CHAPTER}`, <PracticePage />)

    await screen.findByText(/which accounting concept/i)
    expect(screen.queryByText(/^Correct$/)).not.toBeInTheDocument()
    expect(screen.queryByText(/your answer/i)).not.toBeInTheDocument()
  })

  it('grades an answer from the response, not from local knowledge', async () => {
    const user = userEvent.setup()
    stubRoutes([
      ['/curriculum/courses', envelope({ courses: [] })],
      ['/practice/answers', envelope({
        questionId: 'q-1',
        isCorrect: false,
        correctAnswer: 'B',
        explanation: 'Accrual recognises revenue when the obligation is met.',
        pointsAwarded: 0,
        attemptsCount: 1,
        correctCount: 0,
        accuracy: 0,
        currentStreak: 0,
        options: [
          { label: 'A', text: 'Cash basis', isCorrect: false },
          { label: 'B', text: 'Accrual basis', isCorrect: true },
        ],
      })],
      ['/practice/questions', envelope({ questions: [QUESTION] })],
    ])

    renderAt(`/practice?chapter=${CHAPTER}`, <PracticePage />)
    await screen.findByText(/which accounting concept/i)

    await user.click(screen.getByRole('button', { name: /Cash basis/ }))

    expect(await screen.findByText(/not quite/i)).toBeInTheDocument()
    expect(screen.getByText(/Accrual recognises revenue/i)).toBeInTheDocument()
    // The correct option is flagged from the SERVER's response.
    expect(screen.getByText(/^Correct$/)).toBeInTheDocument()
  })
})

describe('Mock exams', () => {
  it('lists papers and marks a locked one as part of a plan rather than hiding it', async () => {
    stubRoutes([
      ['/mock-attempts', envelope([])],
      ['/mocks', {
        data: [
          {
            id: 'm-1',
            courseId: 'c-1',
            subjectId: null,
            title: 'Foundation Paper 1 — chapter practice',
            kind: 'CHAPTER',
            durationMin: 30,
            totalMarks: 8,
            isPremium: false,
            locked: false,
            questionCount: 4,
          },
          {
            id: 'm-2',
            courseId: 'c-1',
            subjectId: null,
            title: 'Full-length mock',
            kind: 'FULL_LENGTH',
            durationMin: 180,
            totalMarks: 100,
            isPremium: true,
            locked: true,
            questionCount: 100,
          },
        ],
        meta: { requestId: 't', total: 2, page: 1, limit: 20, hasMore: false },
      }],
    ])

    renderAt('/mocks', <MocksPage />)

    expect(await screen.findByText(/Foundation Paper 1/)).toBeInTheDocument()
    // A premium paper is SHOWN, with its lock explained. Hiding it gives a student
    // no reason to upgrade.
    expect(screen.getByText(/Full-length mock/)).toBeInTheDocument()
    expect(screen.getByText(/Included in a paid plan/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /start paper/i })).toHaveAttribute(
      'href',
      '/mocks/m-1',
    )
  })

  it('reports every outcome, including the two a binary report would lose', async () => {
    stubRoutes([
      [
        '/mock-attempts/att-1/report',
        envelope({
          attemptId: 'att-1',
          mockTestId: 'm-1',
          title: 'Foundation Paper 1',
          kind: 'CHAPTER',
          status: 'SUBMITTED',
          submittedAt: '2026-09-25T10:00:00Z',
          autoSubmitted: false,
          timeTakenSeconds: 600,
          score: 2,
          maxScore: 6,
          correct: 1,
          wrong: 1,
          unattempted: 1,
          pendingReview: 1,
          questions: [
            {
              questionId: 'q-1',
              text: 'Accrual concept?',
              marks: 2,
              difficulty: 'EASY',
              chosenOption: 1,
              correctOption: 1,
              outcome: 'CORRECT',
              explanation: null,
              options: [
                { label: 'A', text: 'Cash', isCorrect: false },
                { label: 'B', text: 'Accrual', isCorrect: true },
              ],
            },
            {
              questionId: 'q-2',
              text: 'Depreciation treatment?',
              marks: 2,
              difficulty: 'MEDIUM',
              chosenOption: 0,
              correctOption: 2,
              outcome: 'WRONG',
              explanation: 'Charged to the profit and loss account.',
              options: [
                { label: 'A', text: 'Capitalised', isCorrect: false },
                { label: 'B', text: 'Ignored', isCorrect: false },
                { label: 'C', text: 'Charged to P&L', isCorrect: true },
              ],
            },
            {
              questionId: 'q-3',
              text: 'Discuss the going-concern assumption.',
              marks: 1,
              difficulty: 'HARD',
              chosenOption: 1,
              correctOption: null,
              outcome: 'PENDING_REVIEW',
              explanation: null,
              options: [],
            },
          ],
        }),
      ],
    ])

    renderAt('/reports/att-1', <ReportPage />, '/reports/:attemptId')

    expect(await screen.findByText('2/6')).toBeInTheDocument()
    // The per-question outcomes, which a "correct or wrong" report would collapse:
    // these three badges are the taxonomy, and each appears exactly once here.
    expect(screen.getByText('correct')).toBeInTheDocument()
    expect(screen.getByText('wrong')).toBeInTheDocument()
    expect(screen.getByText('pending review')).toBeInTheDocument()
    // The tally counts the un-attempted question separately from a wrong answer.
    expect(screen.getByText('Wrong')).toBeInTheDocument()
    expect(screen.getByText(/left blank/i)).toBeInTheDocument()
    expect(screen.getByText(/awaiting a human/i)).toBeInTheDocument()
    expect(screen.getByText(/Charged to the profit and loss account/i)).toBeInTheDocument()
  })
})

describe('Search', () => {
  it('renders a highlighted snippet as text, so markup in a question cannot execute', async () => {
    stubRoutes([
      [
        '/search',
        envelope({
          query: 'account',
          count: 1,
          results: [
            {
              id: 'q-9',
              // A question whose own text contains a tag: the server wraps the
              // match in <b>, and everything else must stay literal.
              snippet:
                "A cheque marked '<b>account</b> payee only' <script>alert('x')</script>",
              text: "A cheque marked 'account payee only'",
              questionType: 'MCQ',
              difficulty: 'MEDIUM',
              marks: 2,
              subjectId: 's-2',
              subjectName: 'Corporate and Other Laws',
              chapterId: 'ch-2',
              chapterName: 'The Negotiable Instruments Act, 1881',
              rank: 0.06,
            },
          ],
        }),
      ],
    ])

    const user = userEvent.setup()
    renderAt('/search', <SearchPage />)

    await user.type(screen.getByLabelText(/search the question bank/i), 'account')
    await user.click(screen.getByRole('button', { name: /^search$/i }))

    await waitFor(() => expect(screen.getByText(/1 result/i)).toBeInTheDocument())
    // The emphasis the server added is rendered...
    expect(screen.getByText('account', { selector: 'mark' })).toBeInTheDocument()
    // ...and the script tag is displayed as characters, not parsed.
    const script = document.querySelector('script')
    expect(script).toBeNull()
    expect(document.body.textContent).toContain("<script>alert('x')</script>")
  })
})
