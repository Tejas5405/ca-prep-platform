import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Card, ErrorState, Spinner } from "../components/ui";
import {
  answerChallenge,
  createCalendarEvent,
  createGroup,
  createMentorship,
  createThread,
  fetchCalendar,
  fetchChallenge,
  fetchExperiment,
  fetchFormulas,
  fetchGlossary,
  fetchGroups,
  fetchMarketplace,
  fetchMentorship,
  fetchProjection,
  fetchReferrals,
  fetchThreads,
  joinGroup,
  recordPomodoro,
  redeemReferral,
} from "../lib/campus";
import { useLoader } from "../lib/useLoader";

/**
 * The study desk. Each panel shows the server's own caveat: a formula is an
 * editor's note, a referral does not grant Premium, a timer is self-reported.
 */
export default function StudyPage() {
  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Study</h1>
        <p className="mt-1 text-sm text-slate-600">
          Groups, notes, a daily question, and a timer. Nothing here is an ICAI ruling,
          and nothing here charges a card.
        </p>
      </header>
      <Projection />
      <Challenge />
      <div className="grid gap-4 lg:grid-cols-2">
        <Groups />
        <Forum />
        <Notes />
        <Glossary />
        <Mentorship />
        <Referrals />
        <CalendarPanel />
        <Pomodoro />
        <Marketplace />
        <Experiment />
      </div>
      <Card>
        <h2 className="font-medium text-slate-900">Exam mode</h2>
        <p className="mt-1 text-sm text-slate-600">
          A mock already hides answers until you submit. Exam mode records that you sat
          it that way, and records tab changes. It does not open a camera or lock the browser.
        </p>
        <Link className="mt-2 inline-block text-sm font-medium text-brand-700 underline" to="/mocks">
          Open mock papers
        </Link>
      </Card>
    </div>
  );
}

function Projection() {
  const data = useLoader((signal) => fetchProjection(signal), []);
  if (data.loading) return <Spinner label="Reading recent practice" />;
  if (data.error) return <ErrorState error={data.error} what="the practice trend" />;
  if (!data.data) return null;
  return (
    <Card>
      <h2 className="font-medium text-slate-900">Practice trend</h2>
      <p className="mt-1 text-sm text-slate-700">{data.data.note}</p>
      {data.data.enoughData && (
        <p className="mt-2 text-sm text-slate-600">
          Last 14 days: {data.data.recentAccuracy ?? "—"}. Previous 14 days:{" "}
          {data.data.previousAccuracy ?? "—"}. Predicted exam mark: none.
        </p>
      )}
    </Card>
  );
}

function Challenge() {
  const data = useLoader((signal) => fetchChallenge(signal), []);
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function answer(label: string) {
    setBusy(true);
    try {
      const saved = await answerChallenge(label);
      setResult(
        saved.alreadyAnswered
          ? saved.note
          : `${saved.isCorrect ? "Correct" : "Not correct"}. Points awarded: ${saved.pointsAwarded}. ${saved.note}`,
      );
    } catch (caught) {
      setResult(caught instanceof Error ? caught.message : "Could not mark that.");
    } finally {
      setBusy(false);
    }
  }

  if (data.loading) return <Spinner label="Loading today's question" />;
  if (data.error) return <ErrorState error={data.error} what="the daily challenge" />;
  if (!data.data?.available || !data.data.question) {
    return (
      <Card>
        <h2 className="font-medium text-slate-900">Daily challenge</h2>
        <p className="mt-1 text-sm text-slate-600">
          {data.data?.note ?? "No published question is available."}
        </p>
      </Card>
    );
  }
  const question = data.data.question;
  return (
    <Card>
      <h2 className="font-medium text-slate-900">Daily challenge</h2>
      <p className="mt-2 whitespace-pre-wrap text-sm text-slate-800">{question.text}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {question.options.map((option) => (
          <button
            key={option.label}
            type="button"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
            disabled={busy || data.data?.alreadyAnswered}
            onClick={() => void answer(option.label)}
          >
            {option.label}. {option.text}
          </button>
        ))}
      </div>
      {data.data.alreadyAnswered && (
        <p className="mt-2 text-xs text-slate-500">Already answered today.</p>
      )}
      {result && <p className="mt-2 text-sm text-slate-700">{result}</p>}
    </Card>
  );
}

