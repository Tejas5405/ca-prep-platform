/**
 * The checkout, tested against a stubbed Razorpay.
 *
 * WHY THIS FILE EXISTS AT ALL
 *
 * The server half of payments was built and tested months before any screen used it,
 * which is exactly the condition in which a frontend integration rots: nothing proved
 * the browser sent the right fields, nothing proved a dismissal did not look like a
 * success, and nothing proved the key secret never reached the client. All four
 * endings of a checkout are unglamorous, and three of them are invisible to a happy
 * path test.
 *
 * The stub loads a fake `window.Razorpay` and records the options it was constructed
 * with, so the assertions are about what this app actually handed the provider:
 *
 *   * the PUBLIC key id from the server's order response, and never anything else;
 *   * the gateway order id, so a payment can be reconciled server-side;
 *   * on success the confirm call carries exactly the three values Checkout returned;
 *   * on dismissal no confirm call happens at all and the screen says nothing was
 *     charged - the state a student actually needs after closing the window;
 *   * on failure the provider's own reason is shown;
 *   * when the API answers 503 because no gateway credentials are set, the screen
 *     names the environment variables instead of showing a dead button.
 */

import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext } from '../hooks/authContext'
import UpgradePage from '../pages/Upgrade'

import { makeAuth } from './authStub'

vi.mock('../lib/supabase', () => ({
  supabase: {
    auth: {
      getSession: vi.fn().mockResolvedValue({
        data: { session: { access_token: 'test-access-token' } },
        error: null,
      }),
    },
  },
  authRedirectUrl: () => 'http://localhost:3000/auth/callback',
  supabaseDashboardUrl: (path: string) => `https://supabase.com/dashboard/project/test/${path}`,
}))

const envelope = (data: unknown) => ({ data, meta: { requestId: 'test' } })

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

const PLANS = envelope({
  plans: [
    {
      code: 'FREE',
      tier: 'FREE',
      label: 'Free',
      amountPaise: 0,
      amountRupees: 0,
      currency: 'INR',
      durationDays: 0,
      tagline: 'Start here.',
      features: ['Past papers'],
      recommended: false,
    },
    {
      code: 'PREMIUM_YEARLY',
      tier: 'PREMIUM',
      label: 'Premium',
      amountPaise: 99900,
      amountRupees: 999,
      currency: 'INR',
      durationDays: 365,
      tagline: 'Both groups.',
      features: ['Everything in Free', 'Unlimited mocks'],
      recommended: true,
    },
  ],
})

const SUBSCRIPTION = envelope({
  tier: 'FREE',
  status: 'ACTIVE',
  expiresAt: null,
  entitlements: ['past_papers'],
  isPremium: false,
})

const ORDER = envelope({
  orderId: '11111111-1111-1111-1111-111111111111',
  planCode: 'PREMIUM_YEARLY',
  amountPaise: 99900,
  amountRupees: 999,
  currency: 'INR',
  providerKeyId: 'rzp_test_public_key',
  providerOrderId: 'order_ABC123',
  receipt: 'rcpt_1',
})

/**
 * The options the page handed to Checkout, narrowed to the fields the tests assert on.
 * A bare `Record<string, unknown>` would make `options.prefill?.name` a type error and
 * quietly discourage asserting on it - which is how the prefill stayed broken.
 */
interface OpenedOptions {
  key: string
  order_id: string
  amount: number
  currency?: string
  description?: string
  prefill?: { name?: string; email?: string }
  modal?: { ondismiss?: () => void }
  handler?: (payload: unknown) => void
}

/** A stand-in for the Checkout API, recording how it was opened. */
class FakeCheckout {
  static instances: FakeCheckout[] = []
  static handlers: Record<string, (payload: unknown) => void> = {}

  options: OpenedOptions
  opened = false

  constructor(options: OpenedOptions) {
    this.options = options
    FakeCheckout.instances.push(this)
  }

  open() {
    this.opened = true
  }

  on(event: string, handler: (payload: unknown) => void) {
    FakeCheckout.handlers[event] = handler
  }
}

function installRazorpay() {
  FakeCheckout.instances = []
  FakeCheckout.handlers = {}
  ;(window as unknown as { Razorpay: unknown }).Razorpay = FakeCheckout
}

/**
 * A fetch stub with one recorded request list.
 *
 * `body` is captured for POSTs because the assertions are about payloads: what the
 * order request contains, and what the confirm request contains.
 */
