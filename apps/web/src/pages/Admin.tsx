import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge, Button, Card, EmptyState, ErrorState, SectionHeading, Spinner } from '../components/ui'
import { useAuth } from '../hooks/authContext'
import { hasRole } from '../lib/roles'
import { useLoader } from '../lib/useLoader'
import {
  fetchDrafts,
  fetchIngestionJobs,
  fetchSubjects,
  fetchCourses,
  fetchChapters,
  publishQuestion,
  requestUpload,
  reviewDraft,
  startIngestionJob,
  uploadToSignedUrl,
  type DraftSummary,
  type IngestionJob,
} from '../lib/queries'
import { QueryError } from '../lib/queries'

/**
 * The editorial surface: upload a paper, watch the job, clear the QA queue.
 *
 * WHY THIS SCREEN EXISTS AT ALL
 *
 * Every endpoint here was built and tested before any of it was reachable from the
 * app. The chain was real - signed upload, job, extraction, drafts, review,
 * publish - and it could only be driven with curl, which means in practice it was
 * driven by whoever wrote it and then by nobody. A pipeline with no operator
 * interface is not a feature; it is a set of endpoints.
 *
 * THE ORDER IS THE POINT, AND IT IS DELIBERATELY SLOW.
 *
 * Nobody who uploads a paper can publish what comes out of it in one action. The
 * draft is reviewed against its own page image, approving it produces a question in
 * DRAFT with no verifier, and publishing is a separate act with its own permission
 * (Content Manager) and its own name on the record. Clearing an OCR backlog is
 * triage; signing content off for students is not, and the two must not be one
 * button.
 *
 * WHAT THE ROLE CHECK DOES AND DOES NOT DO
 *
 * `hasRole` decides whether this screen renders. It protects nothing: every request
 * below is refused by the API for a caller whose verified token lacks the role, so a
 * student who navigates to /admin sees a refusal page rather than a queue. The UI
 * check exists to avoid showing a screen full of errors, not to be the boundary.
 */
export default function AdminPage() {
  const { role } = useAuth()

  if (!hasRole(role, 'EDITOR')) {
    return (
      <div className="mx-auto max-w-lg py-16 text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Editors only</h1>
        <p className="mt-2 text-slate-600">
          This screen is for the content team. Your account is a {role.toLowerCase()}.
        </p>
        <Link to="/dashboard" className="mt-6 inline-block text-brand-600 hover:underline">
          Back to dashboard
        </Link>
      </div>
    )
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Content</h1>
        <p className="mt-1 text-slate-600">
          Upload source papers, watch extraction, and clear the review queue. Nothing reaches a
          student until a Content Manager publishes it.
        </p>
      </div>

      <UploadCard />
      <JobsCard />
      <ReviewCard canApprove={hasRole(role, 'CONTENT_MANAGER')} />
    </div>
  )
}

// --------------------------------------------------------------------- upload

/**
 * Upload, in the browser, straight to storage.
 *
 * The bytes never pass through the API: the server mints a URL scoped to one path
 * and the browser PUTs to it, which is what keeps a 200 MB scan out of the request
 * worker's memory. The job row is created with the URL, so an upload that fails or
 * is abandoned leaves a visible QUEUED job - evidence, rather than a paper that
 * silently never appeared.
 */
