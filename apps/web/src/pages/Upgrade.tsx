/**
 * `/upgrade` — buy a plan, and nothing else.
 *
 * THIS PAGE EXISTS TO DRIVE THE SERVER THAT WAS ALREADY THERE. Order creation,
 * signature verification, webhook reconciliation and entitlement activation were all
 * built and tested before this screen existed; what was missing was the browser half,
 * so the only way to buy was curl. Nothing in the payment flow is reimplemented here.
 *
 * THE FOUR ENDINGS, ALL HANDLED
 *
 *   paid        Checkout hands back a payment id and a signature -> the server
 *               verifies and activates. The screen then re-reads entitlements rather
 *               than assuming, because the source of truth for "am I Premium" is the
 *               server's answer, not this component's state.
 *   failed      Razorpay reports a declined card or a failed collect, with its own
 *               reason. Shown verbatim; nothing was charged.
 *   dismissed   The student closed the window. NOT AN ERROR, and the most common
 *               ending. Explicitly reported as "nothing was charged" so nobody is
 *               left wondering whether a closed window means a pending payment.
 *   offline     The Checkout script is blocked or the device is offline. The order is
 *               still created server-side and simply expires unconfirmed; the screen
 *               says so instead of dying silently.
 *
 * A payment that succeeds does not always activate instantly: the server can learn
 * about it from the webhook before or after the browser callback, so an activation in
 * flight is a state this page renders ("payment received, activating") with the
 * ability to re-check, rather than a spinner with no explanation.
 */

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge, Button, Card, ErrorState, SectionHeading, Spinner } from '../components/ui'
import { useAuth } from '../hooks/authContext'
import {
  confirmPayment,
  createPaymentOrder,
  fetchMe,
  fetchPlans,
  fetchSubscription,
  QueryError,
  type Entitlements,
  type Plan,
} from '../lib/queries'
import { openCheckout, type RazorpayFailurePayload } from '../lib/razorpay'

/**
 * The environment variables that must be set for checkout to be usable.
 *
 * Named in the UI on purpose. The API answers 503 with "Payments are unavailable on
 * this deployment", which is correct but leaves an operator with nowhere to look. The
 * owner asked to be told the exact variable rather than a category of problem, and
 * this is where an operator actually sees it.
 */
const REQUIRED_SERVER_CONFIG = [
  'RAZORPAY_KEY_ID',
  'RAZORPAY_KEY_SECRET',
  'RAZORPAY_WEBHOOK_SECRET',
]

type Stage =
  | { kind: 'idle' }
  | { kind: 'starting'; planCode: string }
  | { kind: 'waiting'; planCode: string }
  | { kind: 'activating'; planCode: string }
  | { kind: 'paid'; orderId: string; tier?: string; expiresAt?: string | null }
  | { kind: 'not-activated'; orderId: string; detail: string }
  | { kind: 'cancelled' }
  | { kind: 'failed'; detail: string; code?: string | undefined }
  | { kind: 'unavailable'; detail: string }

