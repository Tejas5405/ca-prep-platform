/**
 * The value, but only after it has stopped changing for `delayMs`.
 *
 * WHY NOT A TIMER PER SEARCH EFFECT
 *
 * The obvious version of an admin search box is an effect that clears and re-arms a
 * timer and calls setState from inside it. That works, but the timer, the abort
 * controller and the "clear the list when the box is emptied" branch all have to be
 * written again in every screen that has a search box - and the empty branch is the
 * one that usually sets state during the effect body, which is exactly the cascading
 * render the hooks lint rule rejects.
 *
 * Splitting it in two removes both problems: this hook only debounces a VALUE, and
 * `useLoader` only fetches a value. The screens then read
 *
 *   const term = useDebounced(input, 250)
 *   const results = useLoader((s) => search(term, s), [term], term.length >= 2)
 *
 * and "too short to search" is a render-time decision about `term`, not a state write.
 */

import { useEffect, useState } from 'react'

export function useDebounced<T>(value: T, delayMs: number): T {
  const [settled, setSettled] = useState(value)

  useEffect(() => {
    // Inside the timer callback, not in the effect body: the first render already has
    // `value`, and a synchronous set here would render twice for every keystroke.
    const handle = setTimeout(() => setSettled(value), delayMs)
    return () => clearTimeout(handle)
  }, [value, delayMs])

  return settled
}
