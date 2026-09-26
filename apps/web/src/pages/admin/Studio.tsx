/**
 * The six sections that were grey because a screen would have been a lie.
 *
 * Question bank, test series and syllabus write through the new admin routes.
 * Plans save a price checkout charges. AI does not generate. Storage names
 * the missing credential instead of inventing a usage number.
 */

import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Spinner,
} from "../../components/ui";
import { useLoader } from "../../lib/useLoader";
import {
  createAdminCourse,
  createAdminMock,
  createAdminQuestion,
  fetchAdminAi,
  fetchAdminCurriculum,
  fetchAdminMocks,
  fetchAdminPlans,
  fetchUnclassified,
  recordLawNotice,
  saveAdminPlan,
  assignQuestionComponent,
  fetchAdminQuestions,
  fetchAdminStorage,
  fetchCourses,
  fetchQuestionFlags,
  fetchSubjects,
  publishMock,
  publishQuestion,
  resolveQuestionFlag,
  retireAdminCourse,
  type AdminQuestion,
} from "../../lib/queries";

function ClassificationQueue() {
  const queue = useLoader((signal) => fetchUnclassified(signal), []);
  const [citation, setCitation] = useState("");
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  async function assign(questionId: string, componentId: string) {
    setFormError(null);
    setBusy(questionId);
    try {
      const result = await assignQuestionComponent(questionId, componentId);
      setNote(
        result.answerChanged
          ? "The server reported an answer change. That should not happen."
          : `Assigned ${result.componentCode}. The answer was not changed.`,
      );
      queue.reload();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Could not assign.");
    } finally {
      setBusy(null);
    }
  }

  async function record() {
    setFormError(null);
    setNote(null);
    if (citation.trim().length < 12 || summary.trim().length < 8) {
      setFormError("A citation needs 12 characters and a summary needs 8. This does not invent a statute.");
      return;
    }
    setBusy("notice");
    try {
      const result = await recordLawNotice(citation.trim(), summary.trim());
      setNote(
        `Matched ${result.matched} published question${result.matched === 1 ? "" : "s"}. ` +
          `Flagged ${result.flagged}. Answers changed: ${result.answersChanged ? "yes" : "no"}.`,
      );
      setCitation("");
      setSummary("");
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Could not record the notice.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <h2 className="text-sm font-medium text-slate-900">Intermediate splits and law notices</h2>
      <p className="mt-1 text-sm text-slate-600">
        A question with no split does not appear in both Direct Tax and Indirect Tax.
        A law notice flags questions whose text contains the citation you type. It does not rewrite the answer.
      </p>
      {queue.loading && <Spinner label="Loading the classification queue" />}
      {queue.error && <ErrorState error={queue.error} what="the classification queue" />}
      {queue.data && (
        <p className="mt-2 text-sm text-slate-700">
          {queue.data.total} published question{queue.data.total === 1 ? "" : "s"} still unassigned.
        </p>
      )}
      <ul className="mt-3 space-y-2">
        {(queue.data?.questions ?? []).slice(0, 8).map((question) => (
          <li key={question.id} className="rounded-md border border-slate-200 p-2 text-sm">
            <p className="text-slate-800">{question.text}</p>
            <p className="mt-1 text-xs text-slate-500">{question.subjectName}</p>
            <select
              aria-label={`Assign a split to ${question.id}`}
              className="mt-2 rounded-md border border-slate-300 px-2 py-1 text-sm"
              defaultValue=""
              disabled={busy === question.id}
              onChange={(event) => {
                if (event.target.value) assign(question.id, event.target.value);
              }}
            >
              <option value="">Assign a split</option>
              {(queue.data?.components ?? [])
                .filter((component) => question.subjectCode === "INT_TAX"
                  ? component.code === "DIRECT_TAX" || component.code === "INDIRECT_TAX"
                  : component.code === "FINANCIAL_MANAGEMENT" || component.code === "STRATEGIC_MANAGEMENT")
                .map((component) => (
                  <option key={component.id} value={component.id}>
                    {component.name}
                  </option>
                ))}
            </select>
          </li>
        ))}
      </ul>
      <div className="mt-4 grid gap-2">
        <input
          aria-label="Citation"
          className="rounded-md border border-slate-300 px-2 py-2 text-sm"
          placeholder="Exact citation, at least 12 characters"
          value={citation}
          onChange={(event) => setCitation(event.target.value)}
        />
        <input
          aria-label="Notice summary"
          className="rounded-md border border-slate-300 px-2 py-2 text-sm"
          placeholder="What an editor should review. The answer is not changed."
          value={summary}
          onChange={(event) => setSummary(event.target.value)}
        />
        <Button tone="quiet" disabled={busy === "notice"} onClick={record}>
          Record law notice
        </Button>
      </div>
      {formError && <p className="mt-2 text-sm text-red-700">{formError}</p>}
      {note && <p className="mt-2 text-sm text-slate-700">{note}</p>}
    </Card>
  );
}

function Shell({
  title,
  purpose,
  children,
}: {
  title: string;
  purpose: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">{purpose}</p>
      </header>
      {children}
    </section>
  );
}

export function QuestionBank() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [text, setText] = useState("");
  const [courseId, setCourseId] = useState("");
  const [subjectId, setSubjectId] = useState("");
  const [questionType, setQuestionType] = useState("MCQ");
  const [answer, setAnswer] = useState("");
  const [optionA, setOptionA] = useState("");
  const [optionB, setOptionB] = useState("");
  const [historical, setHistorical] = useState(false);
  const [disclaimer, setDisclaimer] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const courses = useLoader((signal) => fetchCourses(signal), []);
  const subjects = useLoader(
    (signal) =>
      courseId
        ? fetchSubjects(courseId, signal, { includeParents: true })
        : Promise.resolve([]),
    [courseId],
  );
  const bank = useLoader(
    (signal) =>
      fetchAdminQuestions(
        { ...(q.trim() ? { q: q.trim() } : {}), ...(status ? { status } : {}) },
        signal,
      ),
    [q, status],
  );
  const flags = useLoader((signal) => fetchQuestionFlags(signal), []);

  async function create() {
    setFormError(null);
    if (!courseId || !subjectId || !text.trim()) {
      setFormError("A question needs a course, a subject and text.");
      return;
    }
    setBusy("create");
    try {
      const choices = [
        optionA.trim() ? { label: "A", text: optionA.trim() } : null,
        optionB.trim() ? { label: "B", text: optionB.trim() } : null,
      ].filter((item): item is { label: string; text: string } => item !== null);
      await createAdminQuestion({
        course_id: courseId,
        subject_id: subjectId,
        text: text.trim(),
        question_type: questionType,
        ...(answer.trim() ? { correct_answer: answer.trim() } : {}),
        is_historical: historical,
        ...(disclaimer.trim() ? { disclaimer_text: disclaimer.trim() } : {}),
        ...(choices.length ? { options: choices } : {}),
      });
      setText("");
      setAnswer("");
      bank.reload();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Could not save.");
    } finally {
      setBusy(null);
    }
  }

  async function publish(row: AdminQuestion) {
    setBusy(row.id);
    setFormError(null);
    try {
      await publishQuestion(row.id);
      bank.reload();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Could not publish.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Shell
      title="Question bank"
      purpose="Drafts are created here. Publishing goes through the verifier route, which records who signed the answer. A live question is not edited on this screen."
    >
      <ClassificationQueue />
      <Card>
        <h2 className="text-sm font-medium text-slate-900">New draft</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm text-slate-700">
            Course
            <select
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2"
              value={courseId}
              onChange={(event) => {
                setCourseId(event.target.value);
                setSubjectId("");
              }}
            >
              <option value="">Choose</option>
              {(courses.data ?? []).map((course) => (
                <option key={course.id} value={course.id}>
                  {course.code} — {course.name}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm text-slate-700">
            Subject
            <select
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2"
              value={subjectId}
              onChange={(event) => setSubjectId(event.target.value)}
            >
              <option value="">Choose</option>
              {(subjects.data ?? [])
                .filter((subject) => subject.type !== "subject_component")
                .map((subject) => (
                <option key={subject.id} value={subject.id}>
                  {subject.code} — {subject.name}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm text-slate-700">
            Type
            <select
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2"
              value={questionType}
              onChange={(event) => setQuestionType(event.target.value)}
            >
              {["MCQ", "MSQ", "TRUE_FALSE", "NUMERICAL", "DESCRIPTIVE", "CASE_STUDY"].map(
                (type) => (
                  <option key={type}>{type}</option>
                ),
              )}
            </select>
          </label>
          <label className="text-sm text-slate-700">
            Correct answer
            <input
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2"
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
              placeholder="A label such as B, or a number"
            />
          </label>
          <label className="text-sm text-slate-700">
            Option A
            <input
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2"
              value={optionA}
              onChange={(event) => setOptionA(event.target.value)}
            />
          </label>
          <label className="text-sm text-slate-700">
            Option B
            <input
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2"
              value={optionB}
              onChange={(event) => setOptionB(event.target.value)}
            />
          </label>
        </div>
        <label className="mt-3 block text-sm text-slate-700">
          Question text
          <textarea
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2"
            rows={4}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </label>
        <label className="mt-3 flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={historical}
            onChange={(event) => setHistorical(event.target.checked)}
          />
          Historical taxation — needs a disclaimer
        </label>
        {historical && (
          <label className="mt-2 block text-sm text-slate-700">
            Disclaimer
            <input
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2"
              value={disclaimer}
              onChange={(event) => setDisclaimer(event.target.value)}
              placeholder="This question uses law from an earlier Finance Act."
            />
          </label>
        )}
        {formError && <p className="mt-2 text-sm text-red-700">{formError}</p>}
        <div className="mt-3">
          <Button onClick={create} disabled={busy === "create"}>
            Save draft
          </Button>
        </div>
      </Card>

      <div className="flex flex-wrap gap-2">
        <input
          className="rounded-md border border-slate-300 px-2 py-2 text-sm"
          placeholder="Search question text"
          value={q}
          onChange={(event) => setQ(event.target.value)}
          aria-label="Search questions"
        />
        <select
          className="rounded-md border border-slate-300 px-2 py-2 text-sm"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          {["DRAFT", "IN_REVIEW", "APPROVED", "PUBLISHED", "ARCHIVED"].map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
      </div>

      {bank.loading && <Spinner label="Loading questions" />}
      {bank.error && <ErrorState error={bank.error} what="the question bank" />}
      {bank.data && bank.data.data.length === 0 && (
        <EmptyState title="No questions" body="Nothing matches that filter." />
      )}
      <ul className="space-y-2">
        {(bank.data?.data ?? []).map((row) => (
          <li key={row.id} className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={row.status === "PUBLISHED" ? "right" : "slate"}>{row.status}</Badge>
              <span className="text-xs text-slate-500">{row.questionType}</span>
              {row.isHistorical && <Badge tone="amber">Historical</Badge>}
            </div>
            <p className="mt-2 whitespace-pre-wrap text-sm text-slate-800">{row.text}</p>
            {row.status !== "PUBLISHED" && row.status !== "ARCHIVED" && (
              <div className="mt-2">
                <Button
                  tone="secondary"
                  disabled={busy === row.id}
                  onClick={() => publish(row)}
                >
                  Publish
                </Button>
              </div>
            )}
          </li>
        ))}
      </ul>
      <Card>
        <h2 className="text-sm font-medium text-slate-900">Reports</h2>
        <p className="mt-1 text-sm text-slate-600">
          A report does not change the question. Correcting a published question keeps the version
          the student sat.
        </p>
        {flags.loading && <Spinner label="Loading reports" />}
        {flags.error && <ErrorState error={flags.error} what="question reports" />}
        {(flags.data?.flags ?? []).length === 0 && !flags.loading && !flags.error && (
          <p className="mt-2 text-sm text-slate-500">No open reports.</p>
        )}
        <ul className="mt-3 space-y-2">
          {(flags.data?.flags ?? []).map((flag) => (
            <li key={flag.id} className="text-sm text-slate-700">
              <span className="font-medium">{flag.reason}</span> — {flag.questionText}
              <div className="mt-1">
                <Button
                  tone="secondary"
                  onClick={() => {
                    void resolveQuestionFlag(flag.id, "TRIAGED").then(() => flags.reload());
                  }}
                >
                  Mark triaged
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </Shell>
  );
}

export function TestSeries() {
  const [title, setTitle] = useState("");
  const [courseId, setCourseId] = useState("");
  const [kind, setKind] = useState("FULL_LENGTH");
  const [duration, setDuration] = useState("180");
  const [marks, setMarks] = useState("100");
  const [questionIds, setQuestionIds] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const courses = useLoader((signal) => fetchCourses(signal), []);
  const papers = useLoader((signal) => fetchAdminMocks(signal), []);

  async function create() {
    setFormError(null);
    if (!courseId || !title.trim()) {
      setFormError("A paper needs a course and a title.");
      return;
    }
    const ids = questionIds
      .split(/[\s,]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    setBusy(true);
    try {
      await createAdminMock({
        course_id: courseId,
        title: title.trim(),
        kind,
        duration_min: Number(duration) || 180,
        total_marks: Number(marks) || 100,
        ...(ids.length ? { question_ids: ids } : {}),
      });
      setTitle("");
      setQuestionIds("");
      papers.reload();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  async function publish(id: string) {
    setFormError(null);
    setBusy(true);
    try {
      await publishMock(id);
      papers.reload();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Could not publish.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell
      title="Test series"
      purpose="A new paper is a draft. Publishing uses the existing route, which refuses a paper whose questions are not all published. A published paper is not edited here."
    >
      <Card>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-sm text-slate-700">
            Title
            <input
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <label className="text-sm text-slate-700">
            Course
            <select
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2"
              value={courseId}
              onChange={(event) => setCourseId(event.target.value)}
            >
              <option value="">Choose</option>
              {(courses.data ?? []).map((course) => (
                <option key={course.id} value={course.id}>
                  {course.name}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm text-slate-700">
            Kind
            <select
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2"
              value={kind}
              onChange={(event) => setKind(event.target.value)}
            >
              {["CHAPTER", "SUBJECT", "FULL_LENGTH", "PREVIOUS_PAPER", "CUSTOM"].map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
          <label className="text-sm text-slate-700">
            Duration (minutes)
            <input
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2"
              value={duration}
              onChange={(event) => setDuration(event.target.value)}
            />
          </label>
          <label className="text-sm text-slate-700">
            Total marks
            <input
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2"
              value={marks}
              onChange={(event) => setMarks(event.target.value)}
            />
          </label>
        </div>
        <label className="mt-3 block text-sm text-slate-700">
          Question ids, if you already have published ones
          <input
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 font-mono text-xs"
            value={questionIds}
            onChange={(event) => setQuestionIds(event.target.value)}
            placeholder="uuid, uuid"
          />
        </label>
        {formError && <p className="mt-2 text-sm text-red-700">{formError}</p>}
        <div className="mt-3">
          <Button onClick={create} disabled={busy}>
            Save draft paper
          </Button>
        </div>
      </Card>
      {papers.loading && <Spinner label="Loading papers" />}
      {papers.error && <ErrorState error={papers.error} what="the papers" />}
      <ul className="space-y-2">
        {(papers.data ?? []).map((paper) => (
          <li key={paper.id} className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-slate-900">{paper.title}</span>
              <Badge tone={paper.status === "PUBLISHED" ? "right" : "slate"}>{paper.status}</Badge>
              <span className="text-xs text-slate-500">
                {paper.questionCount} questions · {paper.durationMin} min · {paper.totalMarks} marks
              </span>
            </div>
            {paper.status !== "PUBLISHED" && (
              <div className="mt-2">
                <Button tone="secondary" disabled={busy} onClick={() => publish(paper.id)}>
                  Publish
                </Button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </Shell>
  );
}

export function CurriculumAdmin() {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [level, setLevel] = useState("FOUNDATION");
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const tree = useLoader((signal) => fetchAdminCurriculum(signal), []);

  async function create() {
    setFormError(null);
    setBusy(true);
    try {
      await createAdminCourse({ code: code.trim(), name: name.trim(), level });
      setCode("");
      setName("");
      tree.reload();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  async function retire(id: string, isActive: boolean) {
    setFormError(null);
    try {
      await retireAdminCourse(id, isActive);
      tree.reload();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Could not update.");
    }
  }

  return (
    <Shell
      title="Courses and syllabus"
      purpose="Inactive rows stay in this list. They are hidden from students. A course is retired, not deleted, because questions reference it."
    >
      <Card>
        <h2 className="text-sm font-medium text-slate-900">Add a course</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <input
            className="rounded-md border border-slate-300 px-2 py-2 text-sm"
            placeholder="Code"
            aria-label="Course code"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
          <input
            className="rounded-md border border-slate-300 px-2 py-2 text-sm"
            placeholder="Name"
            aria-label="Course name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <select
            className="rounded-md border border-slate-300 px-2 py-2 text-sm"
            aria-label="Level"
            value={level}
            onChange={(event) => setLevel(event.target.value)}
          >
            {["FOUNDATION", "INTERMEDIATE", "FINAL"].map((item) => (
              <option key={item}>{item}</option>
            ))}
          </select>
        </div>
        {formError && <p className="mt-2 text-sm text-red-700">{formError}</p>}
        <div className="mt-3">
          <Button onClick={create} disabled={busy || !code.trim() || !name.trim()}>
            Add course
          </Button>
        </div>
      </Card>
      {tree.loading && <Spinner label="Loading syllabus" />}
      {tree.error && <ErrorState error={tree.error} what="the syllabus" />}
      <ul className="space-y-2">
        {(tree.data?.courses ?? []).map((course) => (
          <li key={course.id} className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <span className="font-medium text-slate-900">{course.name}</span>
                <span className="ml-2 text-xs text-slate-500">
                  {course.code} · {course.level}
                  {course.isActive ? "" : " · inactive"}
                </span>
              </div>
              <Button tone="secondary" onClick={() => retire(course.id, !course.isActive)}>
                {course.isActive ? "Retire" : "Restore"}
              </Button>
            </div>
            <p className="mt-1 text-xs text-slate-500">
              {(tree.data?.subjects ?? []).filter((subject) => subject.courseId === course.id).length}{" "}
              subjects
            </p>
          </li>
        ))}
      </ul>
    </Shell>
  );
}

export function PlansAdmin() {
  const plans = useLoader((signal) => fetchAdminPlans(signal), []);
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  async function save(code: string, durationDays: number) {
    setFormError(null);
    setNote(null);
    const rupees = Number(amounts[code]);
    if (!Number.isInteger(rupees) || rupees < 1) {
      setFormError("Enter a whole number of rupees, at least 1.");
      return;
    }
    setBusy(code);
    try {
      const saved = await saveAdminPlan({
        code,
        amount_paise: rupees * 100,
        duration_days: durationDays,
      });
      setNote(`Saved. The next ${saved.code} order charges ₹${saved.amountRupees.toLocaleString("en-IN")}.`);
      plans.reload();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Could not save.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Shell
      title="Plans and pricing"
      purpose="This is the catalogue checkout charges. A price saved here is the price of the next order. An order already created keeps its own amount."
    >
      {plans.loading && <Spinner label="Loading plans" />}
      {plans.error && <ErrorState error={plans.error} what="the plans" />}
      {plans.data && (
        <Card>
          <p className="text-sm text-slate-700">{plans.data.reason}</p>
          <p className="mt-2 text-sm font-medium text-slate-900">
            Checkout reads this catalogue. Entitlements are not edited here.
          </p>
        </Card>
      )}
      {formError && <p className="text-sm text-red-700">{formError}</p>}
      {note && <p className="text-sm text-slate-700">{note}</p>}
      <ul className="grid gap-3 lg:grid-cols-3">
        {(plans.data?.plans ?? []).map((plan) => (
          <li key={plan.code} className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between gap-2">
              <h2 className="font-medium text-slate-900">{plan.label}</h2>
              {plan.recommended && <Badge tone="brand">Recommended</Badge>}
            </div>
            <p className="mt-2 text-2xl font-semibold text-slate-900">
              ₹{plan.amountRupees.toLocaleString("en-IN")}
            </p>
            <p className="text-xs text-slate-500">{plan.amountPaise.toLocaleString("en-IN")} paise</p>
            {plan.amountPaise > 0 && plans.data?.editable && (
              <div className="mt-3 flex items-end gap-2">
                <label className="text-xs text-slate-600">
                  Rupees
                  <input
                    aria-label={`Price for ${plan.label}`}
                    className="mt-1 w-28 rounded-md border border-slate-300 px-2 py-1 text-sm"
                    value={amounts[plan.code] ?? String(plan.amountRupees)}
                    onChange={(event) =>
                      setAmounts((current) => ({ ...current, [plan.code]: event.target.value }))
                    }
                  />
                </label>
                <Button
                  tone="primary"
                  disabled={busy === plan.code}
                  onClick={() => save(plan.code, plan.durationDays)}
                >
                  Save price
                </Button>
              </div>
            )}
            <p className="mt-2 text-sm text-slate-600">{plan.tagline}</p>
            <ul className="mt-3 list-disc space-y-1 pl-4 text-sm text-slate-700">
              {plan.features.map((feature) => (
                <li key={feature}>{feature}</li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </Shell>
  );
}

export function AiAdmin() {
  const config = useLoader((signal) => fetchAdminAi(signal), []);
  return (
    <Shell
      title="AI configuration"
      purpose="A suggestion is written only from excerpts a student may already read. It is not a legal authority."
    >
      {config.loading && <Spinner label="Loading AI configuration" />}
      {config.error && <ErrorState error={config.error} what="AI configuration" />}
      {config.data && (
        <Card>
          <dl className="space-y-2 text-sm text-slate-700">
            <div>
              <dt className="font-medium text-slate-900">Assistant flag</dt>
              <dd>{config.data.enabled ? "On" : "Off — features.ai_assistant"}</dd>
            </div>
            <div>
              <dt className="font-medium text-slate-900">Generated answers</dt>
              <dd>
                {config.data.generatesAnswers
                  ? "Yes, but only when an excerpt exists. Otherwise answer stays null."
                  : "No. answer is always null until AI_PROVIDER_API_KEY is set."}
              </dd>
            </div>
            <div>
              <dt className="font-medium text-slate-900">Grounding</dt>
              <dd>
                {config.data.grounding}. Optional: {config.data.groundingOptional ? "yes" : "no"}.
              </dd>
            </div>
            <div>
              <dt className="font-medium text-slate-900">Provider</dt>
              <dd>
                {config.data.providerConfigured
                  ? "A key is set on the server. It is not shown here."
                  : `Missing ${config.data.missingEnv.join(", ") || "AI_PROVIDER_API_KEY"}.`}
              </dd>
            </div>
            <div>
              <dt className="font-medium text-slate-900">Spend ceiling</dt>
              <dd>${config.data.monthlyCeilingUsd} a month. There is no unlimited mode.</dd>
            </div>
          </dl>
          <p className="mt-3 text-sm text-slate-600">{config.data.note}</p>
          <p className="mt-2 text-sm text-slate-600">
            An owner turns the flag on under{" "}
            <Link className="text-brand-700 underline" to="/admin/settings">
              Platform settings
            </Link>
            . Students open the assistant from the dashboard, not the clipped header.
          </p>
        </Card>
      )}
    </Shell>
  );
}

export function StorageAdmin() {
  const storage = useLoader((signal) => fetchAdminStorage(signal), []);
  return (
    <Shell
      title="Storage"
      purpose="This screen reports whether the server can talk to the bucket. It does not invent a usage number."
    >
      {storage.loading && <Spinner label="Checking storage" />}
      {storage.error && <ErrorState error={storage.error} what="storage" />}
      {storage.data && (
        <Card>
          <p className="text-sm font-medium text-slate-900">
            {storage.data.configured ? "Credentials are set." : "Not configured."}
          </p>
          {storage.data.missingEnv.length > 0 && (
            <p className="mt-2 text-sm text-slate-700">
              Missing environment variables:{" "}
              <span className="font-mono">{storage.data.missingEnv.join(", ")}</span>
            </p>
          )}
          <p className="mt-2 text-sm text-slate-600">{storage.data.note}</p>
          <p className="mt-2 text-sm text-slate-500">Objects listed: none. No count is shown.</p>
        </Card>
      )}
    </Shell>
  );
}
