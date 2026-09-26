/**
 * Study tools. The responses say what was recorded and what was not.
 * A ticket does not send email. A listing does not take payment.
 */

import { api } from "./api";

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const body = await api.get<{ data: T }>(path, signal);
  return body.data;
}

async function post<T>(path: string, payload?: unknown): Promise<T> {
  const body = await api.post<{ data: T }>(path, payload);
  return body.data;
}

export interface StudyGroup {
  id: string;
  name: string;
  description: string | null;
  memberCount: number;
  joined: boolean;
}

export function fetchGroups(signal?: AbortSignal) {
  return get<{ groups: StudyGroup[]; note: string }>("/campus/groups", signal);
}

export function createGroup(name: string, description: string) {
  return post<{ id: string }>("/campus/groups", { name, description: description || null });
}

export function joinGroup(groupId: string) {
  return post<{ joined: boolean }>("/campus/groups/join", { group_id: groupId });
}

export interface ForumThread {
  id: string;
  title: string;
  subjectLabel: string | null;
  posts: number;
}

export function fetchThreads(signal?: AbortSignal) {
  return get<{ threads: ForumThread[]; disclaimer: string }>("/campus/forum", signal);
}

export function createThread(title: string, body: string) {
  return post<{ id: string; disclaimer: string }>("/campus/forum", { title, body });
}

export interface GlossaryEntry {
  term: string;
  definition: string;
  source: string;
}

export function fetchGlossary(signal?: AbortSignal) {
  return get<{ entries: GlossaryEntry[]; note: string }>("/campus/glossary", signal);
}

export interface FormulaEntry {
  id: string;
  title: string;
  body: string;
  subjectLabel: string | null;
}

export function fetchFormulas(signal?: AbortSignal) {
  return get<{ entries: FormulaEntry[]; note: string }>("/campus/formulas", signal);
}

export function fetchChallenge(signal?: AbortSignal) {
  return get<{
    available: boolean;
    question: {
      id: string;
      text: string;
      options: { label: string; text: string }[];
    } | null;
    alreadyAnswered: boolean;
    note?: string;
  }>("/campus/challenge", signal);
}

export function answerChallenge(chosenOption: string) {
  return post<{
    isCorrect: boolean | null;
    pointsAwarded: number;
    alreadyAnswered: boolean;
    note: string;
  }>("/campus/challenge", { chosen_option: chosenOption });
}

export function fetchReferrals(signal?: AbortSignal) {
  return get<{
    code: string;
    signups: number;
    conversions: number;
    premiumGranted: boolean;
    note: string;
  }>("/campus/referrals", signal);
}

export function redeemReferral(code: string) {
  return post<{ recorded: boolean; premiumGranted: boolean; note?: string }>(
    "/campus/referrals/redeem",
    { code },
  );
}

export function createMentorship(topic: string, note: string) {
  return post<{ id: string; mentorAssigned: boolean; note: string }>("/campus/mentorship", {
    topic,
    note,
  });
}

export function fetchMentorship(signal?: AbortSignal) {
  return get<{
    requests: { id: string; topic: string; status: string; mentorAssigned: boolean }[];
  }>("/campus/mentorship", signal);
}

export function createTicket(subject: string, body: string) {
  return post<{ id: string; emailSent: boolean; note: string }>("/campus/support", {
    subject,
    body,
  });
}

export function fetchCalendar(signal?: AbortSignal) {
  return get<{
    events: { id: string; title: string; startsAt: string }[];
    synced: boolean;
    note: string;
  }>("/campus/calendar", signal);
}

export function createCalendarEvent(title: string, startsAt: string) {
  return post<{ id: string; synced: boolean }>("/campus/calendar", {
    title,
    starts_at: startsAt,
  });
}

export function recordPomodoro(minutes: number, completed: boolean) {
  return post<{ verified: boolean; note: string }>("/campus/pomodoro", { minutes, completed });
}

export function fetchProjection(signal?: AbortSignal) {
  return get<{
    enoughData: boolean;
    gradedAttempts: number;
    recentAccuracy: number | null;
    previousAccuracy: number | null;
    examScore: null;
    note: string;
  }>("/progress/projection", signal);
}

export function fetchMarketplace(signal?: AbortSignal) {
  return get<{
    listings: {
      id: string;
      title: string;
      description: string;
      planCode: string | null;
      takesPayment: boolean;
    }[];
    note: string;
  }>("/campus/marketplace", signal);
}

export function fetchExperiment(key: string, signal?: AbortSignal) {
  return get<{ active: boolean; variant: string | null; note: string }>(
    `/campus/experiments/${encodeURIComponent(key)}`,
    signal,
  );
}

export function recordExamMode(attemptId: string) {
  return post<{ cameraUsed: boolean; locksBrowser: boolean; note: string }>("/campus/exam-mode", {
    attempt_id: attemptId,
  });
}

export function recordProctorEvent(attemptId: string, eventType: string) {
  return post<{ recorded: boolean; blocked: boolean; cameraUsed: boolean }>(
    "/campus/proctor-events",
    { attempt_id: attemptId, event_type: eventType },
  );
}

export function fetchReviewQueue(signal?: AbortSignal) {
  return get<{
    questions: { id: string; reviewState: string; status: string; excerpt: string }[];
    answersChanged: boolean;
    note: string;
  }>("/admin/review-queue", signal);
}

export function runReverify() {
  return post<{
    needsUpdate: number;
    pendingReview: number;
    answersChanged: boolean;
    publishedAutomatically: boolean;
    note: string;
  }>("/admin/review-queue/run");
}
