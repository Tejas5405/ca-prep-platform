/**
 * Ask the library.
 *
 * The page never writes an explanation of its own. If the server returns
 * `answer: null`, that is what the student sees: quotations, or a sentence that
 * nothing they may read contains the question. A missing provider key is not
 * papered over with a paragraph.
 */

import { useState } from "react";
import { Link } from "react-router-dom";

import { Button, Card, ErrorState, Spinner } from "../components/ui";
import { useLoader } from "../lib/useLoader";
import {
  askAssistant,
  fetchAssistantStatus,
  type AssistantAnswer,
} from "../lib/queries";

export default function AssistantPage() {
  const status = useLoader((signal) => fetchAssistantStatus(signal), []);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AssistantAnswer | null>(null);

  async function ask() {
    setError(null);
    setBusy(true);
    try {
      setResult(await askAssistant(query.trim()));
    } catch (caught) {
      setResult(null);
      setError(caught instanceof Error ? caught.message : "Could not ask.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mx-auto max-w-3xl space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-slate-900">Library assistant</h1>
        <p className="mt-1 text-sm text-slate-600">
          {status.data?.generatesAnswers
            ? "A study suggestion is written only from excerpts you may already read. It is not a legal authority."
            : "Quotations from documents you may already read. This page does not write an explanation."}
        </p>
      </header>

      {status.loading && <Spinner label="Checking the assistant" />}
      {status.error && <ErrorState error={status.error} what="the assistant" />}
      {status.data && !status.data.enabled && (
        <Card>
          <p className="text-sm text-slate-700">
            The assistant is off. An owner turns <span className="font-mono">features.ai_assistant</span> on
            in platform settings. Nothing is invented while it is off.
          </p>
        </Card>
      )}
      {status.data?.enabled && (
        <Card>
          <label className="block text-sm text-slate-700" htmlFor="ask">
            Ask about something in your library
          </label>
          <textarea
            id="ask"
            className="mt-2 w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
            rows={3}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <div className="mt-3">
            <Button onClick={ask} disabled={busy || query.trim().length < 2}>
              {busy ? "Searching" : status.data?.generatesAnswers ? "Ask" : "Search my documents"}
            </Button>
          </div>
          <p className="mt-2 text-xs text-slate-500">{status.data.note}</p>
        </Card>
      )}

      {error && <p className="text-sm text-red-700">{error}</p>}

      {result && (
        <Card>
          <p className="text-sm text-slate-700">{result.note}</p>
          <p className="mt-1 text-xs text-slate-500">
            {result.answer === null
              ? "Generated answer: none."
              : "Study suggestion. Not a legal authority."}{" "}
            {result.remainingToday} left today.
          </p>
          {result.answer && (
            <p className="mt-3 whitespace-pre-wrap text-sm text-slate-800">{result.answer}</p>
          )}
          {result.quotations.length === 0 && result.questions.length === 0 && (
            <p className="mt-3 text-sm text-slate-600">No quotation to show.</p>
          )}
          <ul className="mt-3 space-y-3">
            {result.quotations.map((hit) => (
              <li key={`${hit.documentId}-${hit.pageNumber}`} className="text-sm">
                <Link className="font-medium text-brand-700 underline" to={`/library/${hit.documentId}`}>
                  {hit.title}, page {hit.pageNumber}
                </Link>
                <p className="mt-1 whitespace-pre-wrap text-slate-700">{hit.excerpt}</p>
              </li>
            ))}
            {result.questions.map((hit) => (
              <li key={hit.questionId} className="text-sm text-slate-700">
                <span className="font-medium text-slate-900">Published question. </span>
                {hit.excerpt}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </section>
  );
}
