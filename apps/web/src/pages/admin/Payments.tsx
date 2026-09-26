/**
 * Orders and webhook events, for an operator reconciling money.
 *
 * WHAT THIS SCREEN WILL NOT DO
 *
 * It will not take a payment itself. The owner can save RAZORPAY_KEY_ID,
 * RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET here, or set the same names
 * on the API. Without the key pair, checkout answers 503. This page does not
 * call Razorpay, and it does not show a secret that was already saved.
 *
 * It will not refund. There is no refunds table, and nothing writes the audit
 * action `payments.refund_recorded`. A button that marked an order refunded would
 * disagree with the gateway.
 *
 * It will not show a webhook body. Those rows can contain payment-method details.
 * The events route does not select that column, and this page has no field for it.
 *
 * A PAID ORDER IS NOT ACCESS
 *
 * Entitlement is the subscription. The amber "Paid, no active entitlement" badge
 * is the row this screen exists to surface: money arrived, access did not.
 */

import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Spinner,
} from "../../components/ui";
import { useDebounced } from "../../lib/useDebounced";
import { useLoader, type LoaderState } from "../../lib/useLoader";
import {
  fetchAdminPaymentEvents,
  fetchAdminPaymentOrders,
  fetchGatewayStatus,
  saveGatewayKeys,
  type AdminPaymentEventList,
  type AdminPaymentOrder,
  type GatewayStatus,
  type AdminPaymentOrderList,
} from "../../lib/queries";

const STATUSES = ["CREATED", "PAID", "FAILED", "EXPIRED"] as const;

const STATUS_TONE: Record<
  string,
  "slate" | "brand" | "right" | "wrong" | "amber"
> = {
  CREATED: "amber",
  PAID: "right",
  FAILED: "wrong",
  EXPIRED: "slate",
};

function formatRupees(rupees: number, remainderPaise: number): string {
  const whole = rupees.toLocaleString("en-IN");
  if (remainderPaise === 0) return `₹${whole}`;
  return `₹${whole}.${String(remainderPaise).padStart(2, "0")}`;
}

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString("en-IN") : "—";
}

