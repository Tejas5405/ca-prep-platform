import { useState } from "react";

import { Button, Card, ErrorState, Spinner } from "../../components/ui";
import { fetchReviewQueue, runReverify } from "../../lib/campus";
import { useLoader } from "../../lib/useLoader";

/**
 * Review states. Running the job does not publish a question and does not
 * change an answer. The page says so from the response, not from a guess.
 */
export default function ReviewQueue() {
  const queue = useLoader((signal) => fetchReviewQueue(signal), []);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setNote(null);
    try {
      const result = await runReverify();
      setNote(
        `${result.note} Marked ${result.needsUpdate} as needing update and ${result.pendingReview} as pending. Answers changed: ${result.answersChanged ? "yes" : "no"}. Auto-published: ${result.publishedAutomatically ? "yes" : "no"}.`,
      );
      queue.reload();
    } catch (caught) {
      setNote(caught instanceof Error ? caught.message : "The job did not run.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-slate-900">Question review</h1>
        <p className="mt-1 text-sm text-slate-600">
          Open historical flags and law-notice matches become NEEDS_UPDATE. Other open flags
          become PENDING_ADMIN_REVIEW. A published answer is not rewritten.
        </p>
      </header>
      <Button onClick={run} disabled={busy}>
        {busy ? "Running" : "Run re-verification"}
      </Button>
      {note && <p className="text-sm text-slate-700">{note}</p>}
      {queue.loading && <Spinner label="Loading the queue" />}
      {queue.error && <ErrorState error={queue.error} what="the review queue" />}
      {queue.data && queue.data.questions.length === 0 && (
        <Card>
          <p className="text-sm text-slate-700">No question is outside CURRENT.</p>
        </Card>
      )}
      {queue.data && queue.data.questions.length > 0 && (
        <ul className="space-y-2">
          {queue.data.questions.map((row) => (
            <li key={row.id} className="rounded-md border border-slate-200 bg-white p-3 text-sm">
              <p className="font-medium text-slate-900">
                {row.reviewState} · {row.status}
              </p>
              <p className="mt-1 text-slate-700">{row.excerpt}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
