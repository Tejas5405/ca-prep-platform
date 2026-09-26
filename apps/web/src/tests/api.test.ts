/**
 * Tests for the API client's error handling.
 *
 * This is the highest-value frontend test surface: the error path is where the
 * backend's RFC 7807 contract meets the UI, and it is the part nobody exercises
 * manually. A field error that silently fails to reach a form looks like "the
 * form does nothing".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api } from '../lib/api'

// The client reads the current session to attach a token. Mocking the Supabase
// module keeps these tests offline; a real client would try to reach the project
// and, on a page load with no stored session, silently send no token at all --
// which is exactly the assertion below.
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

/**
 * Await a call that is expected to fail and return the error, typed.
 *
 * `api.get<T>()` resolves to T, so a `.catch()` returns `T | ApiError` and every
 * assertion site would need a cast. Narrowing once here keeps the tests readable
 * and keeps the assertion honest — if the call unexpectedly succeeds, the
 * `expect(error).toBeInstanceOf(ApiError)` below fails loudly rather than
 * silently testing nothing.
 */
async function expectFailure(promise: Promise<unknown>): Promise<ApiError> {
  const result = await promise.then(
    () => null,
    (error: unknown) => error,
  )
  expect(result).toBeInstanceOf(ApiError)
  return result as ApiError
}

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

describe('api client', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('attaches a bearer token to authenticated requests', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ data: [], meta: {} }))
    vi.stubGlobal('fetch', fetchMock)

    await api.get('/mocks')

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect((init.headers as Record<string, string>).Authorization).toBe(
      'Bearer test-access-token',
    )
  })

  it('unwraps the documented list envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({
          data: [{ id: 'm1' }],
          meta: { requestId: 'r1', total: 1, page: 1, limit: 20, hasMore: false },
        }),
      ),
    )

    const result = await api.get<{ data: unknown[]; meta: { total: number } }>('/mocks')
    expect(result.meta.total).toBe(1)
    expect(result.data).toHaveLength(1)
  })

  it('throws an ApiError carrying the RFC 7807 body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            type: 'https://api.caprep.in/errors/validation',
            title: 'Validation Failed',
            status: 422,
            detail: 'One or more fields were rejected',
            errors: [{ field: 'marks', message: 'must be positive' }],
          },
          422,
        ),
      ),
    )

    await expect(api.post('/planner/generate', {})).rejects.toThrow(ApiError)
  })

  it('exposes field errors as a map so a form can render them inline', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            type: 't',
            title: 'Validation Failed',
            status: 422,
            errors: [
              { field: 'exam_date', message: 'must be in the future' },
              { field: 'daily_hours', message: 'must be positive' },
            ],
          },
          422,
        ),
      ),
    )

    const error = await expectFailure(api.post('/planner/generate', {}))

    expect(error.fieldErrors).toEqual({
      exam_date: 'must be in the future',
      daily_hours: 'must be positive',
    })
    expect(error.isValidationError).toBe(true)
  })

  it('distinguishes an expired session from a permission failure', async () => {
    // These need different UI: 401 means sign in again, 403 means you cannot.
    const make = (status: number) =>
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ type: 't', title: 'x', status, detail: 'nope' }, status),
        )

    vi.stubGlobal('fetch', make(401))
    const unauth = await expectFailure(api.get('/mocks'))
    expect(unauth.isAuthError).toBe(true)
    expect(unauth.isForbidden).toBe(false)

    vi.stubGlobal('fetch', make(403))
    const forbidden = await expectFailure(api.get('/mocks'))
    expect(forbidden.isForbidden).toBe(true)
    expect(forbidden.isAuthError).toBe(false)
  })

  it('surfaces the request id so a failure can be traced in logs', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({ type: 't', title: 'boom', status: 500 }, 500, {
          'X-Request-Id': 'abc123',
        }),
      ),
    )

    const error = await expectFailure(api.get('/mocks'))
    expect(error.requestId).toBe('abc123')
  })

  it('still throws a usable ApiError when the body is not JSON', async () => {
    // A proxy or gateway error page is HTML, not our error shape. Parsing must
    // not mask the real status code behind a JSON syntax error.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('<html>502 Bad Gateway</html>', { status: 502 })),
    )

    const error = await expectFailure(api.get('/mocks'))
    expect(error.status).toBe(502)
    expect(error.problem).toBeNull()
  })

  it('falls back to a generic message when detail is absent', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ type: 't', title: 'Forbidden', status: 403 }, 403)),
    )
    const error = await expectFailure(api.get('/mocks'))
    expect(error.message).toBe('Forbidden')
  })

  it('returns undefined for a 204 rather than trying to parse a body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))
    await expect(api.delete('/mocks/1')).resolves.toBeUndefined()
  })

  it('serialises the request body as JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ data: {}, meta: {} }))
    vi.stubGlobal('fetch', fetchMock)

    await api.post('/planner/generate', { daily_hours: 4 })

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(init.body).toBe(JSON.stringify({ daily_hours: 4 }))
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json')
  })

  it('does not send a body on a GET', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ data: [], meta: {} }))
    vi.stubGlobal('fetch', fetchMock)

    await api.get('/mocks')

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(init.body).toBeUndefined()
  })

  it('targets the /api/v1 prefix', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ data: [], meta: {} }))
    vi.stubGlobal('fetch', fetchMock)

    await api.get('/mocks?page=1')

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('/api/v1/mocks?page=1')
  })
})
