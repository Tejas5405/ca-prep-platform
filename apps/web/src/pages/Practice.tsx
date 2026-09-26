import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { Badge, Button, Card, EmptyState, ErrorState, SectionHeading, Spinner } from '../components/ui'
import {
  fetchChapters,
  fetchCourses,
  fetchPracticeQuestions,
  fetchSubjects,
  setBookmark,
  reportQuestion,
  submitAnswer,
  type AnswerResult,
  type Question,
} from '../lib/queries'
import { useLoader } from '../lib/useLoader'
import { QueryError } from '../lib/queries'

/**
 * Practice, from the curriculum down to one question at a time.
 *
 * THE DRILL-DOWN IS THE POINT. A student opens this page to answer questions, not
 * to choose a course, so the selection state is kept in the URL
 * (`?course=&subject=&chapter=`). That makes a practice set linkable, survivable
 * across a refresh, and reachable from the curriculum page with one click - none
 * of which is true of state held in a component.
 *
 * THE ANSWER NEVER LEAVES THE SERVER BEFORE IT IS GIVEN. The questions endpoint
 * returns options with no `isCorrect` flag; the key arrives in the RESPONSE to an
 * answer and is only then stitched into the option list. Reading it from the
 * question payload would be a simpler component and a compromised question bank.
 */
/**
 * Seconds since a question was first shown.
 *
 * A module-level function rather than an inline expression: reading the clock
 * inside a component body is impure, and the React compiler rejects it. Hoisting it
 * makes the impurity explicit and keeps the component a pure function of its props
 * and state.
 */
function secondsSince(startedAt: number): number {
  return Math.max(1, Math.round((Date.now() - startedAt) / 1000))
}

export default function PracticePage() {
  const [params, setParams] = useSearchParams()

  const courseId = params.get('course') ?? ''
  const subjectId = params.get('subject') ?? ''
  const chapterId = params.get('chapter') ?? ''
  const selected = Boolean(chapterId || subjectId)
  // Bumping this re-draws a set from the same chapter, which is how a student asks
  // for "ten more" without navigating.
  const [draw, setDraw] = useState(0)

  const courses = useLoader((signal) => fetchCourses(signal), [])
  const subjects = useLoader(
    (signal) => (courseId ? fetchSubjects(courseId, signal) : Promise.resolve([])),
    [courseId],
    Boolean(courseId),
  )
  const chapters = useLoader(
    (signal) => (subjectId ? fetchChapters(subjectId, signal) : Promise.resolve([])),
    [subjectId],
    Boolean(subjectId),
  )
  const set = useLoader(
    (signal) =>
      fetchPracticeQuestions(
        {
          ...(chapterId ? { chapterId } : {}),
          ...(subjectId ? { subjectId } : {}),
          limit: 10,
        },
        signal,
      ),
    [chapterId, subjectId, draw],
    selected,
  )

  const questions = set.data ?? []

  function select(key: 'course' | 'subject' | 'chapter', value: string) {
    const next = new URLSearchParams(params)
    next.set(key, value)
    // Changing a parent invalidates its children: leaving a chapter id in the URL
    // draws a set from a chapter the student is no longer looking at.
    if (key === 'course') next.delete('subject')
    if (key === 'course' || key === 'subject') next.delete('chapter')
    setParams(next)
  }

  const chapterName = useMemo(
    () => chapters.data?.find((c) => c.id === chapterId)?.name ?? null,
    [chapters.data, chapterId],
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Practice</h1>
        <p className="mt-1 text-slate-600">
          Pick a chapter and answer one question at a time. Wrong answers and bookmarks go to your
          revision queue automatically.
        </p>
      </div>

      {courses.error && <ErrorState error={courses.error} what="the curriculum" />}

      <Card>
        <div className="grid gap-4 sm:grid-cols-3">
          <Select
            label="Course"
            value={courseId}
            onChange={(value) => select('course', value)}
            placeholder="Choose a course…"
            options={(courses.data ?? []).map((course) => ({
              value: course.id,
              label: course.name,
            }))}
          />
          <Select
            label="Paper"
            value={subjectId}
            onChange={(value) => select('subject', value)}
            placeholder={courseId ? 'Choose a paper…' : 'Pick a course first'}
            disabled={!courseId}
            options={(subjects.data ?? []).map((subject) => ({
              value: subject.id,
              label: `${subject.paperNumber}. ${subject.name}`,
            }))}
          />
          <Select
            label="Chapter"
            value={chapterId}
            onChange={(value) => select('chapter', value)}
            placeholder={subjectId ? 'Choose a chapter…' : 'Pick a paper first'}
            disabled={!subjectId}
            options={(chapters.data ?? []).map((chapter) => ({
              value: chapter.id,
              label: chapter.name,
            }))}
          />
        </div>
      </Card>

      {!selected && (
        <EmptyState
          title="Nothing selected yet"
          body="Choose a course, a paper and a chapter. A set of up to ten questions is drawn from that chapter."
        />
      )}

      {set.loading && <Spinner label="Drawing questions…" />}
      {set.error && <ErrorState error={set.error} what="this practice set" />}

      {selected && !set.loading && questions.length === 0 && (
        <EmptyState
          title="No questions here yet"
          body="This chapter has no published questions. The bank grows as papers are ingested and verified — try another chapter, or ask a doubt and we will prioritise it."
          action={
            <Link to="/doubts" className="text-sm font-medium text-brand-600 hover:underline">
              Ask a doubt
            </Link>
          }
        />
      )}

      {set.data && (
        <QuestionSet
          key={`${chapterId || subjectId}-${draw}`}
          questions={questions}
          chapterName={chapterName}
          onDrawMore={() => setDraw((value) => value + 1)}
        />
      )}
    </div>
  )
}

