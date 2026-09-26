/**
 * The application role, read from the server-set metadata bag.
 *
 * Sits in its own module for two reasons. It is a security-relevant rule and
 * deserves to be findable by name — `grep roleFromUser src/` is how a reviewer
 * should be able to answer "where does the UI get a role from?". And a module
 * exporting both a React component and a plain function breaks React Fast
 * Refresh, so keeping it beside the provider would force a full reload on every
 * edit to either.
 *
 * TWO BAGS OF METADATA, ONE OF THEM DANGEROUS
 *
 * `user_metadata` is writable by the authenticated user: `updateUser({ data: { role:
 * 'ADMIN' } })` puts whatever they like in there and the token stays validly
 * signed. `app_metadata` can only be written server-side, through the Admin API.
 * The role is read from `app_metadata` and NOWHERE else.
 *
 * An unrecognised or absent value resolves to STUDENT, the least privileged role,
 * which is the same rule the backend applies in
 * `SupabaseTokenVerifier.extract_role`. Note the trap: an authenticated Supabase
 * user's token carries a top-level `role: "authenticated"`, which is not one of our
 * roles and must degrade rather than grant.
 *
 * UI CONVENIENCE ONLY. This decides which links to show. It never decides what an
 * endpoint will do — the backend re-reads the same claim from the verified token on
 * every request, and hiding a button protects nothing.
 */

import type { User } from '@supabase/supabase-js'

import { isRole, type Role } from './roles'

export function roleFromUser(user: User | null): Role {
  const appMetadata = user?.app_metadata as Record<string, unknown> | undefined
  const raw = appMetadata?.role
  return isRole(raw) ? raw : 'STUDENT'
}