function stubFetch(overrides: { order?: Response | (() => Response) } = {}) {
  const calls: { url: string; method: string; body: string | null }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      calls.push({
        url,
        method: init?.method ?? 'GET',
        body: typeof init?.body === 'string' ? init.body : null,
      })

      if (url.includes('/me')) {
        return Promise.resolve(json(envelope({ displayName: 'Asha Rao', email: 'asha@example.test' })))
      }
      if (url.includes('/payments/plans')) return Promise.resolve(json(PLANS))
      if (url.includes('/payments/subscription')) return Promise.resolve(json(SUBSCRIPTION))
      if (url.includes('/payments/order')) {
        const override = overrides.order
        if (typeof override === 'function') return Promise.resolve(override())
        if (override) return Promise.resolve(override)
        return Promise.resolve(json(ORDER, 201))
      }
      if (url.includes('/payments/confirm')) {
        return Promise.resolve(
          json(
            envelope({
              orderId: '11111111-1111-1111-1111-111111111111',
              tier: 'PREMIUM',
              expiresAt: '2027-09-25T00:00:00+00:00',
              extended: false,
            }),
          ),
        )
      }
      return Promise.resolve(new Response('{}', { status: 500 }))
    }),
  )
  return calls
}

function renderUpgrade() {
  return render(
    <MemoryRouter initialEntries={['/upgrade']}>
      <AuthContext.Provider value={makeAuth()}>
        <UpgradePage />
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}

async function startPayment() {
  const user = userEvent.setup()
  const button = await screen.findByRole('button', { name: /pay ₹999/i })
  await user.click(button)
  await waitFor(() => expect(FakeCheckout.instances.length).toBe(1))
  const checkout = FakeCheckout.instances[0]
  // Narrowed rather than asserted away: the `waitFor` above proves one was opened, and
  // if that ever stops being true the failure should read as one, not as a crash on
  // `undefined.options`.
  if (!checkout) throw new Error('Checkout was never opened')
  return checkout
}

beforeEach(() => {
  vi.unstubAllGlobals()
  installRazorpay()
})

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as unknown as { Razorpay?: unknown }).Razorpay
})

