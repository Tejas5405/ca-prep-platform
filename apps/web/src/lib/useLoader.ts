/**
 * Run an async loader on mount and whenever its dependencies change.
 *
 * WHY THIS IS NOT JUST `useEffect` IN EVERY SCREEN
 *
 * Hand-rolling it per screen is where the classic bugs come from: a missing
 * AbortController (state set after unmount, or a slow earlier response overwriting
 * a newer one), an aborted request rendered as an error, and a `loading` flag that
 * is never cleared on the error path. One hook means one place those are right.
 *
 * WHY `loading` IS DERIVED AND NOT STORED
 *
 * The obvious implementation sets `loading: true` at the top of the effect and
 * `false` in the promise handlers. That sets state during the effect body, which
 * triggers a second render before any request has been made - the compiler flags it
 * as a cascading render, and it is a real (if small) wasted render on every screen.
 * Instead the result is tagged with the KEY it was fetched for, and `loading` is
 * simply "the current key has no result yet". Same behaviour, one render, and a
 * stale response cannot be mistaken for a fresh one because the keys differ.
 *
 * The key is the caller's dependency list serialised. That is fine for the kinds
 * of values these loaders depend on - ids, page numbers, search terms - and a
 * dependency that cannot be serialised is not a dependency a loader should have.
 */

import { useEffect, useRef, useState } from 'react'

import type { QueryError } from './queries'

export interface LoaderState<T> {
  data: T | null
  error: QueryError | null
  loading: boolean
  /**
   * Re-run the loader after a write.
   *
   * Added for the admin console, where almost every action is "change something, then
   * re-read it": archive a document, revoke a grant, flip a feature flag, send a
   * broadcast. Without this the screens either hand-rolled a second `useEffect` (which
   * the hooks lint rule rejects for setting state in an effect body, correctly) or
   * kept a duplicate copy of the list in a separate `useState` that drifts from the
   * loader's.
   *
   * Implemented by bumping a nonce that is part of the loader key, so a reload goes
   * through exactly the same path as a dependency change - same abort handling, same
   * "loading is the key has no result yet" rule.
   */
  reload: () => void
}

export function useLoader<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
  enabled = true,
): LoaderState<T> {
  const [nonce, setNonce] = useState(0)
  const key = JSON.stringify([...deps, nonce])
  const [result, setResult] = useState<{
    key: string
    data: T | null
    error: QueryError | null
  } | null>(null)
  // The loader is usually an inline arrow, so its identity changes on every render
  // and it must not be a dependency - the caller's `deps` are the real trigger.
  //
  // Written in an effect rather than during render: assigning to a ref while
  // rendering is a side effect, and the React compiler rejects it for the same
  // reason a mutation of props would be. Effects run in declaration order, so this
  // assignment has happened before the loader effect below reads it.
  const loader = useRef(load)
  useEffect(() => {
    loader.current = load
  })

  useEffect(() => {
    if (!enabled) return

    const controller = new AbortController()
    let live = true

    loader
      .current(controller.signal)
      .then((data) => {
        if (live) setResult({ key, data, error: null })
      })
      .catch((error: unknown) => {
        if (!live || controller.signal.aborted) return
        setResult({ key, data: null, error: error as QueryError })
      })

    return () => {
      live = false
      controller.abort()
    }
  }, [key, enabled])

  return {
    data: result?.data ?? null,
    error: result?.error ?? null,
    reload: () => setNonce((current) => current + 1),
    // `!result` covers the first render; `result.key !== key` covers a dependency
    // change, which is exactly when a refetch is in flight.
    loading: enabled && (!result || result.key !== key),
  }
}
