/**
 * The public origin, in one place.
 *
 * Canonical URLs, Open Graph URLs and the sitemap all have to agree on one origin
 * or a search engine treats them as different pages. Read from Vite's env so
 * production, preview deployments and local development each advertise themselves
 * rather than all claiming to be localhost.
 *
 * The fallback is the production origin rather than localhost, deliberately: a
 * build that forgot to set the variable should describe the real site, not ship
 * `http://localhost:5173` into a canonical tag.
 */
export const SITE_URL: string = (import.meta.env.VITE_SITE_URL as string | undefined) ?? 'https://caprep.in'

/** Absolute URL for a workspace asset, for use in metadata. */
export function absoluteUrl(path: string): string {
  return `${SITE_URL}${path.startsWith('/') ? path : `/${path}`}`
}

/**
 * The support address shown on the legal pages.
 *
 * Configurable rather than hardcoded in two pages: a legal document with a contact
 * address that bounces is worse than one that points at a supported channel, and
 * the address is a deployment decision, not a copy decision.
 */
export const SUPPORT_EMAIL: string =
  (import.meta.env.VITE_SUPPORT_EMAIL as string | undefined) ?? 'support@caprep.in'
