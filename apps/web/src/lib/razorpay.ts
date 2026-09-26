/**
 * Razorpay Checkout, loaded on demand.
 *
 * THE SERVER SIDE OF PAYMENTS ALREADY EXISTED AND IS UNTOUCHED. This file adds the
 * browser half: it opens Razorpay's own Checkout against an order the server created,
 * and hands the three values Checkout returns back to the server to verify. Nothing
 * here decides whether a payment succeeded - the browser is not a trustworthy witness
 * to its own payment, so `POST /payments/confirm` re-reads the payment from Razorpay
 * and re-verifies the signature. What this file does is make the flow usable and make
 * every ending - paid, failed, dismissed, provider unreachable - an explicit state
 * instead of a spinner that never stops.
 *
 * WHAT IS IN THE BROWSER, AND WHAT IS NOT
 *
 * `key_id` is sent by the server with the order and is public by design: Checkout
 * cannot open without it. The key SECRET is never sent to a browser by any endpoint,
 * never read here, and has no accessor anywhere in the client. The webhook secret is
 * likewise server-only. A grep for `key_secret` under `src/` returns nothing, and the
 * test suite asserts the checkout request body carries only the plan code.
 *
 * WHY THE SCRIPT IS INJECTED RATHER THAN IN index.html
 *
 * A `<script src="https://checkout.razorpay.com/v1/checkout.js">` in the document head
 * loads a third-party script for every visitor to every page, including the public
 * landing page, before anyone has decided to pay. Injecting it on the click that
 * starts a payment keeps the promise implied by the privacy page, and the loader
 * below is written for the real failure modes that result: a blocked CDN, an ad
 * blocker, an offline device. Each of those produces a sentence, not a console error.
 */

/** The subset of the Checkout API this app uses. Declared locally, not vendored. */
export interface RazorpayCheckoutOptions {
  key: string
  amount: number
  currency: string
  name: string
  description?: string
  order_id: string
  handler: (response: RazorpaySuccessPayload) => void
  prefill?: { name?: string; email?: string }
  notes?: Record<string, string>
  theme?: { color?: string }
  modal?: { ondismiss?: () => void; escape?: boolean; backdropclose?: boolean }
}

/** Exactly what Checkout hands to `handler`. `razorpay_signature` is what proves it. */
export interface RazorpaySuccessPayload {
  razorpay_payment_id: string
  razorpay_order_id: string
  razorpay_signature: string
}

export interface RazorpayFailurePayload {
  error?: {
    code?: string
    description?: string
    source?: string
    step?: string
    reason?: string
    metadata?: { order_id?: string; payment_id?: string }
  }
}

interface RazorpayCheckout {
  open: () => void
  on: (event: string, handler: (payload: RazorpayFailurePayload) => void) => void
  close?: () => void
}

type RazorpayConstructor = new (options: RazorpayCheckoutOptions) => RazorpayCheckout

declare global {
  interface Window {
    Razorpay?: RazorpayConstructor
  }
}

const SCRIPT_SRC = 'https://checkout.razorpay.com/v1/checkout.js'

/**
 * Why the loader is a module-level promise rather than a per-call one.
 *
 * Two clicks in quick succession would otherwise inject the script twice; the second
 * tag re-executes `checkout.js`, which redefines `window.Razorpay` mid-flow and can
 * drop the options of the instance already open. One promise per document fixes it.
 */
let loading: Promise<RazorpayConstructor> | null = null

export class CheckoutUnavailableError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'CheckoutUnavailableError'
  }
}

export function loadRazorpayCheckout(): Promise<RazorpayConstructor> {
  if (typeof window === 'undefined') {
    return Promise.reject(new CheckoutUnavailableError('Checkout needs a browser.'))
  }
  if (window.Razorpay) return Promise.resolve(window.Razorpay)
  if (loading) return loading

  loading = new Promise<RazorpayConstructor>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = SCRIPT_SRC
    script.async = true
    script.referrerPolicy = 'no-referrer'

    script.onload = () => {
      if (window.Razorpay) {
        resolve(window.Razorpay)
        return
      }
      loading = null
      reject(
        new CheckoutUnavailableError(
          'The payment window loaded but did not initialise. Reload the page and try again.',
        ),
      )
    }
    script.onerror = () => {
      loading = null
      reject(
        new CheckoutUnavailableError(
          'Could not reach the payment provider. This is usually a blocked script or no connection - nothing has been charged.',
        ),
      )
    }

    document.head.appendChild(script)
  })
  return loading
}

/** Everything a caller must supply to open Checkout for a server-created order. */
export interface OpenCheckoutInput {
  /**
   * The PUBLIC key id, as returned by `POST /payments/order`.
   *
   * Deliberately required rather than read from an environment variable in the
   * browser: an env var would let the client start a checkout the server never
   * recorded an order for.
   */
  keyId: string
  providerOrderId: string
  amountPaise: number
  currency: string
  description: string
  prefill?: { name?: string; email?: string }
  onSuccess: (payload: RazorpaySuccessPayload) => void
  onFailure: (detail: string, payload?: RazorpayFailurePayload) => void
  onDismiss: () => void
  onUnavailable: (message: string) => void
}

/**
 * Open Checkout and wire all four endings.
 *
 * `modal.ondismiss` is the one that is usually forgotten. Closing the Checkout
 * window without paying is the most common ending of all - a student who wanted to
 * see the UPI screen - and without a handler the page sits on a disabled "Pay" button
 * forever, which looks like a hung payment. Nothing is charged on a dismiss: the
 * order stays PENDING on the server and is simply never confirmed.
 */
export async function openCheckout(input: OpenCheckoutInput): Promise<void> {
  let Razorpay: RazorpayConstructor
  try {
    Razorpay = await loadRazorpayCheckout()
  } catch (error: unknown) {
    input.onUnavailable(
      error instanceof Error ? error.message : 'The payment window could not be opened.',
    )
    return
  }

  const checkout = new Razorpay({
    key: input.keyId,
    amount: input.amountPaise,
    currency: input.currency,
    name: 'CA Prep',
    description: input.description,
    order_id: input.providerOrderId,
    ...(input.prefill ? { prefill: input.prefill } : {}),
    handler: (response) => {
      if (!response?.razorpay_payment_id || !response?.razorpay_signature) {
        // Checkout called back without the values the server verifies. Treating this
        // as success would show a paid screen for a payment the server will reject.
        input.onFailure(
          'The payment window returned an incomplete confirmation. Nothing has been activated - check your account before paying again.',
        )
        return
      }
      input.onSuccess(response)
    },
    modal: {
      ondismiss: () => input.onDismiss(),
      escape: true,
      backdropclose: false,
    },
    theme: { color: '#0f172a' },
  })

  // `payment.failed` fires for a declined card, a failed UPI collect, a bank timeout.
  // The text is Razorpay's own description, shown as-is: paraphrasing "payment failed"
  // into something friendlier loses the reason the student needs (e.g. insufficient
  // funds), and inventing details would be worse.
  checkout.on('payment.failed', (payload) => {
    input.onFailure(
      payload?.error?.description ?? 'The payment did not go through. Nothing has been charged.',
      payload,
    )
  })

  checkout.open()
}
