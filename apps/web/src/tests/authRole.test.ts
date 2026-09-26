/**
 * Tests for the client-side role read.
 *
 * This is a security property, not a formatting one. Supabase signs whatever
 * metadata is in the token, and `user_metadata` is WRITABLE BY THE USER:
 * `updateUser({ data: { role: 'ADMIN' } })` puts any string a student likes in
 * there and the token is still perfectly valid. The role therefore comes from
 * `app_metadata`, which only the backend can write, and never from anywhere else.
 *
 * The server-side counterpart is `test_security.py::TestRoleClaimCannotBeSelfAssigned`.
 * Both are needed: this one stops the UI from believing a self-assigned role, and
 * the backend one stops an endpoint from honouring it.
 */

import { describe, expect, it } from 'vitest'
import type { User } from '@supabase/supabase-js'

import { roleFromUser } from '../lib/roleFromUser'

/** A user with the given metadata bags, shaped like a Supabase session user. */
function userWith(metadata: Record<string, unknown>, app: Record<string, unknown> = {}): User {
  return {
    id: '00000000-0000-0000-0000-000000000001',
    aud: 'authenticated',
    app_metadata: app,
    user_metadata: metadata,
    created_at: '2026-01-01T00:00:00Z',
  } as unknown as User
}

describe('roleFromUser', () => {
  it('reads the role from app_metadata', () => {
    expect(roleFromUser(userWith({}, { role: 'ADMIN' }))).toBe('ADMIN')
    expect(roleFromUser(userWith({}, { role: 'EDITOR' }))).toBe('EDITOR')
  })

  it('IGNORES a role the student wrote into user_metadata', () => {
    // The privilege-escalation attempt this property exists to stop.
    const selfPromoted = userWith({ role: 'SUPER_ADMIN' }, {})
    expect(roleFromUser(selfPromoted)).toBe('STUDENT')
  })

  it('prefers app_metadata when both bags carry a role', () => {
    expect(roleFromUser(userWith({ role: 'SUPER_ADMIN' }, { role: 'STUDENT' }))).toBe('STUDENT')
  })

  it('resolves anything unrecognised to STUDENT, the least privileged role', () => {
    expect(roleFromUser(userWith({}, { role: 'admin' }))).toBe('STUDENT')
    expect(roleFromUser(userWith({}, { role: 42 }))).toBe('STUDENT')
    expect(roleFromUser(userWith({}, { role: null }))).toBe('STUDENT')
    expect(roleFromUser(userWith({}, {}))).toBe('STUDENT')
  })

  it('treats a signed-out visitor as a student, never as staff', () => {
    expect(roleFromUser(null)).toBe('STUDENT')
  })

  it('does not mistake Supabase’s own top-level metadata for an app role', () => {
    /*
     * The trap worth writing down: an authenticated Supabase user carries
     * `role: "authenticated"` in the JWT's TOP LEVEL, and the client library copies
     * some of those claims into `app_metadata`. "authenticated" is not one of our
     * roles, so it must degrade to STUDENT rather than being treated as a grant.
     */
    expect(roleFromUser(userWith({}, { provider: 'email', role: 'authenticated' }))).toBe('STUDENT')
  })
})
