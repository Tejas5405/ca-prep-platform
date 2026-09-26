/**
 * API client.
 *
 * Attaches the Supabase access token to every request and normalises the
 * backend's RFC 7807 error shape into a throwable.
 *
 * TOKEN FRESHNESS: `getSession()` returns the REMEMBERED session and refreshes the
 * access token in the background when it is close to expiry, so the token sent
 * here is current. The alternatives are both worse: `getUser()` makes a network
 * call to the Auth server on every API request, and capturing a token once at
 * sign-in produces intermittent 401s an hour later.
 */

import { supabase } from './supabase'

/** Base URL. Empty by default so requests are origin-relative (see vite.config.ts proxy). */
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''
const API_PREFIX = '/api/v1'

/** Backend error shape, blueprint v3 §7.2. */
export interface ApiFieldError {
  field: string
  message: string
  type?: string
}

export interface ApiProblem {
  type: string
  title: string
  status: number
  detail?: string
  errors?: ApiFieldError[]
}

/** Envelope shapes. */
export interface ApiMeta {
  requestId: string
}

export interface ApiListMeta extends ApiMeta {
  total: number
  page: number
  limit: number
  hasMore: boolean
}

export interface ApiSuccess<T> {
  data: T
  meta: ApiMeta
}

export interface ApiList<T> {
  data: T[]
  meta: ApiListMeta
}

/**
 * A failed API call.
 *
 * `isAuthError` exists so callers do not have to string-match on status codes:
 * a 401 means the session is gone and the user should be signed out, whereas a
 * 403 means they are signed in but lack permission — a different message and a
 * different remedy.
 */
export class ApiError extends Error {
  readonly status: number
  readonly problem: ApiProblem | null
  readonly requestId: string | null

  constructor(status: number, problem: ApiProblem | null, requestId: string | null) {
    super(problem?.detail ?? problem?.title ?? `Request failed with status ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.problem = problem
    this.requestId = requestId
  }

  get isAuthError(): boolean {
    return this.status === 401
  }

  get isForbidden(): boolean {
    return this.status === 403
  }

  get isValidationError(): boolean {
    return this.status === 422 || Boolean(this.problem?.errors?.length)
  }

  /** Field errors as a map, so a form can render them inline. */
  get fieldErrors(): Record<string, string> {
    const map: Record<string, string> = {}
    for (const err of this.problem?.errors ?? []) {
      map[err.field] = err.message
    }
    return map
  }
}

async function getAuthHeader(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token
  if (!token) return {}
  return { Authorization: `Bearer ${token}` }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  body?: unknown
  /** Set false for public endpoints to avoid an unnecessary token fetch. */
  authenticated?: boolean
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, authenticated = true, signal } = options

  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (authenticated) Object.assign(headers, await getAuthHeader())

  // Build RequestInit conditionally. `exactOptionalPropertyTypes` treats
  // `body: undefined` as a type error against `BodyInit | null`, so the keys are
  // spread in only when they have a value.
  const init: RequestInit = { method, headers }
  if (body !== undefined) init.body = JSON.stringify(body)
  if (signal) init.signal = signal

  const response = await fetch(`${API_BASE_URL}${API_PREFIX}${path}`, init)

  if (response.status === 204) {
    return undefined as T
  }

  const requestId = response.headers.get('X-Request-Id')

  if (!response.ok) {
    // The backend answers with an RFC 7807 body on every error path. If it did
    // not (a proxy error page, for instance), fall back to a null problem rather
    // than throwing a JSON parse error that hides the real status code.
    const problem = await response
      .json()
      .then((body: unknown) => body as ApiProblem)
      .catch(() => null)

    throw new ApiError(response.status, problem, requestId)
  }

  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) =>
    request<T>(path, { method: 'GET', ...(signal ? { signal } : {}) }),

  /**
   * A GET that carries no credentials.
   *
   * For the endpoints that are genuinely public - the pricing catalogue is the one
   * that matters, because the pricing page is read by people who have not signed up
   * and asking Supabase for a session there is a wasted round trip on the most
   * important page in the funnel. Deliberately a separate method rather than a
   * boolean on `get`: a flag on the default path is how an authenticated call gets
   * sent without its token by accident.
   */
  publicGet: <T>(path: string, signal?: AbortSignal) =>
    request<T>(path, { method: 'GET', authenticated: false, ...(signal ? { signal } : {}) }),

  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', ...(body === undefined ? {} : { body }) }),

  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', ...(body === undefined ? {} : { body }) }),

  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', ...(body === undefined ? {} : { body }) }),

  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
