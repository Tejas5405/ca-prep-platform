/**
 * The student's inbox.
 *
 * The list is already scoped to the caller: the server filters on the user id from
 * the verified token, and there is no parameter here that could ask for someone
 * else's rows. This module does not re-filter.
 *
 * `linkUrl` is an in-app path. The create route rejects an absolute URL, and this
 * side refuses one too — an old or hand-written row must not become a link off the
 * site, which is a phishing vector sitting in a surface that looks like email.
 */

import { api, type ApiList, type ApiSuccess } from './api'
import { QueryError } from './queries'

export interface InboxItem {
  id: string
  kind: string
  title: string
  body: string
  linkUrl: string | null
  read: boolean
  readAt: string | null
  createdAt: string | null
}

export interface InboxPage {
  items: InboxItem[]
  total: number
  unread: number
  hasMore: boolean
}

/** An in-app path, or null. `//evil.example` is an absolute URL wearing a slash. */
export function safeInboxPath(url: string | null): string | null {
  if (!url) return null
  if (!url.startsWith('/') || url.startsWith('//') || url.includes('://')) return null
  return url
}

export function fetchInbox(page = 1, signal?: AbortSignal) {
  const query = new URLSearchParams({ page: String(page), limit: '30' })
  return api
    .get<ApiList<InboxItem> & { meta: { unread?: number; hasMore: boolean; total: number } }>(
      `/notifications?${query}`,
      signal,
    )
    .then((body): InboxPage => ({
      items: body.data,
      total: body.meta.total,
      unread: body.meta.unread ?? 0,
      hasMore: body.meta.hasMore,
    }))
    .catch((error: unknown) => {
      throw new QueryError(error)
    })
}

export function markInboxRead(id: string) {
  return api
    .post<ApiSuccess<{ id: string; read: boolean }>>(
      `/notifications/${encodeURIComponent(id)}/read`,
    )
    .then((body) => body.data)
    .catch((error: unknown) => {
      throw new QueryError(error)
    })
}

export function markInboxAllRead() {
  return api
    .post<ApiSuccess<{ marked: number }>>('/notifications/read-all')
    .then((body) => body.data)
    .catch((error: unknown) => {
      throw new QueryError(error)
    })
}
