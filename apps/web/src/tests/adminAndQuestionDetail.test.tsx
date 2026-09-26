/**
 * Tests for the content team's screen and for opening one question.
 *
 * THESE ARE THE TWO PLACES WHERE THE API COULD DO SOMETHING THE APP COULD NOT.
 *
 * The ingestion chain (signed upload → job → drafts → review → publish) and
 * `GET /questions/{id}` both existed and were tested on the server, and neither was
 * reachable from the browser: the pipeline could only be driven with curl, and a
 * search result could only be displayed, never opened. A green API suite does not
 * catch that - it is the same class of bug as a list endpoint returning an empty
 * page, one layer up.
 *
 * So what is asserted here is the wiring, including the parts that are easy to get
 * subtly wrong:
 *
 *   * the role gate shows a refusal instead of a screen full of 403s, AND the gated
 *     screen makes no requests at all for a student;
 *   * approving sends the PLACEMENT to the server, because a question filed under
 *     nothing reaches no student and looks completely fine;
 *   * publishing is offered only to a Content Manager, and only after the promote
 *     call has returned the id of the question it created;
 *   * the worked answer is shown for a question the student has answered, and
 *     explicitly NOT shown - with a sentence saying why - for one they have not.
 *
 * `fetch` is stubbed rather than the query helpers, so the URLs, the envelope and
 * the status handling are all exercised; a mistyped query parameter is exactly the
 * kind of bug a mocked helper would hide.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext } from '../hooks/authContext'
import { stubSignedUploads } from './support/signedUpload'
import AdminPage from '../pages/Admin'
import SearchPage from '../pages/SearchPage'
import type { Role } from '../lib/roles'

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
const list = <T,>(data: T) => ({ data, meta: { requestId: 'test', total: 1, page: 1, limit: 20, hasMore: false } })

/** Route responses by URL substring, and record every call for assertions. */
function stubRoutes(routes: [string, unknown][]) {
  const calls: { url: string; method: string; body: unknown }[] = []
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'
    let body: unknown = null
    if (typeof init?.body === 'string') {
      try {
        body = JSON.parse(init.body)
      } catch {
        body = init.body
      }
    }
    calls.push({ url, method, body })
    for (const [needle, response] of routes) {
      if (url.includes(needle)) return Promise.resolve(ok(response))
    }
    return Promise.resolve(
      new Response(JSON.stringify({ title: `no stub for ${url}`, status: 404 }), { status: 404 }),
    )
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

function renderAs(role: Role, element: React.ReactNode) {
  return render(
    <MemoryRouter>
      <AuthContext.Provider value={makeAuth({ role })}>{element}</AuthContext.Provider>
    </MemoryRouter>,
  )
}

const EDITOR_ROUTES: [string, unknown][] = [
  ['/ingestion/jobs', list([])],
  ['/ingestion/drafts', list([])],
]

describe('the content team’s screen', () => {
  it('refuses a student, and asks the API for nothing', async () => {
    const { calls } = stubRoutes(EDITOR_ROUTES)

    renderAs('STUDENT', <AdminPage />)

    expect(await screen.findByText(/editors only/i)).toBeInTheDocument()
    // The point of the gate: no ingestion request is attempted. A screen that
    // renders three 403 panels is a gate that failed slowly.
    expect(calls).toHaveLength(0)
  })

  it('shows the queues to an editor, with a job’s failure visible in the list', async () => {
    stubRoutes([
      [
        '/ingestion/jobs',
        list([
          {
            jobId: 'job-1',
            stage: 'FAILED',
            bucket: 'source-pdfs',
            storagePath: 'uploads/2026/may-r2.pdf',
            draftsCreated: 0,
            pageCount: 42,
            extractionTier: 1,
            needsManualReview: false,
            errorReason: 'OCR produced no text on 12 pages',
            isTerminal: true,
            createdAt: '2026-05-02T10:00:00Z',
            startedAt: '2026-05-02T10:00:05Z',
            finishedAt: '2026-05-02T10:02:00Z',
          },
        ]),
      ],
      ['/ingestion/drafts', list([])],
    ])

    renderAs('EDITOR', <AdminPage />)

    // The filename, not the storage path: an operator reads papers, not keys.
    expect(await screen.findByText('may-r2.pdf')).toBeInTheDocument()
    expect(screen.getByText(/42 pages/i)).toBeInTheDocument()
    // A failed job says WHY in the queue, without opening the job.
    expect(screen.getByText(/no text on 12 pages/i)).toBeInTheDocument()
    // Scoped to the badge: the stage filter lists the same word as an <option>.
    expect(screen.getByText('failed', { selector: 'span' })).toBeInTheDocument()
    expect(screen.getByText(/queue is clear/i)).toBeInTheDocument()
  })

  it('does not offer publishing to an editor, only to a content manager', async () => {
    const draft = {
      draftId: 'd-1',
      jobId: 'job-1',
      reviewStatus: 'PENDING',
      preview: 'Explain the treatment of a capital asset acquired by way of a gift.',
      sourcePage: 3,
      detectedYear: 2025,
      detectedAttempt: null,
      detectedMarks: 4,
      detectedQuestionType: 'DESCRIPTIVE',
      detectionConfidence: 0.71,
      createdAt: '2026-05-02T10:00:00Z',
    }

    stubRoutes([
      ['/ingestion/jobs', list([])],
      ['/ingestion/drafts', list([draft])],
    ])

    renderAs('EDITOR', <AdminPage />)

    await screen.findByText(/treatment of a capital asset/i)
    // Approving is how a Content Manager signs content off, so an Editor is not
    // offered the control at all rather than being offered one that 403s.
    const approve = screen.getByRole('button', { name: /approve/i })
    expect(approve).toBeDisabled()
    expect(screen.queryByRole('button', { name: /publish this question/i })).not.toBeInTheDocument()
  })

  it('sends the placement when approving, then offers the publish the server gated', async () => {
    const user = userEvent.setup()
    const { calls } = stubRoutes([
      // Order matters: the stub matches on substring, so the review endpoint has to
      // come before the drafts LIST or `/drafts/d-1/review` resolves to the list
      // envelope and the approve silently "succeeds" with no question id.
      [
        '/ingestion/drafts/d-1/review',
        envelope({ draftId: 'd-1', questionId: 'q-77', reviewStatus: 'APPROVED', questionStatus: 'DRAFT', published: false }),
      ],
      ['/ingestion/jobs', list([])],
      ['/ingestion/drafts', list([DRAFT])],
      ['/curriculum/courses', envelope({ courses: [{ id: 'c-1', code: 'FINAL', name: 'CA Final', level: 'FINAL', syllabusScheme: 'NEW_2024', description: null }] })],
      [
        '/curriculum/subjects',
        envelope({
          subjects: [
            { id: 's-1', courseId: 'c-1', code: 'P1', name: 'Financial Reporting', groupName: null, paperNumber: 1, syllabusWeight: null, chapterCount: 3 },
          ],
        }),
      ],
      ['/curriculum/chapters', envelope({ chapters: [{ id: 'ch-1', subjectId: 's-1', code: 'C1', name: 'Consolidation', sequence: 1, weightage: null, estimatedMinutes: null, topicCount: 2 }] })],
      ['/admin/questions/q-77/publish', envelope({ questionId: 'q-77', status: 'PUBLISHED', verifiedBy: 'u-1' })],
    ])

    renderAs('CONTENT_MANAGER', <AdminPage />)

    await user.click(await screen.findByRole('button', { name: /approve/i }))
    await user.selectOptions(await screen.findByLabelText(/course/i), 'c-1')
    await user.selectOptions(await screen.findByLabelText(/paper/i), 's-1')
    await user.selectOptions(await screen.findByLabelText(/chapter/i), 'ch-1')

    const create = screen.getByRole('button', { name: /approve and create the question/i })
    expect(create).toBeEnabled()
    await user.click(create)

    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('/drafts/d-1/review'))).toBe(true),
    )
    const review = calls.find((call) => call.url.includes('/drafts/d-1/review'))
    expect(review?.method).toBe('POST')
    // THE ASSERTION THAT MATTERS: the placement travels with the approval. A
    // question created without a subject is unreachable from the syllabus, and the
    // screen would still say "approved".
    expect(review?.body).toMatchObject({
      decision: 'APPROVE',
      subject_id: 's-1',
      chapter_id: 'ch-1',
      question_type: 'DESCRIPTIVE',
      marks: 4,
    })

    // The promote returns an id; publishing that id is a second, deliberate act and
    // only a Content Manager is offered it.
    const publish = await screen.findByRole('button', { name: /publish this question/i })
    await user.click(publish)

    await waitFor(() => expect(calls.some((call) => call.url.includes('/admin/questions/q-77/publish'))).toBe(true))
    expect(await screen.findByText(/students can practise it now/i)).toBeInTheDocument()
  })

  it('starts the job the upload created, using the id from the upload response', async () => {
    const user = userEvent.setup()
    const storage = stubSignedUploads()
    const { calls } = stubRoutes([
      [
        '/ingestion/uploads',
        envelope({
          bucket: 'source-pdfs',
          path: 'uploads/2026/paper.pdf',
          uploadUrl: 'https://storage.test/object/upload/sign/source-pdfs/uploads/2026/paper.pdf?token=t',
          token: 't',
          expiresInSeconds: 120,
          jobId: 'job-9',
          jobCreated: true,
        }),
      ],
      ['/ingestion/jobs/job-9/start', envelope({ jobId: 'job-9', stage: 'QUEUED', status: 'started' })],
      ['/ingestion/jobs', list([])],
      ['/ingestion/drafts', list([])],
    ])

    renderAs('EDITOR', <AdminPage />)

    const file = new File(['%PDF-1.4'], 'paper.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText(/source pdf/i), file)
    await user.click(screen.getByRole('button', { name: /upload and extract/i }))

    await waitFor(() => expect(calls.some((call) => call.url.includes('/jobs/job-9/start'))).toBe(true))
    expect(await screen.findByText(/queued as job job-9/i)).toBeInTheDocument()

    // The bytes go to storage on the signed URL and NOT through the API: the upload is
    // a PUT to the URL the API minted, and it is the only request in the app that uses
    // XMLHttpRequest rather than fetch (progress reporting - see uploadToSignedUrl).
    expect(storage.uploads).toHaveLength(1)
    expect(storage.uploads[0]?.method).toBe('PUT')
    expect(storage.uploads[0]?.url).toContain('https://storage.test/object/upload/sign/source-pdfs/')
    expect(storage.uploads[0]?.contentType).toBe('application/pdf')
    // Nothing was sent to the API as a file body.
    expect(calls.some((call) => call.method === 'PUT')).toBe(false)
  })

  it('says so when the upload succeeded but no job was created', async () => {
    const user = userEvent.setup()
    stubSignedUploads()
    stubRoutes([
      [
        '/ingestion/uploads',
        envelope({
          bucket: 'source-pdfs',
          path: 'uploads/2026/paper.pdf',
          uploadUrl: 'https://storage.test/object/upload/sign/x?token=t',
          token: 't',
          expiresInSeconds: 120,
          // The job insert failed server-side. The file is in storage, so the
          // honest outcome is "uploaded, not queued" - not a success message.
          jobId: null,
          jobCreated: false,
        }),
      ],
      ['/ingestion/jobs', list([])],
      ['/ingestion/drafts', list([])],
    ])

    renderAs('EDITOR', <AdminPage />)

    await user.upload(
      screen.getByLabelText(/source pdf/i),
      new File(['%PDF-1.4'], 'paper.pdf', { type: 'application/pdf' }),
    )
    await user.click(screen.getByRole('button', { name: /upload and extract/i }))

    expect(await screen.findByText(/no ingestion job was created/i)).toBeInTheDocument()
  })
})

