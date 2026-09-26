/**
 * The dashboard: is anything broken, is anyone using it, is money moving.
 *
 * WHY THE NUMBERS ARE GROUPED THE WAY THEY ARE
 *
 * In the order an operator would ask: students (is the product reaching anyone),
 * content (is the pipeline healthy - processing and FAILED counts are the alerting
 * line, not the totals), curriculum, activity, money. Every figure comes from ONE
 * request, `GET /admin/dashboard`, which is a set of COUNT queries rather than a cache:
 * a stale dashboard is worse than a slow one, because the whole reason to open it is to
 * find out what is true right now.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 *
 * No charts and no sparklines. There is no history table behind these counts yet, so a
 * graph would have to invent its own series. The analytics section has the timeline,
 * from the event log, and this page links to it rather than drawing a fake one.
 */

import { Link } from 'react-router-dom'

import { Badge, Card, ErrorState, SectionHeading, Spinner } from '../../components/ui'
import { useLoader } from '../../lib/useLoader'
import { fetchAdminDashboard } from '../../lib/queries'

export default function AdminDashboard() {
  const state = useLoader((signal) => fetchAdminDashboard(signal), [])

  if (state.loading) return <Spinner label="Counting…" />
  if (state.error) return <ErrorState error={state.error} what="the platform dashboard" />
  if (!state.data) return null

  const counts = state.data
  const pipelineTrouble = counts.content.failed > 0

  return (
    <div className="space-y-6">
      {pipelineTrouble && (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold tracking-tight text-slate-900">
                {counts.content.failed} document
                {counts.content.failed === 1 ? '' : 's'} failed processing
              </h2>
              <p className="mt-1 text-sm text-slate-600">
                A failed document is readable by nobody, including admins, until it is
                reprocessed or replaced.
              </p>
            </div>
            <Link
              to="/admin/library?status=FAILED"
              className="text-sm font-medium text-brand-700 underline"
            >
              Open the library
            </Link>
          </div>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Students"
          value={counts.students.total}
          note={`${counts.students.active} active · ${counts.students.newThisWeek} new this week`}
        />
        <Stat
          label="Documents"
          value={counts.content.documents}
          note={`${counts.content.indexed} searchable · ${counts.content.processing} processing`}
          tone={pipelineTrouble ? 'amber' : 'slate'}
        />
        <Stat
          label="Extracted pages"
          value={counts.content.pages}
          note={`${formatChars(counts.content.extractedChars)} of text · ${formatBytes(counts.content.storageBytes)} stored`}
        />
        <Stat
          label="Questions"
          value={counts.curriculum.questions}
          note={`${counts.curriculum.publishedQuestions} published · ${counts.curriculum.mockTests} mock papers`}
        />
      </div>

      <SectionHeading
        title="Activity"
        subtitle="What students have done, and what the platform has sent them."
      />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Mock attempts"
          value={counts.activity.mockAttempts}
          note={`${counts.activity.completedAttempts} completed`}
        />
        <Stat
          label="Notifications"
          value={counts.activity.notificationsSent}
          note="Sent in-app"
        />
        <Stat
          label="Analytics events"
          value={counts.activity.aiEvents >= 0 ? counts.activity.aiEvents : 0}
          note="AI-related events recorded"
        />
        <Stat
          label="Courses"
          value={counts.curriculum.courses}
          note={`${counts.curriculum.subjects} subjects · ${counts.curriculum.chapters} chapters`}
        />
      </div>

      <SectionHeading
        title="Money"
        subtitle="Amounts in rupees, read from the payment ledger."
      />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Revenue"
          value={`₹${counts.money.revenueRupees.toLocaleString('en-IN')}`}
          note={`${counts.money.successfulPayments} successful payments`}
        />
        <Stat
          label="Active subscriptions"
          value={counts.money.activeSubscriptions}
          note="Entitled accounts right now"
        />
        <Stat
          label="Failed payments"
          value={counts.money.failedPayments}
          note="Rejected by the gateway or the amount check"
          tone={counts.money.failedPayments > 0 ? 'amber' : 'slate'}
        />
        <Stat
          label="Generated"
          value={new Date(counts.generatedAt).toLocaleTimeString('en-IN')}
          note="Counted on this request, not cached"
        />
      </div>
    </div>
  )
}

function Stat({
  label,
  value,
  note,
  tone = 'slate',
}: {
  label: string
  value: number | string
  note?: string
  tone?: 'slate' | 'amber'
}) {
  return (
    <Card>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">
        {typeof value === 'number' ? value.toLocaleString('en-IN') : value}
      </p>
      {note && (
        <p className="mt-1 text-xs text-slate-500">
          {tone === 'amber' ? <Badge tone="amber">{note}</Badge> : note}
        </p>
      )}
    </Card>
  )
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['kB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(1)} ${units[unit]}`
}

/** Characters, phrased the way a person reads them: "1.2 million", not "1200000". */
function formatChars(chars: number): string {
  if (chars >= 1_000_000) return `${(chars / 1_000_000).toFixed(1)}M characters`
  if (chars >= 1_000) return `${Math.round(chars / 1_000)}k characters`
  return `${chars} characters`
}
