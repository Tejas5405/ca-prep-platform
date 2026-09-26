/**
 * Role definitions, shared by the UI and the API client.
 *
 * Kept out of the auth context module on purpose: a file that exports both React
 * components and plain functions breaks React Fast Refresh, so every edit to the
 * role helpers would force a full page reload during development.
 *
 * These MUST stay in sync with `Role` in `apps/api/app/core/security.py`.
 * Divergence is not a runtime error — a role the backend understands but the
 * client does not would simply be treated as STUDENT in the UI, which fails
 * safe but silently. If a role is added there, add it here in the same change.
 */

export type Role =
  | 'STUDENT'
  | 'EDITOR'
  | 'CONTENT_MANAGER'
  | 'MODERATOR'
  | 'ADMIN'
  | 'SUPER_ADMIN'

/** Rank order for "at least this role" checks. Mirrors ROLE_RANK in security.py. */
export const ROLE_RANK: Record<Role, number> = {
  STUDENT: 0,
  EDITOR: 1,
  CONTENT_MANAGER: 2,
  MODERATOR: 3,
  ADMIN: 4,
  SUPER_ADMIN: 5,
}

export function isRole(value: unknown): value is Role {
  return typeof value === 'string' && value in ROLE_RANK
}

/**
 * UI convenience only. NEVER use this to gate a request — the backend
 * re-reads the role from the verified token and enforces it there. Hiding a
 * button does not protect an endpoint.
 */
export function hasRole(role: Role, minimum: Role): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[minimum]
}
