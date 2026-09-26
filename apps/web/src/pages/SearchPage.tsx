import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { Badge, Button, Card, EmptyState, ErrorState, Spinner } from '../components/ui'
import { Headline } from '../lib/headline'
import { useLoader } from '../lib/useLoader'
import { fetchQuestion, search, type QuestionDetail, type SearchResult } from '../lib/queries'

/**
 * Search across the published question bank.
 *
 * SNIPPETS COME BACK WITH `<b>` TAGS, because the match is produced by
 * PostgreSQL's `ts_headline` and the highlight is computed server-side where the
 * search terms are known. Rendering that safely is `Headline`: the tags are split
 * out and the pieces are text nodes, so a question containing `<script>` is
 * displayed as characters rather than executed.
 *
 * A RESULT OPENS INTO THE QUESTION. It did not before: the search payload carries
 * no options by design, so the page could show what it had found and nothing more.
 * Opening one calls `GET /questions/{id}`, which returns the options and, if the
 * student has already answered it, the worked answer. Results are expanded in place
 * rather than navigated to, because a student searching a phrase is scanning - a
 * route change per result would lose the search that produced it.
 */
export default function SearchPage() {
  const [params, setParams] = useSearchParams()
  const urlTerm = params.get('q') ?? ''

  // `term` is what has been SEARCHED; `draft` is what is in the box. Keeping them
  // apart is what lets the form be a form: typing must not fire a request per
  // keystroke, and pressing search must not require an effect to notice.
  const [term, setTerm] = useState(urlTerm)
  const [draft, setDraft] = useState(urlTerm)

  const results = useLoader(
    (signal) => (term ? search(term, 20, signal) : Promise.resolve(null)),
    [term],
    Boolean(term),
  )

  function run(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = draft.trim()
    if (!trimmed) return
    const next = new URLSearchParams(params)
    next.set('q', trimmed)
    setParams(next)
    setTerm(trimmed)
  }

  const rows = results.data?.results ?? []

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Search</h1>
        <p className="mt-1 text-slate-600">
          Find a question by its wording, across every published paper. To search inside a study
          PDF, use the{' '}
          <Link to="/library" className="font-medium text-brand-600 hover:underline">
            library
          </Link>
          .
        </p>
      </div>

      <form onSubmit={run} className="flex gap-2">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="e.g. depreciation, negotiable instrument, working capital"
          aria-label="Search the question bank"
          className="w-full rounded-md border border-slate-300 px-3 py-2"
        />
        <Button type="submit" disabled={results.loading || !draft.trim()}>
          {results.loading ? 'Searching…' : 'Search'}
        </Button>
      </form>

      {results.error && <ErrorState error={results.error} what="your search" />}
      {results.loading && <Spinner label="Searching the bank…" />}

      {term && results.data && (
        <p className="text-sm text-slate-500">
          {results.data.count} result{results.data.count === 1 ? '' : 's'} for “{term}”
        </p>
      )}

      {term && !results.loading && rows.length === 0 && (
        <EmptyState
          title="No questions match"
          body="Search matches the wording of published questions. If the topic is not in the bank yet, ask a doubt instead — it tells us what to ingest next."
          action={
            <Link to="/doubts" className="text-sm font-medium text-brand-600 hover:underline">
              Ask a doubt
            </Link>
          }
        />
      )}

      {!term && (
        <EmptyState
          title="Search the published bank"
          body="Search matches the wording of questions, not topics, so a phrase from the question gets the best result."
        />
      )}

      <ul className="space-y-2">
        {rows.map((result) => (
          <li key={result.id}>
            <ResultCard result={result} />
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * One result, which opens.
 *
 * The loader is enabled only once the row is opened, so a page of twenty results
 * does not fire twenty detail requests - the cost of the full question is paid by
 * the student who actually wants it.
 */
function ResultCard({ result }: { result: SearchResult }) {
  const [open, setOpen] = useState(false)
  const detail = useLoader((signal) => fetchQuestion(result.id, signal), [result.id], open)

  return (
    <Card>
      <p className="text-slate-900">
        <Headline text={result.snippet || result.text} />
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <Badge tone="slate">{result.difficulty}</Badge>
        <Badge tone="brand">{result.marks} marks</Badge>
        {result.subjectName && <span>{result.subjectName}</span>}
        {result.chapterName && <span>· {result.chapterName}</span>}
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="ml-auto font-medium text-brand-600 hover:underline"
        >
          {open ? 'Close' : 'Open'}
        </button>
      </div>

      {open && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          {detail.loading && <Spinner label="Loading the question…" />}
          {detail.error && <ErrorState error={detail.error} what="this question" />}
          {detail.data && <QuestionBody question={detail.data} />}
        </div>
      )}
    </Card>
  )
}

function QuestionBody({ question }: { question: QuestionDetail }) {
  const reveal = question.reveal

  return (
    <div className="space-y-3">
      <p className="text-sm leading-relaxed whitespace-pre-line text-slate-800">{question.text}</p>

      {question.options.length > 0 && (
        <ul className="space-y-1">
          {question.options.map((option) => {
            const isYours = question.yourAnswer === option.label
            const isKey = reveal?.correctAnswer === option.label
            return (
              <li
                key={option.label}
                className={`flex gap-2 rounded-md border px-3 py-2 text-sm ${
                  isKey
                    ? 'border-right-500/40 bg-right-50'
                    : isYours
                      ? 'border-wrong-500/40 bg-wrong-50'
                      : 'border-slate-200'
                }`}
              >
                <span className="font-medium text-slate-500">{option.label}</span>
                <span className="text-slate-800">{option.text}</span>
                {isKey && <span className="ml-auto text-xs text-right-600">answer</span>}
                {isYours && !isKey && <span className="ml-auto text-xs text-wrong-500">yours</span>}
              </li>
            )
          })}
        </ul>
      )}

      {reveal && (
        <div className="rounded-md bg-slate-50 p-3 text-sm">
          <p className="font-medium text-slate-700">
            {question.yourResult === true
              ? 'You got this right.'
              : question.yourResult === false
                ? 'You got this wrong.'
                : 'Your answer is with a reviewer.'}
          </p>
          {reveal.explanation && (
            <p className="mt-1 whitespace-pre-line text-slate-600">{reveal.explanation}</p>
          )}
          {reveal.modelAnswer && (
            <p className="mt-1 whitespace-pre-line text-slate-600">{reveal.modelAnswer}</p>
          )}
        </div>
      )}

      {!reveal && (
        <p className="text-xs text-slate-500">
          {/* Says why the answer is not here, so its absence reads as a rule rather
              than as a broken page. */}
          The worked answer appears once you have answered this question in practice.
        </p>
      )}
    </div>
  )
}
