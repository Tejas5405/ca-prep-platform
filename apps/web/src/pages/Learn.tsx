import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge, Card, EmptyState, ErrorState, SectionHeading, Spinner } from '../components/ui'
import { useLoader } from '../lib/useLoader'
import { fetchChapters, fetchCourses, fetchSubjects, type Course } from '../lib/queries'

/**
 * The syllabus, from a course down to a chapter.
 *
 * WHY THIS PAGE IS MOSTLY NAVIGATION. The product's promise is "practice the
 * chapter you are behind on", and a student cannot do that if they cannot see the
 * syllabus. ICAI's own structure is the map: course, paper, chapter, with each
 * paper's weight in the syllabus shown, because that is how a student decides what
 * to study next when there are eleven weeks left and six papers.
 *
 * Every chapter links into practice and into a mock drawn from it, so the page is
 * a route into the work rather than a description of it.
 */
export default function LearnPage() {
  const [course, setCourse] = useState<Course | null>(null)
  const [subjectId, setSubjectId] = useState('')

  const courses = useLoader((signal) => fetchCourses(signal), [])
  const subjects = useLoader(
    (signal) => (course ? fetchSubjects(course.id, signal) : Promise.resolve([])),
    [course?.id],
    Boolean(course),
  )
  const chapters = useLoader(
    (signal) => (subjectId ? fetchChapters(subjectId, signal) : Promise.resolve([])),
    [subjectId],
    Boolean(subjectId),
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Syllabus</h1>
        <p className="mt-1 text-slate-600">
          The current scheme, course by course. Every chapter opens into practice.
        </p>
      </div>

      {courses.error && <ErrorState error={courses.error} what="the syllabus" />}
      {courses.loading && <Spinner />}

      <div className="grid gap-3 sm:grid-cols-3">
        {(courses.data ?? []).map((item) => {
          const active = course?.id === item.id
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                setCourse(item)
                setSubjectId('')
              }}
              className={`rounded-lg border p-4 text-left transition-colors ${
                active
                  ? 'border-brand-500 bg-brand-50'
                  : 'border-slate-200 bg-white hover:border-brand-500'
              }`}
            >
              <p className="font-medium text-slate-900">{item.name}</p>
              <p className="mt-1 text-xs text-slate-500">
                {item.level} · {item.syllabusScheme.replace('_', ' ')}
              </p>
            </button>
          )
        })}
      </div>

      {course && (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          <Card>
            <SectionHeading title="Papers" subtitle={course.description ?? undefined} />
            {subjects.loading && <Spinner />}
            {subjects.data?.length === 0 && (
              <p className="text-sm text-slate-500">No papers listed for this course yet.</p>
            )}
            <ul className="divide-y divide-slate-100">
              {(subjects.data ?? []).map((subject) => (
                <li key={subject.id}>
                  <button
                    type="button"
                    onClick={() => setSubjectId(subject.id)}
                    className={`flex w-full items-center justify-between gap-3 px-1 py-3 text-left hover:bg-slate-50 ${
                      subjectId === subject.id ? 'bg-brand-50' : ''
                    }`}
                  >
                    <span>
                      <span className="font-medium text-slate-800">
                        Paper {subject.paperNumber} · {subject.name}
                      </span>
                      <span className="mt-0.5 block text-xs text-slate-500">
                        {subject.chapterCount} chapters
                        {subject.groupName ? ` · Group ${subject.groupName}` : ''}
                      </span>
                    </span>
                    {subject.syllabusWeight !== null && (
                      <Badge tone="slate">{subject.syllabusWeight} marks</Badge>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </Card>

          <Card>
            <SectionHeading
              title="Chapters"
              subtitle={
                subjectId
                  ? 'Open a chapter to start practising it.'
                  : 'Choose a paper to see its chapters.'
              }
            />
            {chapters.loading && <Spinner />}
            {!subjectId && (
              <p className="text-sm text-slate-500">
                Papers are listed on the left, with their weight in the syllabus.
              </p>
            )}
            {subjectId && chapters.data?.length === 0 && (
              <EmptyState
                title="No chapters yet"
                body="This paper has not been broken into chapters. Ask a doubt about a specific question and we will prioritise the chapter."
              />
            )}
            <ul className="divide-y divide-slate-100">
              {(chapters.data ?? []).map((chapter) => (
                <li key={chapter.id} className="flex items-center justify-between gap-3 py-3">
                  <span className="min-w-0">
                    <span className="block font-medium text-slate-800">{chapter.name}</span>
                    <span className="mt-0.5 block text-xs text-slate-500">
                      {chapter.estimatedMinutes
                        ? `About ${Math.round(chapter.estimatedMinutes / 60)}h of study`
                        : `Chapter ${chapter.sequence}`}
                      {chapter.weightage ? ` · weight ${chapter.weightage}` : ''}
                    </span>
                  </span>
                  <Link
                    to={`/practice?course=${course.id}&subject=${subjectId}&chapter=${chapter.id}`}
                    className="shrink-0 text-sm font-medium text-brand-600 hover:underline"
                  >
                    Practise
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}

      {!courses.loading && (courses.data ?? []).length === 0 && (
        <EmptyState
          title="No courses published"
          body="The curriculum is seeded from the blueprint's ICAI structure. If this is empty in a deployment, the seed has not been run."
        />
      )}
    </div>
  )
}