function Select({
  label,
  value,
  onChange,
  options,
  placeholder,
  disabled = false,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
  placeholder: string
  disabled?: boolean
}) {
  return (
    <label className="block text-sm">
      <span className="font-medium text-slate-700">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-sm disabled:bg-slate-50"
      >
        <option value="">{placeholder}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}

/**
 * One question at a time.
 *
 * Mounted with a `key` that changes with the practice set, so the index, the graded
 * result and the bookmark flag start clean without a reset effect. The timer for
 * "time spent" starts on the first render of each question rather than in an
 * effect, because a lazy initialiser is the one place an impure clock read is
 * allowed - this component exists to be remounted.
 */
function QuestionSet({
  questions,
  chapterName,
  onDrawMore,
}: {
  questions: Question[]
  chapterName: string | null
  onDrawMore: () => void
}) {
  const [index, setIndex] = useState(0)
  const [result, setResult] = useState<AnswerResult | null>(null)
  const [chosen, setChosen] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<QueryError | null>(null)
  const [bookmarked, setBookmarked] = useState(false)
  const [askedAt, setAskedAt] = useState(() => Date.now())
  const [reportNote, setReportNote] = useState('')
  const [reported, setReported] = useState(false)

  const current = questions[index] ?? null

  function goTo(next: number) {
    setIndex(next)
    setResult(null)
    setChosen(null)
    setError(null)
    setBookmarked(false)
    setAskedAt(Date.now())
    setReportNote('')
    setReported(false)
  }

  async function answer(question: Question, label: string) {
    if (result || busy) return
    setChosen(label)
    setBusy(true)
    setError(null)
    try {
      setResult(await submitAnswer(question.id, label, secondsSince(askedAt)))
    } catch (err: unknown) {
      setError(err as QueryError)
      // Let the student try again rather than stranding them on a dead option.
      setChosen(null)
    } finally {
      setBusy(false)
    }
  }

  async function toggleBookmark(question: Question) {
    const next = !bookmarked
    setBookmarked(next)
    try {
      await setBookmark(question.id, next)
    } catch {
      setBookmarked(!next)
    }
  }

  if (!current) {
    return (
      <Card>
        <SectionHeading
          title="Set complete"
          subtitle="That is every question drawn from this chapter."
        />
        <div className="flex gap-4">
          <Button onClick={onDrawMore}>Draw another set</Button>
          <Link
            to="/revision"
            className="self-center text-sm font-medium text-brand-600 hover:underline"
          >
            Go to revision
          </Link>
        </div>
      </Card>
    )
  }

  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <span>
            Question {index + 1} of {questions.length}
          </span>
          {chapterName && <span aria-hidden>·</span>}
          {chapterName && <span>{chapterName}</span>}
        </div>
        <div className="flex items-center gap-2">
          <Badge tone="slate">{current.difficulty}</Badge>
          <Badge tone="brand">{current.marks} marks</Badge>
          <Button tone="quiet" onClick={() => void toggleBookmark(current)}>
            {bookmarked ? '★ Bookmarked' : '☆ Bookmark'}
          </Button>
        </div>
      </div>

      <p className="text-base leading-relaxed text-slate-900">{current.text}</p>

      <ul className="mt-5 space-y-2">
        {current.options.map((option) => {
          // Once the key has arrived, the option list is rebuilt FROM the response,
          // so the explanation and the correct option come from the server rather
          // than from a second, divergent local guess.
          const graded = result?.options.find((o) => o.label === option.label)
          const isChosen = chosen === option.label
          const tone = !result
            ? isChosen
              ? 'border-brand-500 bg-brand-50'
              : 'border-slate-200 hover:border-brand-500 hover:bg-brand-50'
            : graded?.isCorrect
              ? 'border-right-500 bg-right-50'
              : isChosen
                ? 'border-wrong-500 bg-wrong-50'
                : 'border-slate-200'

          return (
            <li key={option.label}>
              <button
                type="button"
                onClick={() => void answer(current, option.label)}
                disabled={Boolean(result) || busy}
                aria-pressed={isChosen}
                className={`flex w-full items-start gap-3 rounded-md border px-3 py-2.5 text-left transition-colors disabled:cursor-default ${tone}`}
              >
                <span className="mt-0.5 font-semibold text-slate-500">{option.label}</span>
                <span className="text-slate-800">{option.text}</span>
                {result && graded?.isCorrect && (
                  <span className="ml-auto text-xs font-medium text-right-600">Correct</span>
                )}
                {result && isChosen && !graded?.isCorrect && (
                  <span className="ml-auto text-xs font-medium text-wrong-500">Your answer</span>
                )}
              </button>
            </li>
          )
        })}
      </ul>

      {error && (
        <div className="mt-4">
          <ErrorState error={error} what="the answer" />
        </div>
      )}

      {result && (
        <div className="mt-5 rounded-md border border-slate-200 bg-slate-50 p-4">
          <p className={`font-medium ${result.isCorrect ? 'text-right-600' : 'text-wrong-500'}`}>
            {result.isCorrect ? 'Correct' : 'Not quite'}
            {result.pointsAwarded > 0 && ` · +${result.pointsAwarded} points`}
          </p>
          {result.explanation && (
            <p className="mt-2 text-sm leading-relaxed text-slate-700">{result.explanation}</p>
          )}
          <p className="mt-2 text-xs text-slate-500">
            {result.attemptsCount} answered · {Math.round((result.accuracy ?? 0) * 100)}% accuracy ·
            streak {result.currentStreak}
          </p>
          {reported ? (
            <p className="mt-3 text-sm text-slate-600">
              Reported. An editor can see it. The answer key was not changed.
            </p>
          ) : (
            <form
              className="mt-3 flex flex-wrap items-end gap-2"
              onSubmit={(event) => {
                event.preventDefault()
                void reportQuestion(current.id, 'WRONG_ANSWER', reportNote || 'Reported from practice.')
                  .then(() => setReported(true))
                  .catch(() => setReported(false))
              }}
            >
              <label className="text-xs text-slate-600">
                Report a problem with this question
                <input
                  className="mt-1 block rounded-md border border-slate-300 px-2 py-1 text-sm"
                  value={reportNote}
                  onChange={(event) => setReportNote(event.target.value)}
                  placeholder="What looks wrong"
                />
              </label>
              <Button type="submit" tone="secondary">
                Send report
              </Button>
            </form>
          )}
        </div>
      )}

      <div className="mt-5 flex items-center justify-between">
        <Button tone="secondary" disabled={index === 0} onClick={() => goTo(index - 1)}>
          Previous
        </Button>
        <Button
          disabled={index >= questions.length - 1}
          onClick={() => goTo(index + 1)}
        >
          Next question
        </Button>
      </div>
    </Card>
  )
}