function UploadCard() {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState<string | null>(null)
  const [error, setError] = useState<QueryError | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)

  async function submit() {
    if (!file || busy) return
    setBusy(true)
    setError(null)
    setJobId(null)
    try {
      // 1. ask the API for a place to put it
      setStage('Requesting an upload URL…')
      const ticket = await requestUpload({
        filename: file.name,
        contentType: file.type || 'application/pdf',
        sizeBytes: file.size,
      })

      // 2. PUT the bytes to storage
      setStage('Uploading…')
      await uploadToSignedUrl(ticket, file)

      if (!ticket.jobId) {
        throw new QueryError(
          new Error(
            'The file uploaded, but no ingestion job was created — so there is nothing to start. Try again; if it repeats, the job table is failing.',
          ),
        )
      }

      // 3. confirm it landed and queue extraction
      setStage('Queuing extraction…')
      await startIngestionJob(ticket.jobId)
      setJobId(ticket.jobId)
      setFile(null)
      setStage(null)
    } catch (err: unknown) {
      setError(err as QueryError)
      setStage(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <SectionHeading
        title="Upload a paper"
        subtitle="PDF only. The file goes straight to private storage; extraction runs in the worker."
      />
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <input
          type="file"
          accept="application/pdf,.pdf"
          aria-label="Source PDF"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          className="block w-full max-w-sm text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium file:text-slate-700 hover:file:bg-slate-200"
        />
        <Button onClick={() => void submit()} disabled={!file || busy}>
          {busy ? 'Working…' : 'Upload and extract'}
        </Button>
      </div>

      {stage && <p className="mt-3 text-sm text-slate-500">{stage}</p>}
      {error && (
        <div className="mt-4">
          <ErrorState error={error} what="the upload" />
        </div>
      )}
      {jobId && (
        <p className="mt-4 rounded-md bg-right-50 px-3 py-2 text-sm text-right-600">
          Queued as job {jobId.slice(0, 8)}. It appears in the queue below within a few seconds.
        </p>
      )}
    </Card>
  )
}

// ----------------------------------------------------------------------- jobs

/**
 * The operations queue.
 *
 * Newest first, and a failure shows its reason inline: an operator scanning forty
 * rows needs to see WHICH failed without opening each one. `isTerminal` is what
 * tells the screen to stop implying that something is still happening.
 */
function JobsCard() {
  const [stage, setStage] = useState<string>('')
  const jobs = useLoader(
    (signal) => fetchIngestionJobs({ stage: stage || undefined, limit: 20 }, signal),
    [stage],
  )

  return (
    <Card>
      <SectionHeading
        title="Ingestion queue"
        subtitle="Newest first. A job is done when it stops changing — including when it fails."
        action={
          <label className="text-sm text-slate-600">
            <span className="sr-only">Filter by stage</span>
            <select
              value={stage}
              onChange={(event) => setStage(event.target.value)}
              className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            >
              <option value="">All stages</option>
              {STAGES.map((value) => (
                <option key={value} value={value}>
                  {value.replace('_', ' ').toLowerCase()}
                </option>
              ))}
            </select>
          </label>
        }
      />

      {jobs.loading && <Spinner label="Loading the queue…" />}
      {jobs.error && <ErrorState error={jobs.error} what="the ingestion queue" />}

      {jobs.data && jobs.data.data.length === 0 && (
        <EmptyState
          title="Nothing here yet"
          body="Upload a paper above and it will appear here while extraction runs."
        />
      )}

      {jobs.data && jobs.data.data.length > 0 && (
        <ul className="mt-4 divide-y divide-slate-100">
          {jobs.data.data.map((job) => (
            <JobRow key={job.jobId} job={job} />
          ))}
        </ul>
      )}
    </Card>
  )
}

const STAGES = [
  'QUEUED',
  'DOWNLOADING',
  'EXTRACTING',
  'QUALITY_GATE',
  'OCR_FALLBACK',
  'SEGMENTING',
  'DETECTING_METADATA',
  'AWAITING_QA',
  'PUBLISHED',
  'FAILED',
  'REJECTED',
] as const

function JobRow({ job }: { job: IngestionJob }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm">
      <div className="min-w-0">
        <p className="truncate font-medium text-slate-900">{job.storagePath.split('/').pop()}</p>
        <p className="text-xs text-slate-500">
          {job.draftsCreated} draft{job.draftsCreated === 1 ? '' : 's'}
          {job.pageCount ? ` · ${job.pageCount} pages` : ''}
          {job.extractionTier ? ` · tier ${job.extractionTier}` : ''}
          {job.needsManualReview ? ' · flagged for manual review' : ''}
        </p>
      </div>
      <div className="flex items-center gap-2">
        {job.errorReason && <span className="text-xs text-wrong-500">{job.errorReason}</span>}
        <Badge tone={job.stage === 'FAILED' ? 'wrong' : job.isTerminal ? 'right' : 'brand'}>
          {job.stage.replace('_', ' ').toLowerCase()}
        </Badge>
      </div>
    </li>
  )
}

// --------------------------------------------------------------------- review

/**
 * The QA worklist.
 *
 * Each row is one decision: is this candidate question real, and is what the
 * extractor guessed about it right? Approval requires the fields the DATABASE
 * requires - type and marks above all - because marks drive scoring, and a wrong
 * value there marks every student wrong, permanently and silently.
 *
 * Publishing is shown only to a Content Manager, and only after a draft has been
 * approved: the question it created is still a DRAFT, and publishing is what puts
 * it in front of students.
 */