const DRAFT = {
  draftId: 'd-1',
  jobId: 'job-1',
  reviewStatus: 'PENDING',
  preview: 'Explain the treatment of a capital asset acquired by way of a gift.',
  sourcePage: 3,
  detectedYear: 2025,
  detectedAttempt: null,
  detectedMarks: 4,
  detectedQuestionType: 'DESCRIPTIVE',
  detectionConfidence: 0.71,
  createdAt: '2026-05-02T10:00:00Z',
}

const SEARCH_RESULT = {
  id: 'q-9',
  snippet: 'A cheque marked <b>account</b> payee only',
  text: 'A cheque marked account payee only',
  questionType: 'MCQ',
  difficulty: 'MEDIUM',
  marks: 2,
  subjectId: 's-2',
  subjectName: 'Corporate and Other Laws',
  chapterId: 'ch-2',
  chapterName: 'The Negotiable Instruments Act, 1881',
  rank: 0.06,
}

function detailResponse(overrides: Record<string, unknown>) {
  return envelope({
    id: 'q-9',
    text: 'A cheque marked account payee only is',
    questionType: 'MCQ',
    difficulty: 'MEDIUM',
    marks: 2,
    negativeMarks: 0,
    subjectId: 's-2',
    chapterId: 'ch-2',
    topicId: null,
    source: 'ICAI May 2025',
    isPremium: false,
    year: 2025,
    examSession: 'May 2025',
    syllabusScheme: 'NEW_2024',
    bookmarked: false,
    attempted: false,
    options: [
      { label: 'A', text: 'Not transferable' },
      { label: 'B', text: 'Transferable but not negotiable' },
    ],
    ...overrides,
  })
}

