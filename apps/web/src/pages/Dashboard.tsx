import { Link } from 'react-router-dom'

import { api, type ApiList } from '../lib/api'
import { useLoader } from '../lib/useLoader'
import { useAuth } from '../hooks/authContext'

interface MockTest {
  id: string
  title: string
  kind: string
  durationMin: number
  totalMarks: number
  isPremium: boolean
}

/**
 * Dashboard.
 *
 * Note what this page does NOT do: it does not compute entitlements, decide
 * whether the user may start a mock, or fall back to placeholder data on error.
 *
 * On errors it shows the failing request's id. That single detail turns "the app
 * is broken" into a log line an engineer can find, and it costs nothing.
 */
export default function DashboardPage() {
  const { user } = useAuth()
  // Through the shared loader: the abort-on-cleanup case is handled once, there,
  // instead of being recognised here as a DOMException by name. See lib/useLoader.ts
  // for why that matters under StrictMode, whose remount aborts the first request.
  const mocks = useLoader(
    (signal) => api.get<ApiList<MockTest>>('/mocks?page=1&limit=6', signal),
    [],
  )
  const loading = mocks.loading
  const error = mocks.error
  const recent = mocks.data?.data ?? []
  const meta = mocks.data?.meta ?? { hasMore: false, total: 0 }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Welcome back</h1>
        <p className="mt-1 text-slate-600">Signed in as {user?.email ?? 'a student'}.</p>
      </div>

      {error && (
        <div role="alert" className="rounded-lg border border-wrong-500/30 bg-wrong-50 p-4">
          <p className="font-medium text-wrong-500">Could not load your mock tests</p>
          <p className="mt-1 text-sm text-slate-700">{error.message}</p>
          {error.requestId && (
            <p className="mt-2 font-mono text-xs text-slate-500">Request id: {error.requestId}</p>
          )}
          {error.isAuthError && (
            <p className="mt-2 text-sm text-slate-700">
              Your session may have expired. Reload the page to sign in again.
            </p>
          )}
        </div>
      )}

      {/*
        THE TWO "COME BACK TO THIS" LISTS LIVE HERE, not in the top nav. They are
        things a student uses between sessions rather than every session - which is
        exactly what a dashboard is for. The library is the exception: it is in the
        nav AND here, because a PDF shelf that only exists as a card gets missed.
      */}
      <section aria-labelledby="your-inbox">
        <h2 id="your-inbox" className="text-lg font-medium text-slate-900">
          Inbox
        </h2>
        <div className="mt-3 rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="font-medium text-slate-900">
            <Link to="/notifications" className="hover:underline">
              Notices
            </Link>
          </h3>
          <p className="mt-1 text-sm text-slate-600">
            Announcements and account notices. They stay in the app — nothing is emailed.
          </p>
        </div>
      </section>

      <section aria-labelledby="assistant">
        <h2 id="assistant" className="text-lg font-medium text-slate-900">
          Library assistant
        </h2>
        <div className="mt-3 rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="font-medium text-slate-900">
            <Link to="/assistant" className="hover:underline">
              Ask the documents you can read
            </Link>
          </h3>
          <p className="mt-1 text-sm text-slate-600">
            A suggestion is written only from excerpts you may already read, and only when the assistant is on. It is not a legal authority. Without excerpts, nothing is invented.
          </p>
        </div>
      </section>

      <section aria-labelledby="achievements">
        <h2 id="achievements" className="text-lg font-medium text-slate-900">
          Achievements
        </h2>
        <div className="mt-3 rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="font-medium text-slate-900">
            <Link to="/achievements" className="hover:underline">
              Badges and the board
            </Link>
          </h3>
          <p className="mt-1 text-sm text-slate-600">
            Points from practice, the badges still to earn, and a leaderboard of names and totals —
            not email addresses.
          </p>
        </div>
      </section>

      <section aria-labelledby="your-library">
        <h2 id="your-library" className="text-lg font-medium text-slate-900">
          Your library
        </h2>
        <div className="mt-3 rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="font-medium text-slate-900">
            <Link to="/library" className="hover:underline">
              Study material
            </Link>
          </h3>
          <p className="mt-1 text-sm text-slate-600">
            The PDFs published for your plan, with the extracted text and the original file. Search
            looks inside the pages, not just the filenames.
          </p>
        </div>
      </section>

      <section aria-labelledby="kept-questions">
        <h2 id="kept-questions" className="text-lg font-medium text-slate-900">
          Questions you kept
        </h2>
        <ul className="mt-3 grid gap-4 sm:grid-cols-2">
          <li className="rounded-lg border border-slate-200 bg-white p-4">
            <h3 className="font-medium text-slate-900">
              <Link to="/collections" className="hover:underline">
                Collections
              </Link>
            </h3>
            <p className="mt-1 text-sm text-slate-600">
              Named groups you build yourself — a chapter you keep getting wrong, the questions a
              mentor set, the ones for the last week.
            </p>
          </li>
          <li className="rounded-lg border border-slate-200 bg-white p-4">
            <h3 className="font-medium text-slate-900">
              <Link to="/ldr" className="hover:underline">
                Marked for later
              </Link>
            </h3>
            <p className="mt-1 text-sm text-slate-600">
              Everything you flagged while practising, with how you did on it. Flagging happens
              where you answer, so the list stays a record.
            </p>
          </li>
        </ul>
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-medium text-slate-900">Mock exams</h2>
          <Link to="/planner" className="text-sm font-medium text-brand-600 hover:underline">
            Open study planner
          </Link>
        </div>

        {loading && <SkeletonCards count={3} />}

        {!loading && recent.length === 0 && (
          <div className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center">
            <p className="font-medium text-slate-700">No mock tests are published yet</p>
            <p className="mt-1 text-sm text-slate-500">
              Papers appear here once an editor has verified and published them.
            </p>
          </div>
        )}

        {!loading && recent.length > 0 && (
          <>
            <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {recent.map((mock) => (
                <li
                  key={mock.id}
                  className="rounded-lg border border-slate-200 bg-white p-4 transition-shadow hover:shadow-sm"
                >
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="font-medium text-slate-900">{mock.title}</h3>
                    {mock.isPremium && (
                      <span className="shrink-0 rounded-full bg-brand-100 px-2 py-0.5 text-xs font-medium text-brand-700">
                        Premium
                      </span>
                    )}
                  </div>
                  <p className="mt-2 text-sm text-slate-500">
                    {mock.kind.replace('_', ' ').toLowerCase()} · {mock.durationMin} min ·{' '}
                    {mock.totalMarks} marks
                  </p>
                </li>
              ))}
            </ul>

            {meta.hasMore && (
              <p className="mt-3 text-sm text-slate-500">
                Showing {recent.length} of {meta.total}.
              </p>
            )}
          </>
        )}
      </section>
    </div>
  )
}

function SkeletonCards({ count }: { count: number }) {
  return (
    <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <li key={i} className="animate-pulse rounded-lg border border-slate-200 bg-white p-4">
          <div className="h-4 w-3/4 rounded bg-slate-200" />
          <div className="mt-3 h-3 w-1/2 rounded bg-slate-100" />
        </li>
      ))}
    </ul>
  )
}