function GatewayForm() {
  const status = useLoader((signal) => fetchGatewayStatus(signal), []);
  const [keyId, setKeyId] = useState("");
  const [keySecret, setKeySecret] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  async function save() {
    setFormError(null);
    setNote(null);
    const body: { key_id?: string; key_secret?: string; webhook_secret?: string } = {};
    if (keyId.trim()) body.key_id = keyId.trim();
    if (keySecret.trim()) body.key_secret = keySecret.trim();
    if (webhookSecret.trim()) body.webhook_secret = webhookSecret.trim();
    if (!body.key_id && !body.key_secret && !body.webhook_secret) {
      setFormError("Enter at least one value. A blank field is left unchanged.");
      return;
    }
    setBusy(true);
    try {
      const saved: GatewayStatus = await saveGatewayKeys(body);
      setKeySecret("");
      setWebhookSecret("");
      setKeyId("");
      setNote(
        saved.checkoutReady
          ? `Checkout can start. Source: ${saved.source}. The secret was not returned.`
          : `Still missing ${saved.missingForCheckout.join(", ") || "a key"}.`,
      );
      status.reload();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Could not save the keys.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <h2 className="text-sm font-semibold tracking-tight text-slate-900">
        Razorpay keys
      </h2>
      <p className="mt-1 text-sm text-slate-600">
        Enter the key once. It is stored on the server. This form never displays
        a secret that was already saved. Environment variables still win if they
        are set.
      </p>
      {status.loading && <Spinner label="Checking gateway configuration" />}
      {status.error && (
        <p className="mt-2 text-sm text-slate-600">
          Could not read whether a key is already saved. You can still enter one
          if you are the owner.
        </p>
      )}
      {status.data && (
        <p className="mt-2 text-sm text-slate-700">
          Checkout {status.data.checkoutReady ? "is ready" : "is not ready"} (
          {status.data.source}
          {status.data.keyId ? `, key id ${status.data.keyId}` : ""}). Webhooks{" "}
          {status.data.webhookReady ? "are ready" : "still need RAZORPAY_WEBHOOK_SECRET"}.
        </p>
      )}
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        <input
          aria-label="Razorpay key id"
          className="rounded-md border border-slate-300 px-2 py-2 text-sm"
          placeholder="Key id"
          value={keyId}
          onChange={(event) => setKeyId(event.target.value)}
          autoComplete="off"
        />
        <input
          aria-label="Razorpay key secret"
          className="rounded-md border border-slate-300 px-2 py-2 text-sm"
          placeholder="Key secret"
          type="password"
          value={keySecret}
          onChange={(event) => setKeySecret(event.target.value)}
          autoComplete="new-password"
        />
        <input
          aria-label="Razorpay webhook secret"
          className="rounded-md border border-slate-300 px-2 py-2 text-sm"
          placeholder="Webhook secret"
          type="password"
          value={webhookSecret}
          onChange={(event) => setWebhookSecret(event.target.value)}
          autoComplete="new-password"
        />
      </div>
      <div className="mt-3">
        <Button tone="primary" disabled={busy} onClick={save}>
          Save gateway keys
        </Button>
      </div>
      {formError && <p className="mt-2 text-sm text-red-700">{formError}</p>}
      {note && <p className="mt-2 text-sm text-slate-700">{note}</p>}
    </Card>
  );
}

export default function AdminPayments() {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "events" ? "events" : "orders";
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const term = useDebounced(query.trim(), query ? 250 : 0);

  const orders = useLoader(
    (signal) =>
      fetchAdminPaymentOrders(
        {
          ...(status ? { status } : {}),
          ...(term ? { q: term } : {}),
          page,
          limit: 50,
        },
        signal,
      ),
    [status, term, page, view],
    view === "orders",
  );
  const events = useLoader(
    (signal) =>
      fetchAdminPaymentEvents(
        { ...(term ? { q: term } : {}), page, limit: 50 },
        signal,
      ),
    [term, page, view],
    view === "events",
  );

  const showEvents = () => {
    setPage(1);
    setParams({ view: "events" });
  };
  const showOrders = () => {
    setPage(1);
    setParams({});
  };

  return (
    <div className="min-w-0 space-y-6">
      <Card>
        <h2 className="text-sm font-semibold tracking-tight text-slate-900">
          What this list is
        </h2>
        <ul className="mt-2 space-y-2 text-sm text-slate-600">
          <li>
            A paid order is not access. The entitlement next to a row is the
            subscription, which is the only thing that grants it.
          </li>
          <li>
            Refunds are not a button. There is no refunds table, and nothing
            writes{" "}
            <span className="font-mono text-xs">payments.refund_recorded</span>,
            so this screen will not mark an order refunded.
          </li>
          <li>
            Taking a payment needs{" "}
            <span className="font-mono text-xs">RAZORPAY_KEY_ID</span>,{" "}
            <span className="font-mono text-xs">RAZORPAY_KEY_SECRET</span> and,
            for webhooks,{" "}
            <span className="font-mono text-xs">RAZORPAY_WEBHOOK_SECRET</span>.
            Enter them once below, or set the same names on the API. This list
            does not call Razorpay, and a saved secret is not shown again.
          </li>
        </ul>
      </Card>

      <GatewayForm />

      <div
        className="flex flex-wrap gap-2"
        role="group"
        aria-label="Payment views"
      >
        <Button
          tone={view === "orders" ? "primary" : "quiet"}
          onClick={showOrders}
        >
          Orders
        </Button>
        <Button
          tone={view === "events" ? "primary" : "quiet"}
          onClick={showEvents}
        >
          Webhook events
        </Button>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <label className="min-w-0 flex-1 text-sm text-slate-700">
          Email
          <input
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(1);
            }}
            placeholder="student@example.com"
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
        </label>
        {view === "orders" && (
          <label className="text-sm text-slate-700">
            Status
            <select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setPage(1);
              }}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm sm:w-40"
            >
              <option value="">Any</option>
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {view === "orders" ? (
        <OrdersPane state={orders} page={page} onPage={setPage} />
      ) : (
        <EventsPane state={events} page={page} onPage={setPage} />
      )}
    </div>
  );
}

function OrdersPane({
  state,
  page,
  onPage,
}: {
  state: LoaderState<AdminPaymentOrderList>;
  page: number;
  onPage: (page: number) => void;
}) {
  if (state.loading) return <Spinner label="Loading orders…" />;
  if (state.error)
    return <ErrorState error={state.error} what="payment orders" />;
  if (!state.data) return null;

  const { data, meta } = state.data;
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">
        {meta.total.toLocaleString("en-IN")} order{meta.total === 1 ? "" : "s"}
        {" · "}
        {meta.paidOrders.toLocaleString("en-IN")} paid,{" "}
        {formatRupees(meta.paidRupees, meta.paidRemainderPaise)} captured in
        this filter
      </p>
      {data.length === 0 && (
        <EmptyState
          title="No orders match"
          body="Nothing has been charged on this deployment until Razorpay keys exist. A filter can also hide rows that do exist."
        />
      )}
      <ul className="space-y-2">
        {data.map((order) => (
          <li key={order.id}>
            <OrderCard order={order} />
          </li>
        ))}
      </ul>
      <Pager
        page={page}
        hasMore={meta.hasMore}
        total={meta.total}
        onPage={onPage}
      />
    </div>
  );
}