export default function UpgradePage() {
  const { user } = useAuth()
  const [plans, setPlans] = useState<Plan[]>([])
  /**
   * The name to prefill at the gateway, from `GET /me` - not from the auth user.
   *
   * Supabase's `User` has no display name: the platform keeps that in its own `users`
   * row (`displayName`), which is also what the rest of the app shows. Reading a field
   * the auth library does not have compiled to `undefined` here, so the prefill silently
   * never happened and the gateway asked the student to type a name it already knew.
   */
  const [buyerName, setBuyerName] = useState<string | null>(null)
  const [buyerEmail, setBuyerEmail] = useState<string | null>(null)
  const [subscription, setSubscription] = useState<Entitlements | null>(null)
  const [loadError, setLoadError] = useState<QueryError | null>(null)
  const [loading, setLoading] = useState(true)
  const [stage, setStage] = useState<Stage>({ kind: 'idle' })
  const [rechecking, setRechecking] = useState(false)

  const loadSubscription = useCallback(async () => {
    const entitlements = await fetchSubscription()
    setSubscription(entitlements)
    return entitlements
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    let active = true

    Promise.all([
      fetchPlans(controller.signal),
      fetchSubscription(controller.signal),
      // A failure here is not a reason to block the page: the prefill is a courtesy.
      fetchMe(controller.signal).catch(() => null),
    ])
      .then(([catalogue, entitlements, me]) => {
        if (!active) return
        setPlans(catalogue)
        setSubscription(entitlements)
        setBuyerName(me?.displayName ?? null)
        setBuyerEmail(me?.email ?? null)
        setLoading(false)
      })
      .catch((error: unknown) => {
        if (!active) return
        setLoadError(error instanceof QueryError ? error : new QueryError(error))
        setLoading(false)
      })

    return () => {
      active = false
      controller.abort()
    }
  }, [])

  const start = useCallback(
    async (plan: Plan) => {
      setStage({ kind: 'starting', planCode: plan.code })
      let order
      try {
        order = await createPaymentOrder(plan.code)
      } catch (error: unknown) {
        const queryError = error instanceof QueryError ? error : new QueryError(error)
        if (queryError.status === 503) {
          setStage({
            kind: 'unavailable',
            detail:
              queryError.message ||
              'This deployment cannot take payments yet: the gateway credentials are not set.',
          })
          return
        }
        setStage({ kind: 'failed', detail: queryError.message })
        return
      }

      if (!order.providerOrderId) {
        // An order row exists with no gateway id, which means Razorpay was never
        // reached. Opening Checkout without an order_id would create a payment the
        // server cannot reconcile, so it is refused here.
        setStage({
          kind: 'failed',
          detail:
            'The payment could not be started at the provider. Nothing has been charged.',
        })
        return
      }

      setStage({ kind: 'waiting', planCode: plan.code })

      await openCheckout({
        keyId: order.providerKeyId,
        providerOrderId: order.providerOrderId,
        amountPaise: order.amountPaise,
        currency: order.currency,
        description: `${plan.label} — ${plan.durationDays} days`,
        ...(buyerName || buyerEmail || user?.email
          ? {
              prefill: {
                ...(buyerName ? { name: buyerName } : {}),
                // The platform's row first, the auth session second: both are the same
                // address today, but only one of them is the address the receipt will
                // be reconciled against.
                ...(buyerEmail || user?.email
                  ? { email: (buyerEmail ?? user?.email) as string }
                  : {}),
              },
            }
          : {}),
        onDismiss: () => setStage({ kind: 'cancelled' }),
        onUnavailable: (detail) => setStage({ kind: 'unavailable', detail }),
        onFailure: (detail, payload: RazorpayFailurePayload | undefined) =>
          setStage({ kind: 'failed', detail, code: payload?.error?.code }),
        onSuccess: (payload) => {
          setStage({ kind: 'activating', planCode: plan.code })
          confirmPayment({
            orderId: order.orderId,
            razorpayPaymentId: payload.razorpay_payment_id,
            razorpaySignature: payload.razorpay_signature,
          })
            .then(async (result) => {
              // Conditional spreads: with exactOptionalPropertyTypes an explicit
              // `tier: undefined` is not assignable to the optional field.
              setStage({
                kind: 'paid',
                orderId: result.orderId,
                ...(result.tier ? { tier: result.tier } : {}),
                ...(result.expiresAt ? { expiresAt: result.expiresAt } : {}),
              })
              // Re-read rather than trusting the activation payload: entitlements are
              // enforced from the server's row on every request, so this page shows
              // what the API will actually allow.
              await loadSubscription().catch(() => undefined)
            })
            .catch((error: unknown) => {
              const queryError = error instanceof QueryError ? error : new QueryError(error)
              setStage({
                kind: 'not-activated',
                orderId: order.orderId,
                detail:
                  queryError.status === 409
                    ? 'The payment has not been captured yet. If money left your account it will be activated automatically within a few minutes.'
                    : queryError.message,
              })
            })
        },
      })
    },
    [loadSubscription, buyerName, buyerEmail, user?.email],
  )

  const recheck = useCallback(async () => {
    setRechecking(true)
    try {
      await loadSubscription()
      setStage({ kind: 'idle' })
    } catch {
      /* Left on the same screen: a failed re-check must not claim anything changed. */
    } finally {
      setRechecking(false)
    }
  }, [loadSubscription])

  if (loading) return <Spinner label="Loading plans…" />

  const paidPlans = plans.filter((plan) => plan.amountPaise > 0)
  const busy = stage.kind === 'starting' || stage.kind === 'waiting' || stage.kind === 'activating'

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <div>
        <p className="text-xs font-semibold tracking-wide text-brand-600 uppercase">Upgrade</p>
        <SectionHeading
          title="Buy a plan"
          subtitle="Payment is handled by Razorpay. This platform never sees your card or UPI details, and the amount is looked up on the server from its own catalogue."
        />
      </div>

      {loadError ? (
        <ErrorState error={loadError} what="the plan catalogue" />
      ) : (
        <>
          <Card>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold tracking-tight text-slate-900">
                  Your access right now
                </h3>
                <p className="mt-1 text-sm text-slate-600">
                  {subscription && subscription.tier !== 'FREE'
                    ? `${subscription.tier}${subscription.expiresAt ? ` · renews to ${new Date(subscription.expiresAt).toLocaleDateString()}` : ''}`
                    : 'Free tier'}
                </p>
              </div>
              <Badge tone={subscription?.isPremium ? 'right' : 'slate'}>
                {subscription?.isPremium ? 'Premium active' : 'Free'}
              </Badge>
            </div>
          </Card>

          {stage.kind === 'unavailable' && (
            <Card>
              <h3 className="text-sm font-semibold tracking-tight text-slate-900">
                Checkout is not configured on this deployment
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-600">{stage.detail}</p>
              <p className="mt-3 text-sm text-slate-600">
                Set these on the API, or enter them once under Admin → Payments.
                Checkout needs the key id and the key secret. Webhooks also need
                the webhook secret:
              </p>
              <ul className="mt-2 space-y-1">
                {REQUIRED_SERVER_CONFIG.map((name) => (
                  <li key={name}>
                    <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-800">
                      {name}
                    </code>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-slate-500">
                They belong in the server environment only. Nothing in this page reads
                them, and the browser is never given the key secret.
              </p>
            </Card>
          )}

          {stage.kind === 'cancelled' && (
            <Card>
              <h3 className="text-sm font-semibold tracking-tight text-slate-900">
                Payment cancelled
              </h3>
              <p className="mt-2 text-sm text-slate-600">
                You closed the payment window and nothing was charged. The order is left
                unconfirmed on the server and expires on its own — you can start again
                whenever you like.
              </p>
            </Card>
          )}

          {stage.kind === 'failed' && (
            <Card>
              <h3 className="text-sm font-semibold tracking-tight text-slate-900">
                Payment did not go through
              </h3>
              <p className="mt-2 text-sm text-slate-600">{stage.detail}</p>
              {stage.code && (
                <p className="mt-1 text-xs text-slate-500">Provider code: {stage.code}</p>
              )}
              <p className="mt-2 text-sm text-slate-600">
                Nothing has been charged to your account.
              </p>
            </Card>
          )}

          {stage.kind === 'activating' && (
            <Card>
              <div className="flex items-center gap-3">
                <Spinner label="Verifying the payment…" />
              </div>
              <p className="mt-2 text-sm text-slate-600">
                Verifying the signature with the server and reading the payment back from
                Razorpay. Do not close this page.
              </p>
            </Card>
          )}

          {stage.kind === 'paid' && (
            <Card>
              <h3 className="text-sm font-semibold tracking-tight text-slate-900">
                Payment complete
              </h3>
              <p className="mt-2 text-sm text-slate-600">
                Your {stage.tier ?? 'plan'} access is active
                {stage.expiresAt
                  ? ` until ${new Date(stage.expiresAt).toLocaleDateString()}`
                  : ''}
                . Reference <span className="font-mono text-xs">{stage.orderId}</span>.
              </p>
              <div className="mt-4 flex flex-wrap gap-3">
                <Link to="/dashboard" className="text-sm font-medium text-slate-900 underline">
                  Back to your dashboard
                </Link>
              </div>
            </Card>
          )}

          {stage.kind === 'not-activated' && (
            <Card>
              <h3 className="text-sm font-semibold tracking-tight text-slate-900">
                Payment received, activation pending
              </h3>
              <p className="mt-2 text-sm text-slate-600">{stage.detail}</p>
              <p className="mt-1 text-xs text-slate-500">
                Order <span className="font-mono">{stage.orderId}</span>
              </p>
              <div className="mt-4">
                <Button
                  tone="secondary"
                  onClick={recheck}
                  disabled={rechecking}
                  aria-busy={rechecking}
                >
                  {rechecking ? 'Checking…' : 'Check my access again'}
                </Button>
              </div>
            </Card>
          )}

          <ul className="space-y-4">
            {paidPlans.map((plan) => (
              <li key={plan.code}>
                <Card>
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="text-base font-semibold tracking-tight text-slate-900">
                          {plan.label}
                        </h3>
                        {plan.recommended && <Badge tone="brand">Recommended</Badge>}
                      </div>
                      <p className="mt-1 text-sm text-slate-600">{plan.tagline}</p>
                      <ul className="mt-3 space-y-1 text-sm text-slate-600">
                        {plan.features.map((feature) => (
                          <li key={feature}>· {feature}</li>
                        ))}
                      </ul>
                    </div>
                    <div className="text-right">
                      <p className="text-xl font-semibold tracking-tight text-slate-900">
                        ₹{plan.amountRupees.toLocaleString('en-IN')}
                      </p>
                      <p className="text-xs text-slate-500">
                        for {plan.durationDays} days
                      </p>
                      <div className="mt-3">
                        <Button
                          onClick={() => start(plan)}
                          disabled={busy}
                          aria-busy={stage.kind === 'starting' && stage.planCode === plan.code}
                        >
                          {stage.kind === 'starting' && stage.planCode === plan.code
                            ? 'Starting…'
                            : `Pay ₹${plan.amountRupees.toLocaleString('en-IN')}`}
                        </Button>
                      </div>
                    </div>
                  </div>
                </Card>
              </li>
            ))}
          </ul>

          {paidPlans.length === 0 && (
            <Card>
              <p className="text-sm text-slate-600">
                No purchasable plans are published yet.
              </p>
            </Card>
          )}

          <p className="text-xs leading-relaxed text-slate-500">
            Access is activated by the server after it verifies the payment with
            Razorpay — not by this page. Closing the window before paying charges
            nothing. If a payment succeeds but access has not updated within a few
            minutes, check this page again; the activation arrives over a signed webhook
            that retries.
          </p>
        </>
      )}
    </div>
  )
}
