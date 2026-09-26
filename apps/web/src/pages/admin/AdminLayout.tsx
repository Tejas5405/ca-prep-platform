/**
 * The console layout: a grouped sidebar, a section header, and an outlet.
 *
 * WHY ROUTER-BASED SECTIONS RATHER THAN TABS
 *
 * Every section is a real URL (`/admin/access`, `/admin/audit`), so an operator can be
 * sent a link to the thing they need to look at, the browser's back button works, and a
 * refresh lands where they were. Tabs would have made all three of those worse for
 * about twenty lines less code.
 *
 * WHAT THE SIDEBAR SHOWS
 *
 * Every section in the spec's list, with the ones this deployment cannot serve shown as
 * disabled and labelled with what they need. A section the caller's ROLE cannot use is
 * disabled too, using the server's own permission list rather than a hardcoded guess -
 * `GET /admin/me/permissions` is fetched once here and passed down, so the sidebar and
 * the sections agree by construction.
 */

import { Suspense } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

import { Badge, ErrorState, Spinner } from '../../components/ui'
import { useAuth } from '../../hooks/authContext'
import { fetchMyPermissions } from '../../lib/queries'
import { useLoader } from '../../lib/useLoader'
import {
  ADMIN_GROUPS,
  ADMIN_SECTIONS,
  canOpenConsole,
  sectionsForGroup,
  type AdminSection,
} from './sections'

export default function AdminLayout() {
  const { role } = useAuth()
  const location = useLocation()
  const allowed = canOpenConsole(role)

  /**
   * The permission list, through the shared loader.
   *
   * WHY NOT A HAND-ROLLED `useEffect`
   *
   * The first version was one, and in a real browser it put a red "Could not load your
   * permissions - signal is aborted without reason" banner on EVERY console screen.
   * React 19 StrictMode mounts, unmounts and remounts, so the cleanup aborted the first
   * request - and the abort landed in `.catch`, which cannot tell "the component went
   * away" from "the server refused". The screen then rendered that as a failure.
   *
   * `useLoader` already solves exactly this: it checks `signal.aborted` before setting
   * state and ignores an aborted request, which is what makes a StrictMode remount a
   * non-event. Using it here also removes the second, hand-managed loading flag.
   */
  const mine = useLoader(
    (signal) => fetchMyPermissions(signal),
    [allowed],
    allowed,
  )

  // `null` while in flight, so the sidebar renders optimistically (everything
  // enabled) rather than flashing every row as disabled before the answer arrives.
  const permissions = mine.data ? new Set(mine.data.permissions) : null
  const isOwner = mine.data?.isOwner ?? false
  const error = mine.error

  if (!allowed) return <AdminNotAllowed role={role} />

  const current = sectionFromPath(location.pathname)
  const mayUse = (section: AdminSection) =>
    section.permission === null || permissions === null || permissions.has(section.permission)

  return (
    <div className="mx-auto max-w-7xl px-4 py-8">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Admin console
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">
            {current?.label ?? 'Admin'}
          </h1>
          {current && <p className="mt-1 max-w-2xl text-sm text-slate-600">{current.purpose}</p>}
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={isOwner ? 'brand' : 'slate'}>
            {isOwner ? 'Owner' : role.replace('_', ' ')}
          </Badge>
          {permissions === null && !error && <Badge tone="slate">Loading access…</Badge>}
        </div>
      </header>

      <div className="mt-8 grid gap-8 lg:grid-cols-[240px_1fr]">
        <nav aria-label="Admin sections" className="space-y-6">
          {ADMIN_GROUPS.map((group) => (
            <div key={group}>
              <h2 className="px-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                {group}
              </h2>
              <ul className="mt-2 space-y-1">
                {sectionsForGroup(group).map((section) => (
                  <li key={section.path}>
                    <SectionLink section={section} enabled={mayUse(section)} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <div className="min-w-0">
          {error && !error.isForbidden && !error.isAuthError && (
            <div className="mb-6">
              <ErrorState error={error} what="your permissions" />
            </div>
          )}
          {/* A spinner rather than a blank pane: the console is entered from a link and
              a half-second of nothing reads as a broken page. */}
          <Suspense fallback={<Spinner label="Loading section…" />}>
            <Outlet context={{ permissions, isOwner } satisfies OutletContext} />
          </Suspense>
        </div>
      </div>
    </div>
  )
}

/**
 * The refusal page, shown before any section loads.
 *
 * Deliberately says what the account IS rather than only what it lacks: the most common
 * version of this page is a content editor opening a console built for the owner, and
 * "you are an Editor" is the information that resolves it.
 */
function AdminNotAllowed({ role }: { role: string }) {
  return (
    <div className="mx-auto max-w-lg py-16 text-center">
      <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Editors only</h1>
      <p className="mt-2 text-slate-600">
        The admin console is for the content and operations team. Your account is a{' '}
        {role.toLowerCase()}.
      </p>
      <p className="mt-4 text-sm text-slate-500">
        This check only decides whether the screen renders. Every admin request is refused by
        the API for an account without the permission behind it.
      </p>
    </div>
  )
}

export interface OutletContext {
  permissions: Set<string> | null
  isOwner: boolean
}

/** The section whose path matches the current URL, for the header. */
function sectionFromPath(pathname: string): AdminSection | undefined {
  const rest = pathname.replace(/^\/admin\/?/, '')
  return ADMIN_SECTIONS.find((section) => section.path === rest)
}

/**
 * One sidebar row.
 *
 * A disabled section is a plain element, not a link, so a click does nothing rather
 * than navigating to a route that will refuse. The title attribute carries the reason,
 * which is where an operator looks when a row is greyed out.
 */
function SectionLink({ section, enabled }: { section: AdminSection; enabled: boolean }) {
  const base = 'block rounded-md px-2 py-1.5 text-sm transition-colors'
  if (!enabled) {
    return (
      <span
        className={`${base} cursor-not-allowed text-slate-400`}
        title={`Your role does not include ${section.permission ?? 'this section'}.`}
        aria-disabled="true"
      >
        {section.label}
      </span>
    )
  }
  if (section.planned) {
    return (
      <span
        className={`${base} cursor-help text-slate-400`}
        title={`Not built yet: ${section.planned}`}
        aria-disabled="true"
      >
        {section.label}
        <span className="ml-1 text-[10px] uppercase tracking-wide">soon</span>
      </span>
    )
  }
  return (
    <NavLink
      to={section.path === '' ? '/admin' : `/admin/${section.path}`}
      end={section.path === ''}
      className={({ isActive }) =>
        `${base} ${
          isActive
            ? 'bg-brand-50 font-medium text-brand-700'
            : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
        }`
      }
    >
      {section.label}
    </NavLink>
  )
}