function OrderCard({ order }: { order: AdminPaymentOrder }) {
  return (
    <Card>
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-medium text-slate-900">
              {order.displayName ?? order.email ?? "No email"}
            </span>
            <Badge tone={STATUS_TONE[order.status] ?? "slate"}>
              {order.status}
            </Badge>
            <Badge tone="slate">{order.planCode}</Badge>
            {order.status === "PAID" && !order.entitlement && (
              <Badge tone="amber">Paid, no active entitlement</Badge>
            )}
            {order.entitlement && (
              <Badge tone="right">
                {order.entitlement.tier} · {order.entitlement.status}
              </Badge>
            )}
          </div>
          <p className="mt-1 break-words text-xs text-slate-500">
            {order.email ?? "No email"}
            {" · "}
            {order.tier}
            {" · "}
            {when(order.createdAt)}
            {order.paidAt ? ` · paid ${when(order.paidAt)}` : ""}
          </p>
          {order.failureReason && (
            <p className="mt-2 break-words text-sm text-red-700">
              {order.failureReason}
            </p>
          )}
          <p className="mt-2 break-all font-mono text-[11px] text-slate-400">
            {order.providerOrderId ?? "no gateway order id"}
            {order.providerPaymentId ? ` · ${order.providerPaymentId}` : ""}
            {" · "}
            {order.receipt}
          </p>
        </div>
        <p className="text-sm font-semibold tabular-nums text-slate-900">
          {formatRupees(order.amountRupees, order.amountRemainderPaise)}
        </p>
      </div>
    </Card>
  );
}

function EventsPane({
  state,
  page,
  onPage,
}: {
  state: LoaderState<AdminPaymentEventList>;
  page: number;
  onPage: (page: number) => void;
}) {
  if (state.loading) return <Spinner label="Loading webhook events…" />;
  if (state.error)
    return <ErrorState error={state.error} what="webhook events" />;
  if (!state.data) return null;

  const { data, meta } = state.data;
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">
        The raw webhook body is not shown. It can contain payment-method
        details, stays in the database for idempotency, and is not returned by
        this list.
        {meta.unprocessed > 0
          ? ` ${meta.unprocessed.toLocaleString("en-IN")} event${meta.unprocessed === 1 ? "" : "s"} not yet processed.`
          : " None are waiting to be processed."}
      </p>
      {data.length === 0 && (
        <EmptyState
          title="No webhook events"
          body="Events appear when Razorpay calls the webhook. Without the webhook secret, that call is refused and nothing is stored."
        />
      )}
      <ul className="space-y-2">
        {data.map((event) => (
          <li key={event.id}>
            <Card>
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-slate-800">
                  {event.eventType}
                </span>
                <Badge tone={event.processedAt ? "right" : "amber"}>
                  {event.processedAt ? "Processed" : "Not processed"}
                </Badge>
                <Badge tone={event.signatureVerified ? "slate" : "wrong"}>
                  {event.signatureVerified
                    ? "Signature checked"
                    : "Signature not checked"}
                </Badge>
              </div>
              <p className="mt-1 break-words text-xs text-slate-500">
                {event.email ?? "No account linked"}
                {" · "}
                {when(event.createdAt)}
              </p>
              {event.processingError && (
                <p className="mt-2 break-words text-sm text-red-700">
                  {event.processingError}
                </p>
              )}
              <p className="mt-2 break-all font-mono text-[11px] text-slate-400">
                {event.eventId}
              </p>
            </Card>
          </li>
        ))}
      </ul>
      <Pager
        page={page}
        hasMore={meta.hasMore}
        total={meta.total}
        onPage={onPage}
      />
    </div>
  );
}

function Pager({
  page,
  hasMore,
  total,
  onPage,
}: {
  page: number;
  hasMore: boolean;
  total: number;
  onPage: (page: number) => void;
}) {
  if (total === 0 || (page === 1 && !hasMore)) return null;
  return (
    <div className="flex items-center gap-2">
      <Button
        tone="quiet"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
      >
        Previous
      </Button>
      <span className="text-sm text-slate-600">Page {page}</span>
      <Button tone="quiet" disabled={!hasMore} onClick={() => onPage(page + 1)}>
        Next
      </Button>
    </div>
  );
}