describe('opening a search result', () => {
  it('fetches the question only when it is opened, and hides the answer until it is earned', async () => {
    const user = userEvent.setup()
    const { calls } = stubRoutes([
      ['/search', envelope({ query: 'account', count: 1, results: [SEARCH_RESULT] })],
      ['/questions/q-9', detailResponse({})],
    ])

    renderAs('STUDENT', <SearchPage />)

    await user.type(screen.getByLabelText(/search the question bank/i), 'account')
    await user.click(screen.getByRole('button', { name: /^search$/i }))
    await screen.findByText(/1 result/i)

    // A page of twenty results must not fire twenty detail requests.
    expect(calls.filter((call) => call.url.includes('/questions/q-9'))).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: /^open$/i }))

    expect(await screen.findByText('Not transferable')).toBeInTheDocument()
    // The answer is absent, and the screen says WHY rather than leaving a gap.
    expect(screen.getByText(/worked answer appears once you have answered/i)).toBeInTheDocument()
    expect(screen.queryByText(/^answer$/)).not.toBeInTheDocument()
  })

  it('shows the key and the outcome for a question the student has answered', async () => {
    const user = userEvent.setup()
    stubRoutes([
      ['/search', envelope({ query: 'account', count: 1, results: [SEARCH_RESULT] })],
      [
        '/questions/q-9',
        detailResponse({
          attempted: true,
          yourAnswer: 'A',
          yourResult: false,
          reveal: {
            correctAnswer: 'B',
            explanation: 'A crossed cheque is transferable but not negotiable.',
            modelAnswer: null,
          },
        }),
      ],
    ])

    renderAs('STUDENT', <SearchPage />)

    await user.type(screen.getByLabelText(/search the question bank/i), 'account')
    await user.click(screen.getByRole('button', { name: /^search$/i }))
    await screen.findByText(/1 result/i)
    await user.click(screen.getByRole('button', { name: /^open$/i }))

    expect(await screen.findByText(/you got this wrong/i)).toBeInTheDocument()
    // The correct option is marked from the SERVER's key, and the student's own
    // wrong choice is marked as theirs - not recomputed in the browser.
    expect(screen.getByText('answer')).toBeInTheDocument()
    expect(screen.getByText('yours')).toBeInTheDocument()
    // `^...$` because the explanation sentence contains the same words; this
    // asserts the OPTION is rendered, not that the string appears somewhere.
    expect(screen.getByText(/^Transferable but not negotiable$/)).toBeInTheDocument()
    expect(screen.getByText(/crossed cheque/i)).toBeInTheDocument()
  })
})