function Groups() {
  const data = useLoader((signal) => fetchGroups(signal), []);
  const [name, setName] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  async function create() {
    await createGroup(name, "");
    setName("");
    setMessage("Group saved.");
    data.reload();
  }

  return (
    <Card>
      <h2 className="font-medium text-slate-900">Study groups</h2>
      <p className="mt-1 text-xs text-slate-500">{data.data?.note}</p>
      {data.error && <ErrorState error={data.error} what="study groups" />}
      <ul className="mt-2 space-y-2 text-sm">
        {(data.data?.groups ?? []).map((group) => (
          <li key={group.id} className="flex items-center justify-between gap-2">
            <span>
              {group.name} · {group.memberCount}
            </span>
            {!group.joined && (
              <button
                type="button"
                className="text-brand-700 underline"
                onClick={() => void joinGroup(group.id).then(() => data.reload())}
              >
                Join
              </button>
            )}
          </li>
        ))}
      </ul>
      <div className="mt-3 flex gap-2">
        <input
          className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
          value={name}
          placeholder="New group name"
          onChange={(event) => setName(event.target.value)}
        />
        <button
          type="button"
          className="rounded-md bg-slate-900 px-3 text-sm text-white disabled:opacity-50"
          disabled={name.trim().length < 2}
          onClick={() => void create().catch((caught: unknown) => setMessage(String(caught)))}
        >
          Create
        </button>
      </div>
      {message && <p className="mt-2 text-xs text-slate-600">{message}</p>}
    </Card>
  );
}

function Forum() {
  const data = useLoader((signal) => fetchThreads(signal), []);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");

  return (
    <Card>
      <h2 className="font-medium text-slate-900">Forum</h2>
      <p className="mt-1 text-xs text-slate-500">{data.data?.disclaimer}</p>
      <ul className="mt-2 space-y-1 text-sm text-slate-700">
        {(data.data?.threads ?? []).map((thread) => (
          <li key={thread.id}>
            {thread.title} · {thread.posts} posts
          </li>
        ))}
      </ul>
      <input
        className="mt-3 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
        placeholder="Thread title"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
      />
      <textarea
        className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
        rows={2}
        placeholder="This is a student discussion, not a ruling."
        value={body}
        onChange={(event) => setBody(event.target.value)}
      />
      <button
        type="button"
        className="mt-2 rounded-md border border-slate-300 px-3 py-1 text-sm disabled:opacity-50"
        disabled={title.trim().length < 2 || body.trim().length < 2}
        onClick={() =>
          void createThread(title, body).then(() => {
            setTitle("");
            setBody("");
            data.reload();
          })
        }
      >
        Post
      </button>
    </Card>
  );
}