function ReviewCard({ canApprove }: { canApprove: boolean }) {
  const [status, setStatus] = useState('PENDING')
  const [refresh, setRefresh] = useState(0)
  const drafts = useLoader(
    (signal) => fetchDrafts({ reviewStatus: status, limit: 20 }, signal),
    [status, refresh],
  )

  return (
    <Card>
      <SectionHeading
        title="Review queue"
        subtitle="Oldest first, so nothing at the bottom of the pile is forgotten."
        action={
          <label className="text-sm text-slate-600">
            <span className="sr-only">Filter by review status</span>
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            >
              <option value="PENDING">Pending</option>
              <option value="APPROVED">Approved</option>
              <option value="REJECTED">Rejected</option>
              <option value="DUPLICATE">Duplicate</option>
            </select>
          </label>
        }
      />

      {drafts.loading && <Spinner label="Loading the queue…" />}
      {drafts.error && <ErrorState error={drafts.error} what="the review queue" />}

      {drafts.data && drafts.data.data.length === 0 && (
        <EmptyState
          title={status === 'PENDING' ? 'Queue is clear' : 'Nothing with that status'}
          body={
            status === 'PENDING'
              ? 'Every extracted question has been reviewed. Upload another paper to add more.'
              : 'Try a different status, or upload a paper to create new drafts.'
          }
        />
      )}

      {drafts.data && drafts.data.data.length > 0 && (
        <ul className="mt-4 space-y-4">
          {drafts.data.data.map((draft) => (
            <DraftRow
              key={draft.draftId}
              draft={draft}
              canApprove={canApprove}
              onDecided={() => setRefresh((value) => value + 1)}
            />
          ))}
        </ul>
      )}
    </Card>
  )
}