describe('the upgrade screen', () => {
  it('lists only what can actually be bought', async () => {
    stubFetch()
    renderUpgrade()

    expect(await screen.findByText(/premium/i)).toBeInTheDocument()
    // The free tier is a tier, not a purchase: offering it a Pay button would create
    // a zero-value order.
    expect(screen.queryByRole('button', { name: /pay ₹0/i })).not.toBeInTheDocument()
  })

  it('asks the server for an order by PLAN CODE and opens Checkout with the public key', async () => {
    const calls = stubFetch()
    renderUpgrade()
    const checkout = await startPayment()

    const orderRequest = calls.find((call) => call.url.includes('/payments/order'))
    expect(orderRequest).toBeDefined()
    // snake_case on the wire, asserted literally: the API's inbound schemas forbid
    // unknown fields, so this is the difference between a working purchase and a 422.
    expect(JSON.parse(orderRequest?.body ?? '{}')).toEqual({ plan_code: 'PREMIUM_YEARLY' })

    // The key id came BACK from the server with the order, and that is the only key
    // in the browser. No secret is present in the options, and no amount was chosen
    // by this page - it displays the server's figure.
    expect(checkout.options.key).toBe('rzp_test_public_key')
    expect(checkout.options.order_id).toBe('order_ABC123')
    expect(checkout.options.amount).toBe(99900)
    expect(JSON.stringify(checkout.options)).not.toMatch(/secret/i)
    expect(checkout.opened).toBe(true)
  })

  it('prefills the name from the platform profile, not from the auth user', async () => {
    stubFetch()
    renderUpgrade()
    const checkout = await startPayment()

    // The platform's own `users.display_name` arrives from `GET /me`. It used to be read
    // off Supabase's `User`, which has no such field, so the prefill was always absent
    // and the gateway asked for a name it already knew. The type checker could not see
    // it either: `typecheck` ran `tsc --noEmit` on a solution-style tsconfig and checked
    // nothing (it runs `tsc -b` now).
    expect(checkout.options.prefill?.name).toBe('Asha Rao')
    expect(checkout.options.prefill?.email).toBe('asha@example.test')
  })

  it('confirms with exactly the three values Checkout returned', async () => {
    const calls = stubFetch()
    renderUpgrade()
    const checkout = await startPayment()

    // Wrapped in act: these are the provider's callbacks, fired outside React's event
    // system, and an unwrapped call produces the "not wrapped in act" warning that
    // hides the next real one.
    await act(async () => {
      const handler = checkout.options.handler as (payload: unknown) => void
      handler({
        razorpay_payment_id: 'pay_XYZ',
        razorpay_order_id: 'order_ABC123',
        razorpay_signature: 'sig_123',
      })
    })

    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('/payments/confirm'))).toBe(true),
    )
    const confirmCall = calls.find((call) => call.url.includes('/payments/confirm'))
    expect(JSON.parse(confirmCall?.body ?? '{}')).toEqual({
      // The LOCAL order id names the order: a gateway id from the browser would let
      // anyone describe somebody else's pending order.
      order_id: '11111111-1111-1111-1111-111111111111',
      razorpay_payment_id: 'pay_XYZ',
      razorpay_signature: 'sig_123',
    })

    expect(await screen.findByText(/payment complete/i)).toBeInTheDocument()
    expect(screen.getByText(/premium access is active/i)).toBeInTheDocument()
  })

  it('never claims success when Checkout returns an incomplete payload', async () => {
    const calls = stubFetch()
    renderUpgrade()
    const checkout = await startPayment()

    // A callback without the signature. Treating this as a success would show a paid
    // screen for a payment the server cannot verify.
    await act(async () => {
      const handler = checkout.options.handler as (payload: unknown) => void
      handler({ razorpay_payment_id: 'pay_XYZ', razorpay_order_id: 'order_ABC123' })
    })

    expect(await screen.findByText(/did not go through/i)).toBeInTheDocument()
    expect(calls.some((call) => call.url.includes('/payments/confirm'))).toBe(false)
  })

  it('treats closing the window as a cancellation, not a failure and not a success', async () => {
    const calls = stubFetch()
    renderUpgrade()
    const checkout = await startPayment()

    await act(async () => {
      // Optional in the type because the page only sets it when it wants the dismissal
      // hook; the test asserts it is there by calling it.
      checkout.options.modal?.ondismiss?.()
    })

    expect(await screen.findByText(/payment cancelled/i)).toBeInTheDocument()
    expect(screen.getByText(/nothing was charged/i)).toBeInTheDocument()
    // No confirm call at all: a dismissed checkout is not a payment.
    await waitFor(() => expect(FakeCheckout.instances.length).toBe(1))
    expect(calls.some((call) => call.url.includes('/payments/confirm'))).toBe(false)
  })

  it('shows the provider reason when a payment fails', async () => {
    stubFetch()
    renderUpgrade()
    await startPayment()

    const onFailed = FakeCheckout.handlers['payment.failed']
    expect(onFailed).toBeDefined()
    await act(async () => {
      onFailed?.({
        error: { code: 'BAD_REQUEST_ERROR', description: 'Payment failed: insufficient funds' },
      })
    })

    expect(await screen.findByText(/insufficient funds/i)).toBeInTheDocument()
    expect(screen.getByText(/nothing has been charged/i)).toBeInTheDocument()
  })

  it('names the missing environment variables when the API has no gateway keys', async () => {
    stubFetch({
      order: () =>
        json(
          {
            type: 'https://api.caprep.in/errors/payments',
            title: 'Payments unavailable',
            status: 503,
            detail: 'Payments are not enabled on this deployment.',
          },
          503,
        ),
    })
    renderUpgrade()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /pay ₹999/i }))

    // The page's own heading for the 503 case, and the server's sentence beneath it.
    // It does not print a message of its own here: the API's `detail` names the
    // variables that are actually unset on this deployment, which is more useful to
    // the operator reading it than page copy - and the page cannot know which of the
    // three are missing, so inventing its own sentence would be guessing.
    expect(
      await screen.findByText(/checkout is not configured on this deployment/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/payments are not enabled on this deployment/i)).toBeInTheDocument()
    for (const name of ['RAZORPAY_KEY_ID', 'RAZORPAY_KEY_SECRET', 'RAZORPAY_WEBHOOK_SECRET']) {
      expect(screen.getByText(name)).toBeInTheDocument()
    }
    // Checkout must not have been opened for an order the server refused.
    expect(FakeCheckout.instances.length).toBe(0)
  })

  it('tells the student what to do when the payment is real but activation has not landed', async () => {
    stubFetch()
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input.toString()
        if (url.includes('/payments/plans')) return Promise.resolve(json(PLANS))
        if (url.includes('/payments/subscription')) return Promise.resolve(json(SUBSCRIPTION))
        if (url.includes('/payments/order')) return Promise.resolve(json(ORDER, 201))
        if (url.includes('/payments/confirm')) {
          return Promise.resolve(
            json(
              {
                type: 'https://api.caprep.in/errors/payments',
                title: 'Payment not confirmed',
                status: 409,
                detail: 'The payment could not be confirmed (not_captured).',
              },
              409,
            ),
          )
        }
        void init
        return Promise.resolve(new Response('{}', { status: 500 }))
      }),
    )
    renderUpgrade()
    const checkout = await startPayment()
    await act(async () => {
      ;(checkout.options.handler as (payload: unknown) => void)({
        razorpay_payment_id: 'pay_XYZ',
        razorpay_order_id: 'order_ABC123',
        razorpay_signature: 'sig_123',
      })
    })

    expect(await screen.findByText(/activation pending/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /check my access again/i })).toBeInTheDocument()
  })
})
