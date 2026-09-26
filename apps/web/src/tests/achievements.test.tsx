/**
 * The achievements screen, through the real client.
 *
 *   * it calls the student gamification routes, not the admin badge catalogue;
 *   * a badge the server cannot measure (`progress: null`) gets no bar — 0% would
 *     be a claim the API refused to make;
 *   * a disabled leaderboard is a named state, and it does not take the badges
 *     down with it;
 *   * a row is a name and a total. An email or a user id in the payload is not
 *     rendered, because the board is not an enumeration surface.
 */

import type { ReactNode } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext } from '../hooks/authContext'
import AchievementsPage from '../pages/Achievements'

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

function ok(data: unknown) {
  return new Response(JSON.stringify({ data, meta: { requestId: 'test' } }), {
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

const SUMMARY = { points: 40, badgesEarned: 1, badgesAvailable: 2, rank: 3, totalRanked: 10 }
const STREAK = { current: 2, longest: 4, activeDays: 4 }
const POINTS = {
  total: 40,
  lastSevenDays: 10,
  recent: [{ points: 10, reason: 'QUESTION_CORRECT', referenceId: null, createdAt: null }],
}
const BADGES = {
  earnedCount: 1,
  totalCount: 2,
  badges: [
    {
      code: 'FIRST_STEPS',
      name: 'First Steps',
      description: 'Answer one question.',
      icon: null,
      criteriaKind: 'QUESTIONS_ANSWERED',
      criteriaValue: 1,
      pointsReward: 5,
      earned: true,
      earnedAt: '2026-09-26T00:00:00Z',
      progress: 4,
    },
    {
      code: 'WEEK_STREAK',
      name: 'Seven Days Straight',
      description: 'Practise seven days running.',
      icon: null,
      criteriaKind: 'STREAK',
      criteriaValue: 7,
      pointsReward: 20,
      earned: false,
      earnedAt: null,
      progress: null,
    },
  ],
}

function board(window = 'week') {
  return {
    window,
    entries: [
      {
        rank: 1,
        name: 'A student',
        points: 80,
        avatarUrl: 'https://evil.example/pixel.png',
        isYou: false,
      },
      { rank: 2, name: 'You', points: 10, avatarUrl: null, isYou: true },
    ],
    you: { rank: 2, points: 10 },
  }
}

function renderAt(element: ReactNode, entry = '/achievements') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <AuthContext.Provider value={makeAuth()}>
        <Routes>
          <Route path="/achievements" element={element} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}

function stub(routes: [string, Response][]) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    for (const [needle, body] of routes) {
      // A fresh clone per call. Returning the same Response twice makes the second
      // read throw "Body has already been read", which the screen then shows as a
      // leaderboard failure — a test bug, not a product one.
      if (url.includes(needle)) return Promise.resolve(body.clone())
    }
    return Promise.resolve(problem(404, 'no stub', url))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => vi.unstubAllGlobals())
afterEach(() => vi.unstubAllGlobals())

describe('Achievements', () => {
  it('reads the student routes and does not draw a bar the server refused to measure', async () => {
    const fetchMock = stub([
      ['/gamification/achievements', ok(SUMMARY)],
      ['/gamification/streak', ok(STREAK)],
      ['/gamification/points', ok(POINTS)],
      ['/gamification/badges', ok(BADGES)],
      ['/gamification/leaderboard', ok(board())],
    ])

    renderAt(<AchievementsPage />)

    expect(await screen.findByText('First Steps')).toBeInTheDocument()
    expect(screen.getByText('Earned')).toBeInTheDocument()
    expect(screen.getByText(/not counted on this screen/i)).toBeInTheDocument()
    expect(screen.queryByRole('progressbar', { name: /7/ })).not.toBeInTheDocument()
    expect(screen.getByText('Correct answer')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
    expect(document.body.textContent).not.toContain('evil.example')
    expect(document.body.textContent).not.toContain('@')

    const urls = fetchMock.mock.calls.map((call) => String(call[0]))
    expect(urls.some((url) => url.includes('/gamification/badges'))).toBe(true)
    expect(urls.some((url) => url.includes('/admin/badges'))).toBe(false)
  })

  it('names a switched-off leaderboard without hiding the badges', async () => {
    stub([
      ['/gamification/achievements', ok(SUMMARY)],
      ['/gamification/streak', ok(STREAK)],
      ['/gamification/points', ok(POINTS)],
      ['/gamification/badges', ok(BADGES)],
      [
        '/gamification/leaderboard',
        problem(403, 'Leaderboard disabled', 'An administrator has turned the leaderboard off.'),
      ],
    ])

    renderAt(<AchievementsPage />)
    expect(await screen.findByText('Leaderboard is off')).toBeInTheDocument()
    expect(await screen.findByText('First Steps')).toBeInTheDocument()
  })

  it('asks for the window the student selected', async () => {
    const user = userEvent.setup()
    const fetchMock = stub([
      ['/gamification/achievements', ok(SUMMARY)],
      ['/gamification/streak', ok(STREAK)],
      ['/gamification/points', ok(POINTS)],
      ['/gamification/badges', ok(BADGES)],
      ['/gamification/leaderboard', ok(board('month'))],
    ])

    renderAt(<AchievementsPage />)
    await screen.findByText(/you are 2nd this week/i)
    await user.click(screen.getByRole('button', { name: 'This month' }))

    await screen.findByText(/you are 2nd this month/i)
    const urls = fetchMock.mock.calls.map((call) => String(call[0]))
    expect(urls.some((url) => url.includes('window=month'))).toBe(true)
  })
})