function DraftRow({
  draft,
  canApprove,
  onDecided,
}: {
  draft: DraftSummary
  canApprove: boolean
  onDecided: () => void
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<QueryError | null>(null)
  const [outcome, setOutcome] = useState<string | null>(null)
  const [publishedId, setPublishedId] = useState<string | null>(null)

  // Placement is required to approve: a question filed under nothing appears in no
  // chapter, so no student can ever practise it. Prefilled from the job's guesses.
  const courses = useLoader((signal) => fetchCourses(signal), [], open)
  const [courseId, setCourseId] = useState('')
  const subjects = useLoader(
    (signal) =>
      courseId ? fetchSubjects(courseId, signal, { includeParents: true }) : Promise.resolve([]),
    [courseId],
    open && Boolean(courseId),
  )
  const [subjectId, setSubjectId] = useState('')
  const chapters = useLoader(
    (signal) => (subjectId ? fetchChapters(subjectId, signal) : Promise.resolve([])),
    [subjectId],
    open && Boolean(subjectId),
  )
  const [chapterId, setChapterId] = useState('')

  const [questionType, setQuestionType] = useState(draft.detectedQuestionType ?? 'DESCRIPTIVE')
  const [marks, setMarks] = useState(draft.detectedMarks ?? 4)
  const [explanation, setExplanation] = useState('')

  async function decide(decision: 'APPROVE' | 'REJECT' | 'DUPLICATE') {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const result = await reviewDraft(draft.draftId, {
        decision,
        ...(decision === 'APPROVE'
          ? {
              subjectId: subjectId || undefined,
              chapterId: chapterId || undefined,
              questionType,
              marks,
              explanation: explanation || undefined,
            }
          : {}),
      })
      setOutcome(decision)
      // Approving creates a DRAFT question. Publishing it is the next act, and the
      // id comes back so the reviewer can do it here rather than hunting for it.
      const created = result.questionId
      if (typeof created === 'string') setPublishedId(created)
      onDecided()
    } catch (err: unknown) {
      setError(err as QueryError)
    } finally {
      setBusy(false)
    }
  }

  async function publish() {
    if (!publishedId || busy) return
    setBusy(true)
    setError(null)
    try {
      await publishQuestion(publishedId)
      setOutcome('PUBLISHED')
      onDecided()
    } catch (err: unknown) {
      setError(err as QueryError)
    } finally {
      setBusy(false)
    }
  }

  return (
    <li>
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="slate">page {draft.sourcePage ?? '?'}</Badge>
          {draft.detectedYear && <Badge tone="slate">{draft.detectedYear}</Badge>}
          {draft.detectedMarks != null && <Badge tone="slate">{draft.detectedMarks} marks</Badge>}
          {draft.detectedQuestionType && <Badge tone="brand">{draft.detectedQuestionType}</Badge>}
          {draft.detectionConfidence != null && (
            <span className="text-xs text-slate-500">
              {/* Labelled as a guess: it is a regex read of OCR text, and the editor
                  confirms or corrects it. */}
              detector {Math.round(draft.detectionConfidence * 100)}% sure
            </span>
          )}
          <span className="ml-auto text-xs text-slate-400">
            {new Date(draft.createdAt).toLocaleDateString('en-IN')}
          </span>
        </div>

        <p className="mt-3 text-sm leading-relaxed text-slate-800 whitespace-pre-line">
          {draft.preview}
        </p>

        {outcome && (
          <p className="mt-3 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-600">
            {outcome === 'PUBLISHED'
              ? 'Published. Students can practise it now.'
              : outcome === 'APPROVE'
                ? 'Approved — the question is created in DRAFT. Publishing it is the next step.'
                : 'Recorded and removed from the queue.'}
          </p>
        )}

        {error && (
          <div className="mt-3">
            <ErrorState error={error} what="the review" />
          </div>
        )}

        {publishedId && outcome === 'APPROVE' && canApprove && (
          <div className="mt-3">
            <Button onClick={() => void publish()} disabled={busy}>
              Publish this question
            </Button>
          </div>
        )}

        {draft.reviewStatus === 'PENDING' && !outcome && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {/* Approving is the only path that opens the form: rejection needs no
                placement, so it stays a one-click decision. */}
            <Button
              tone="secondary"
              onClick={() => setOpen((value) => !value)}
              disabled={!canApprove}
              title={!canApprove ? 'Approving is a Content Manager action.' : ''}
            >
              {open ? 'Cancel' : 'Approve…'}
            </Button>
            <Button tone="quiet" onClick={() => void decide('REJECT')} disabled={busy}>
              Reject
            </Button>
            <Button tone="quiet" onClick={() => void decide('DUPLICATE')} disabled={busy}>
              Duplicate
            </Button>
          </div>
        )}

        {open && canApprove && (
          <div className="mt-4 space-y-3 rounded-md border border-slate-200 p-3">
            <p className="text-xs text-slate-500">
              Placement and marks are required. Marks drive scoring — a wrong value here marks every
              student wrong, silently and permanently.
            </p>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Course">
                <select
                  value={courseId}
                  onChange={(event) => {
                    setCourseId(event.target.value)
                    setSubjectId('')
                    setChapterId('')
                  }}
                  className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
                >
                  <option value="">Choose…</option>
                  {(courses.data ?? []).map((course) => (
                    <option key={course.id} value={course.id}>
                      {course.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Paper">
                <select
                  value={subjectId}
                  disabled={!courseId}
                  onChange={(event) => {
                    setSubjectId(event.target.value)
                    setChapterId('')
                  }}
                  className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm disabled:bg-slate-50"
                >
                  <option value="">Choose…</option>
                  {(subjects.data ?? [])
                    .filter((subject) => subject.type !== "subject_component")
                    .map((subject) => (
                    <option key={subject.id} value={subject.id}>
                      {subject.paperNumber}. {subject.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Chapter">
                <select
                  value={chapterId}
                  disabled={!subjectId}
                  onChange={(event) => setChapterId(event.target.value)}
                  className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm disabled:bg-slate-50"
                >
                  <option value="">Choose…</option>
                  {(chapters.data ?? []).map((chapter) => (
                    <option key={chapter.id} value={chapter.id}>
                      {chapter.name}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Type">
                <select
                  value={questionType}
                  onChange={(event) => setQuestionType(event.target.value)}
                  className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
                >
                  {TYPES.map((value) => (
                    <option key={value} value={value}>
                      {value.replace('_', ' ').toLowerCase()}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Marks">
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={marks}
                  onChange={(event) => setMarks(Number(event.target.value))}
                  className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
                />
              </Field>
              <Field label="Explanation (optional)">
                <input
                  type="text"
                  value={explanation}
                  onChange={(event) => setExplanation(event.target.value)}
                  className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
                />
              </Field>
            </div>
            <Button
              onClick={() => void decide('APPROVE')}
              disabled={busy || !subjectId}
              title={!subjectId ? 'Choose a paper first: an unplaced question reaches no student.' : ''}
            >
              {busy ? 'Saving…' : 'Approve and create the question'}
            </Button>
          </div>
        )}
      </Card>
    </li>
  )
}

const TYPES = ['MCQ', 'MSQ', 'TRUE_FALSE', 'NUMERICAL', 'DESCRIPTIVE', 'CASE_STUDY'] as const

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="font-medium text-slate-700">{label}</span>
      <span className="mt-1 block">{children}</span>
    </label>
  )
}
