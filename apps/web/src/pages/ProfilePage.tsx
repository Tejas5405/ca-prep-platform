import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge, Button, Card, EmptyState, ErrorState, SectionHeading, Spinner } from '../components/ui'
import { useAuth } from '../hooks/authContext'
import {
  fetchMe,
  fetchPlans,
  fetchSubscription,
  updateMe,
  type Entitlements,
  type Me,
  type Plan,
  type ProfileUpdate,
} from '../lib/queries'
import { QueryError } from '../lib/queries'

/**
 * Profile and plan.
 *
 * THE EXAM DATE IS THE MOST VALUABLE FIELD ON THIS PAGE. Everything the planner
 * does is derived from it, and a wrong date produces a study plan for the wrong
 * month - which is worse than no plan, because it looks authoritative. So it is
 * validated on save and echoed back with its consequence spelled out in days.
 *
 * WHAT THIS PAGE WILL NOT DO IS TAKE A PAYMENT. It shows what a student HAS and
 * links to `/upgrade`, which owns the checkout. Keeping three concerns apart is
 * deliberate: this page answers "what am I entitled to", `/pricing` answers "what
 * exists", and `/upgrade` is the only screen that opens Razorpay Checkout.
 */
export default function ProfilePage() {
  const { user } = useAuth()
  const [me, setMe] = useState<Me | null>(null)
  const [plans, setPlans] = useState<Plan[]>([])
  const [subscription, setSubscription] = useState<Entitlements | null>(null)
  const [error, setError] = useState<QueryError | null>(null)
  const [loading, setLoading] = useState(true)

  /*
   * "Today" is captured ONCE, at mount, rather than read during render.
   *
   * Reading the clock in render is impure (the React compiler rejects it) and it is
   * also pointless here: the day count only needs to be right for the visit, and a
   * value that ticks over at midnight mid-session would re-render a form the
   * student may be typing into.
   */
  const [openedAt] = useState(() => Date.now())
  const [form, setForm] = useState<ProfileUpdate>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState<QueryError | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    Promise.all([
      fetchMe(controller.signal),
      fetchSubscription(controller.signal).catch(() => null),
      fetchPlans(controller.signal).catch(() => [] as Plan[]),
    ])
      .then(([profile, sub, planRows]) => {
        setMe(profile)
        setSubscription(sub)
        setPlans(planRows)
        setForm({
          ...(profile.displayName ? { display_name: profile.displayName } : {}),
          ...(profile.profile.city ? { city: profile.profile.city } : {}),
          ...(profile.profile.college ? { college: profile.profile.college } : {}),
          ...(profile.profile.attemptNumber
            ? { attempt_number: profile.profile.attemptNumber }
            : {}),
          ...(profile.targetLevel ? { target_level: profile.targetLevel } : {}),
          ...(profile.targetExamDate ? { target_exam_date: profile.targetExamDate } : {}),
          daily_goal_minutes: profile.dailyGoalMinutes,
          ...(profile.timezone ? { timezone: profile.timezone } : {}),
        })
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setError(err as QueryError)
      })
      .finally(() => setLoading(false))

    return () => controller.abort()
  }, [])

  async function save() {
    setSaving(true)
    setSaved(false)
    setSaveError(null)
    try {
      const updated = await updateMe(form)
      setMe(updated)
      setSaved(true)
    } catch (err: unknown) {
      setSaveError(err as QueryError)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <Spinner label="Loading your profile…" />
  if (error) return <ErrorState error={error} what="your profile" />
  if (!me) return null

  const daysToExam = me.targetExamDate
    ? Math.ceil(
        (new Date(`${me.targetExamDate}T00:00:00`).getTime() - openedAt) / (1000 * 60 * 60 * 24),
      )
    : null

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Profile</h1>
        <p className="mt-1 text-slate-600">
          Signed in as {user?.email ?? me.email ?? 'a student'}.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Card>
          <SectionHeading title="Study profile" subtitle="Used by the planner and by every estimate the app gives you." />
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name">
              <input
                value={form.display_name ?? ''}
                onChange={(event) => setForm({ ...form, display_name: event.target.value })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </Field>
            <Field label="City">
              <input
                value={form.city ?? ''}
                onChange={(event) => setForm({ ...form, city: event.target.value })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </Field>
            <Field label="College">
              <input
                value={form.college ?? ''}
                onChange={(event) => setForm({ ...form, college: event.target.value })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </Field>
            <Field label="Attempt number">
              <input
                type="number"
                min={1}
                max={10}
                value={form.attempt_number ?? ''}
                onChange={(event) =>
                  setForm({
                    ...form,
                    attempt_number: event.target.value ? Number(event.target.value) : undefined,
                  })
                }
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </Field>
            <Field label="Target level">
              <select
                value={form.target_level ?? ''}
                onChange={(event) => setForm({ ...form, target_level: event.target.value })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              >
                <option value="">Not set</option>
                <option value="FOUNDATION">CA Foundation</option>
                <option value="INTERMEDIATE">CA Intermediate</option>
                <option value="FINAL">CA Final</option>
              </select>
            </Field>
            <Field label="Target exam date">
              <input
                type="date"
                value={form.target_exam_date ?? ''}
                onChange={(event) => setForm({ ...form, target_exam_date: event.target.value })}
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </Field>
            <Field label="Daily goal (minutes)">
              <input
                type="number"
                min={15}
                max={960}
                step={15}
                value={form.daily_goal_minutes ?? 120}
                onChange={(event) =>
                  setForm({ ...form, daily_goal_minutes: Number(event.target.value) })
                }
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </Field>
            <Field label="Time zone">
              <input
                value={form.timezone ?? ''}
                onChange={(event) => setForm({ ...form, timezone: event.target.value })}
                placeholder="Asia/Kolkata"
                className="w-full rounded-md border border-slate-300 px-3 py-2"
              />
            </Field>
          </div>

          {daysToExam !== null && (
            <p className="mt-3 text-sm text-slate-600">
              {daysToExam > 0
                ? `${daysToExam} days until your exam.`
                : daysToExam === 0
                  ? 'Your exam is today.'
                  : 'That date is in the past — update it so the plan is not built for a finished exam.'}
            </p>
          )}

          {saveError && (
            <div className="mt-3">
              <ErrorState error={saveError} what="your profile" />
            </div>
          )}

          <div className="mt-4 flex items-center gap-3">
            <Button disabled={saving} onClick={() => void save()}>
              {saving ? 'Saving…' : 'Save profile'}
            </Button>
            {saved && <span className="text-sm text-right-600">Saved.</span>}
          </div>
        </Card>

        <div className="space-y-4">
          <Card>
            <SectionHeading title="Your plan" />
            {subscription ? (
              <>
                <div className="flex items-center gap-2">
                  <Badge tone={subscription.isPremium ? 'right' : 'slate'}>
                    {subscription.tier}
                  </Badge>
                  <span className="text-sm text-slate-600">{subscription.status}</span>
                </div>
                <ul className="mt-3 space-y-1 text-sm text-slate-600">
                  {subscription.entitlements.map((item) => (
                    <li key={item}>✓ {item.replace(/_/g, ' ')}</li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="text-sm text-slate-500">Could not read your plan just now.</p>
            )}
            <Link
              to="/subscription"
              className="mt-3 inline-block text-sm font-medium text-brand-600 hover:underline"
            >
              Compare plans
            </Link>
          </Card>

          <Card>
            <SectionHeading title="Your progress" />
            <dl className="space-y-2 text-sm">
              <Row label="Points" value={me.profile.totalPoints} />
              <Row label="Current streak" value={`${me.profile.currentStreak} days`} />
              <Row label="Longest streak" value={`${me.profile.longestStreak} days`} />
              <Row label="Referral code" value={me.referralCode ?? '—'} />
            </dl>
          </Card>
        </div>
      </div>

      <Card>
        <SectionHeading
          title="Plans"
          subtitle="Prices are live from the API. Checkout is on the Upgrade page, and it opens only if this deployment has its gateway credentials set."
        />
        <div className="grid gap-3 sm:grid-cols-3">
          {plans.map((plan) => (
            <div
              key={plan.code}
              className={`rounded-lg border p-4 ${
                plan.tier === 'FREE' ? 'border-slate-200' : 'border-brand-500'
              }`}
            >
              <div className="flex items-baseline justify-between">
                <p className="font-medium text-slate-900">{plan.label}</p>
                <Badge tone={plan.tier === 'FREE' ? 'slate' : 'brand'}>{plan.tier}</Badge>
              </div>
              <p className="mt-2 text-2xl font-semibold text-slate-900">
                {plan.amountRupees === 0 ? 'Free' : `₹${plan.amountRupees}`}
              </p>
              {plan.durationDays > 0 && (
                <p className="text-xs text-slate-500">
                  for {Math.round(plan.durationDays / 30)} months
                </p>
              )}
              <p className="mt-2 text-sm text-slate-600">{plan.tagline}</p>
              <ul className="mt-3 space-y-1 text-sm text-slate-600">
                {plan.features.map((feature) => (
                  <li key={feature}>· {feature}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        {plans.length === 0 && (
          <EmptyState title="No plans configured" body="The pricing table is served by the API." />
        )}
      </Card>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="font-medium text-slate-700">{label}</span>
      <span className="mt-1 block">{children}</span>
    </label>
  )
}

function Row({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-slate-600">{label}</dt>
      <dd className="font-medium text-slate-900">{value}</dd>
    </div>
  )
}