function Notes() {
  const data = useLoader((signal) => fetchFormulas(signal), []);
  return (
    <Card>
      <h2 className="font-medium text-slate-900">Formula notes</h2>
      <p className="mt-1 text-xs text-slate-500">{data.data?.note}</p>
      {(data.data?.entries.length ?? 0) === 0 && (
        <p className="mt-2 text-sm text-slate-600">
          No published note yet. An editor adds one. This page does not invent a rate or a section.
        </p>
      )}
      <ul className="mt-2 space-y-2 text-sm">
        {(data.data?.entries ?? []).map((entry) => (
          <li key={entry.id}>
            <p className="font-medium text-slate-900">{entry.title}</p>
            <p className="text-slate-700">{entry.body}</p>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Glossary() {
  const data = useLoader((signal) => fetchGlossary(signal), []);
  return (
    <Card>
      <h2 className="font-medium text-slate-900">Glossary</h2>
      <p className="mt-1 text-xs text-slate-500">{data.data?.note}</p>
      <ul className="mt-2 space-y-2 text-sm">
        {(data.data?.entries ?? []).map((entry) => (
          <li key={entry.term}>
            <span className="font-medium text-slate-900">{entry.term}. </span>
            {entry.definition}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Mentorship() {
  const data = useLoader((signal) => fetchMentorship(signal), []);
  const [topic, setTopic] = useState("");
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  return (
    <Card>
      <h2 className="font-medium text-slate-900">Mentorship</h2>
      <p className="mt-1 text-xs text-slate-500">
        A request is saved. A mentor is not assigned until an operator does it, and no message is sent.
      </p>
      <ul className="mt-2 text-sm text-slate-700">
        {(data.data?.requests ?? []).map((row) => (
          <li key={row.id}>
            {row.topic} · {row.status}
            {row.mentorAssigned ? " · mentor assigned" : " · no mentor yet"}
          </li>
        ))}
      </ul>
      <input
        className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
        placeholder="Topic"
        value={topic}
        onChange={(event) => setTopic(event.target.value)}
      />
      <textarea
        className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
        rows={2}
        value={note}
        onChange={(event) => setNote(event.target.value)}
      />
      <button
        type="button"
        className="mt-2 rounded-md border border-slate-300 px-3 py-1 text-sm disabled:opacity-50"
        disabled={topic.trim().length < 2 || note.trim().length < 2}
        onClick={() =>
          void createMentorship(topic, note).then((row) => {
            setSaved(row.note);
            setTopic("");
            setNote("");
            data.reload();
          })
        }
      >
        Request a mentor
      </button>
      {saved && <p className="mt-2 text-xs text-slate-600">{saved}</p>}
    </Card>
  );
}

function Referrals() {
  const data = useLoader((signal) => fetchReferrals(signal), []);
  const [code, setCode] = useState("");
  const [note, setNote] = useState<string | null>(null);
  return (
    <Card>
      <h2 className="font-medium text-slate-900">Referrals</h2>
      <p className="mt-1 text-sm text-slate-700">{data.data?.note}</p>
      {data.data && (
        <p className="mt-2 font-mono text-sm text-slate-900">
          {data.data.code} · {data.data.signups} signups · {data.data.conversions} conversions
        </p>
      )}
      <div className="mt-3 flex gap-2">
        <input
          className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
          placeholder="Someone else's code"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        <button
          type="button"
          className="rounded-md border border-slate-300 px-3 text-sm"
          onClick={() =>
            void redeemReferral(code)
              .then((row) => setNote(row.note ?? "Recorded. Premium was not granted."))
              .catch((caught: unknown) => setNote(caught instanceof Error ? caught.message : "Not recorded."))
          }
        >
          Redeem
        </button>
      </div>
      {note && <p className="mt-2 text-xs text-slate-600">{note}</p>}
    </Card>
  );
}

function CalendarPanel() {
  const data = useLoader((signal) => fetchCalendar(signal), []);
  const [title, setTitle] = useState("");
  const [when, setWhen] = useState("");
  return (
    <Card>
      <h2 className="font-medium text-slate-900">Calendar</h2>
      <p className="mt-1 text-xs text-slate-500">{data.data?.note}</p>
      <ul className="mt-2 text-sm text-slate-700">
        {(data.data?.events ?? []).map((event) => (
          <li key={event.id}>
            {event.title} · {event.startsAt}
          </li>
        ))}
      </ul>
      <input
        className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
        placeholder="Revision block"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
      />
      <input
        className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
        type="datetime-local"
        value={when}
        onChange={(event) => setWhen(event.target.value)}
      />
      <button
        type="button"
        className="mt-2 rounded-md border border-slate-300 px-3 py-1 text-sm disabled:opacity-50"
        disabled={title.trim().length < 2 || !when}
        onClick={() =>
          void createCalendarEvent(title, new Date(when).toISOString()).then(() => {
            setTitle("");
            data.reload();
          })
        }
      >
        Add event
      </button>
    </Card>
  );
}

function Pomodoro() {
  const [left, setLeft] = useState(25 * 60);
  const [running, setRunning] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      setLeft((value) => {
        if (value <= 1) {
          setRunning(false);
          return 0;
        }
        return value - 1;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  return (
    <Card>
      <h2 className="font-medium text-slate-900">Pomodoro</h2>
      <p className="mt-1 font-mono text-2xl text-slate-900">
        {String(Math.floor(left / 60)).padStart(2, "0")}:{String(left % 60).padStart(2, "0")}
      </p>
      <p className="text-xs text-slate-500">The timer runs in this browser. The server does not verify it.</p>
      <div className="mt-2 flex gap-2">
        <button
          type="button"
          className="rounded-md bg-slate-900 px-3 py-1 text-sm text-white"
          onClick={() => setRunning((value) => !value)}
        >
          {running ? "Pause" : "Start"}
        </button>
        <button
          type="button"
          className="rounded-md border border-slate-300 px-3 py-1 text-sm"
          onClick={() => {
            const elapsed = left === 0 ? 25 : Math.max(1, Math.round((25 * 60 - left) / 60));
            void recordPomodoro(elapsed, left === 0).then((row) => setNote(row.note));
          }}
        >
          Save what I reported
        </button>
      </div>
      {note && <p className="mt-2 text-xs text-slate-600">{note}</p>}
    </Card>
  );
}

function Marketplace() {
  const data = useLoader((signal) => fetchMarketplace(signal), []);
  return (
    <Card>
      <h2 className="font-medium text-slate-900">Marketplace</h2>
      <p className="mt-1 text-xs text-slate-500">{data.data?.note}</p>
      {(data.data?.listings.length ?? 0) === 0 && (
        <p className="mt-2 text-sm text-slate-600">No listing is published. This page does not take payment.</p>
      )}
      <ul className="mt-2 space-y-2 text-sm">
        {(data.data?.listings ?? []).map((listing) => (
          <li key={listing.id}>
            <p className="font-medium text-slate-900">{listing.title}</p>
            <p className="text-slate-700">{listing.description}</p>
            <p className="text-xs text-slate-500">Takes payment here: {listing.takesPayment ? "yes" : "no"}.</p>
            {listing.planCode && (
              <Link className="text-brand-700 underline" to="/upgrade">
                Plans are bought on the upgrade page
              </Link>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Experiment() {
  const data = useLoader((signal) => fetchExperiment("study_hub_layout", signal), []);
  return (
    <Card>
      <h2 className="font-medium text-slate-900">Layout experiment</h2>
      <p className="mt-1 text-sm text-slate-700">
        {data.data?.active
          ? `You are in variant ${data.data.variant}. ${data.data.note}`
          : (data.data?.note ?? "No experiment is configured.")}
      </p>
    </Card>
  );
}
