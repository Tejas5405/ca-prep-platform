/**
 * The inbox link rule, without a renderer: an absolute URL in a notification is a
 * phishing link wearing the app's chrome, and the check has to fail closed.
 */

import { describe, expect, it } from 'vitest'

import { safeInboxPath } from '../lib/inbox'

describe('safeInboxPath', () => {
  it('keeps an in-app path', () => {
    expect(safeInboxPath('/library/doc-1')).toBe('/library/doc-1')
    expect(safeInboxPath('/upgrade?plan=premium')).toBe('/upgrade?plan=premium')
  })

  it('drops anything that leaves the app', () => {
    expect(safeInboxPath('https://evil.example/login')).toBeNull()
    expect(safeInboxPath('//evil.example/login')).toBeNull()
    expect(safeInboxPath('javascript:alert(1)')).toBeNull()
    expect(safeInboxPath(null)).toBeNull()
  })
})
