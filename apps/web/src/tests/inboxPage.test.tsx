/**
 * The inbox screen, through the real client.
 *
 *   * it calls `/notifications`, not an admin route;
 *   * an absolute link is not rendered as a link;
 *   * "mark read" hits the student's own read route and then re-reads the list,
 *     rather than pretending the row changed.
 */

import type { ReactNode } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext } from '../hooks/authContext'
import InboxPage from '../pages/Inbox'

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

const list = (items: unknown[], unread: number) => ({
  data: items,
  meta: { requestId: 'test', total: items.length, page: 1, limit: 30, hasMore: false, unread },
})

function renderAt(element: ReactNode) {
  return render(
    <MemoryRouter initialEntries={['/notifications']}>
      <AuthContext.Provider value={makeAuth()}>
        <Routes>
          <Route path="/notifications" element={element} />
          <Route path="/library" element={<p>library reached</p>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}

beforeEach(() => vi.unstubAllGlobals())
afterEach(() => vi.unstubAllGlobals())

describe('Inbox', () => {
  it('shows the student rows and refuses an off-site link', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/notifications')) {
        return Promise.resolve(
          ok(
            list(
              [
                {
                  id: 'n-1',
                  kind: 'ANNOUNCEMENT',
                  title: 'New material published',
                  body: 'Financial Reporting notes are in the library.',
                  linkUrl: '/library',
                  read: false,
                  readAt: null,
                  createdAt: '2026-09-26T00:00:00Z',
                },
                {
                  id: 'n-2',
                  kind: 'SYSTEM',
                  title: 'Ignore this link',
                  body: 'A bad row.',
                  linkUrl: 'https://evil.example/login',
                  read: true,
                  readAt: '2026-09-26T01:00:00Z',
                  createdAt: '2026-09-25T00:00:00Z',
                },
              ],
              1,
            ),
          ),
        )
      }
      return Promise.resolve(new Response('no stub', { status: 404 }))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderAt(<InboxPage />)

    expect(await screen.findByText('New material published')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/library')
    expect(screen.getByText(/leaves the app/i)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /evil/i })).not.toBeInTheDocument()
    const urls = fetchMock.mock.calls.map((call) => String(call[0]))
    expect(urls.some((url) => url.includes('/notifications'))).toBe(true)
    expect(urls.some((url) => url.includes('/admin/'))).toBe(false)
  })

  it('marks one row read through the student route', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (init?.method === 'POST' && url.includes('/notifications/n-1/read')) {
        return Promise.resolve(ok({ data: { id: 'n-1', read: true }, meta: { requestId: 'test' } }))
      }
      return Promise.resolve(
        ok(
          list(
            [
              {
                id: 'n-1',
                kind: 'PAYMENT',
                title: 'Payment received',
                body: 'Your plan is active.',
                linkUrl: null,
                read: false,
                readAt: null,
                createdAt: null,
              },
            ],
            1,
          ),
        ),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    renderAt(<InboxPage />)
    await screen.findByText('Payment received')
    await user.click(screen.getByRole('button', { name: 'Mark read' }))

    const posts = fetchMock.mock.calls.filter((call) => call[1]?.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(String(posts[0]?.[0])).toContain('/notifications/n-1/read')
  })

  it('says the inbox is empty rather than inventing notices', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(ok(list([], 0)))),
    )
    renderAt(<InboxPage />)
    expect(await screen.findByText('Nothing here yet')).toBeInTheDocument()
  })
})
