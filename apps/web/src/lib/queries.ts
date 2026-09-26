/**
 * Typed calls to the API, one function per endpoint the app uses.
 *
 * WHY THIS IS ONE FILE. Every response here was read from a LIVE API against a
 * seeded database while this was written, so the field names are what the server
 * actually sends rather than what a schema seemed to imply. Three of them are not
 * guessable:
 *
 *   * every list endpoint returns `{data: [...], meta: {total, page, limit,
 *     hasMore}}`, but several collection endpoints return `{data: {courses: []}}` -
 *     a single object with the array inside it. A helper that assumed one shape
 *     would half-work, which is worse than failing.
 *   * query parameters are snake_case (`course_id`, `chapter_id`), while response
 *     FIELDS are camelCase (`subjectId`, `chapterId`). The mismatch is real and is
 *     the property of the contract, not a typo to be tidied away.
 *   * the practice and mock payloads have no OpenAPI schema (the routes declare
 *     `response_model=None`), so nothing generates these types automatically. They
 *     are hand-written from observed responses, which is why each one says where it
 *     came from.
 */

import {
  api,
  ApiError,
  type ApiList,
  type ApiListMeta,
  type ApiSuccess,
} from "./api";

/** Uniform failure so screens do not each invent an error shape. */
export class QueryError extends Error {
  readonly requestId: string | null;
  readonly status: number;
  readonly isAuthError: boolean;
  readonly isForbidden: boolean;
  readonly isValidationError: boolean;

  constructor(error: unknown) {
    if (error instanceof ApiError) {
      super(error.message);
      this.name = "QueryError";
      this.status = error.status;
      this.requestId = error.requestId;
      this.isAuthError = error.isAuthError;
      // Copied, not re-derived from `status` at each call site: `ApiError` is the one
      // place that decides what 401/403/422 mean (a 422 body may carry field errors
      // without the status, for instance), and a screen that re-implemented the check
      // would drift the first time that rule changed.
      this.isForbidden = error.isForbidden;
      this.isValidationError = error.isValidationError;
      return;
    }
    super(error instanceof Error ? error.message : "Something went wrong.");
    this.name = "QueryError";
    this.status = 0;
    this.requestId = null;
    this.isAuthError = false;
    this.isForbidden = false;
    this.isValidationError = false;
  }
}

/** Every loader funnels through here, so no screen sees a raw fetch rejection. */
async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  try {
    return await api.get<T>(path, signal);
  } catch (error: unknown) {
    throw new QueryError(error);
  }
}

async function requestPublic<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  try {
    return await api.publicGet<T>(path, signal);
  } catch (error: unknown) {
    throw new QueryError(error);
  }
}

async function send<T>(path: string, body?: unknown): Promise<T> {
  try {
    return await api.post<T>(path, body);
  } catch (error: unknown) {
    throw new QueryError(error);
  }
}

/**
 * A write that is not a POST.
 *
 * Kept separate from `send` rather than adding a method parameter: every POST in this
 * file is a POST, and a defaulted verb argument is exactly how a route ends up accepting
 * the wrong method. Errors are wrapped the same way so no screen sees a raw rejection.
 */
async function sendWith<T>(
  method: "PATCH" | "PUT",
  path: string,
  body?: unknown,
): Promise<T> {
  try {
    return method === "PATCH"
      ? await api.patch<T>(path, body)
      : await api.put<T>(path, body);
  } catch (error: unknown) {
    throw new QueryError(error);
  }
}

// ------------------------------------------------------------------ profile --

export interface Entitlements {
  tier: string;
  status: string;
  expiresAt: string | null;
  entitlements: string[];
  isPremium: boolean;
}

export interface Me {
  id: string;
  email: string | null;
  displayName: string | null;
  role: string;
  isStaff: boolean;
  targetLevel: string | null;
  targetExamDate: string | null;
  syllabusScheme: string;
  dailyGoalMinutes: number;
  timezone: string;
  referralCode: string | null;
  profile: {
    college: string | null;
    city: string | null;
    attemptNumber: number | null;
    currentStreak: number;
    longestStreak: number;
    totalPoints: number;
    currentLevel: number;
  };
  entitlements: Entitlements;
}

export function fetchMe(signal?: AbortSignal) {
  return request<ApiSuccess<Me>>("/me", signal).then((body) => body.data);
}

export interface ProfileUpdate {
  // `| undefined` on every field, not just `?`: the project compiles with
  // `exactOptionalPropertyTypes`, so a form that clears an input (producing
  // `undefined`) would otherwise be unassignable to this type.
  display_name?: string | undefined;
  city?: string | undefined;
  college?: string | undefined;
  attempt_number?: number | undefined;
  target_level?: string | undefined;
  target_exam_date?: string | undefined;
  syllabus_scheme?: string | undefined;
  daily_goal_minutes?: number | undefined;
  timezone?: string | undefined;
}

export function updateMe(patch: ProfileUpdate) {
  return api
    .patch<ApiSuccess<Me>>("/me", patch)
    .then((body) => body.data)
    .catch((error: unknown) => {
      throw new QueryError(error);
    });
}

// --------------------------------------------------------------- curriculum --

export interface Course {
  id: string;
  code: string;
  name: string;
  level: string;
  syllabusScheme: string;
  description: string | null;
}

export interface Subject {
  id: string;
  courseId: string;
  code: string;
  name: string;
  groupName: string | null;
  paperNumber: number;
  syllabusWeight: number | null;
  chapterCount: number;
  type?: "subject" | "subject_component";
  parentSubjectCode?: string | null;
}

export interface Chapter {
  id: string;
  subjectId: string;
  code: string;
  name: string;
  sequence: number;
  weightage: number | null;
  estimatedMinutes: number | null;
  topicCount: number;
}

export function fetchCourses(signal?: AbortSignal) {
  return request<ApiSuccess<{ courses: Course[] }>>(
    "/curriculum/courses",
    signal,
  ).then((body) => body.data.courses);
}

export function fetchSubjects(
  courseId: string,
  signal?: AbortSignal,
  options?: { includeParents?: boolean },
) {
  const parents = options?.includeParents ? "&include_parents=true" : "";
  return request<ApiSuccess<{ subjects: Subject[] }>>(
    `/curriculum/subjects?course_id=${encodeURIComponent(courseId)}${parents}`,
    signal,
  ).then((body) => body.data.subjects);
}

export function fetchChapters(subjectId: string, signal?: AbortSignal) {
  return request<ApiSuccess<{ chapters: Chapter[] }>>(
    `/curriculum/chapters?subject_id=${encodeURIComponent(subjectId)}`,
    signal,
  ).then((body) => body.data.chapters);
}

// ----------------------------------------------------------------- practice --

export interface Option {
  label: string;
  text: string;
}

export interface Question {
  id: string;
  text: string;
  questionType: string;
  difficulty: string;
  marks: number;
  negativeMarks: number;
  subjectId: string | null;
  chapterId: string | null;
  source: string | null;
  isPremium: boolean;
  options: Option[];
}

export interface AnswerResult {
  questionId: string;
  isCorrect: boolean | null;
  correctAnswer: string | null;
  explanation: string | null;
  pointsAwarded: number;
  attemptsCount: number;
  correctCount: number;
  accuracy: number | null;
  currentStreak: number;
  options: (Option & { isCorrect: boolean })[];
}

export interface PracticeQuery {
  subjectId?: string;
  chapterId?: string;
  difficulty?: string;
  limit?: number;
}

export function fetchPracticeQuestions(
  query: PracticeQuery,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams();
  if (query.subjectId) params.set("subject_id", query.subjectId);
  if (query.chapterId) params.set("chapter_id", query.chapterId);
  if (query.difficulty) params.set("difficulty", query.difficulty);
  params.set("limit", String(query.limit ?? 10));

  return request<ApiSuccess<{ questions: Question[]; count?: number }>>(
    `/practice/questions?${params.toString()}`,
    signal,
  ).then((body) => body.data.questions);
}

export function submitAnswer(
  questionId: string,
  chosenOption: string,
  seconds: number,
) {
  return send<ApiSuccess<AnswerResult>>("/practice/answers", {
    question_id: questionId,
    chosen_option: chosenOption,
    time_spent_seconds: seconds,
  }).then((body) => body.data);
}

export function setBookmark(questionId: string, marked: boolean) {
  return send<ApiSuccess<{ questionId: string; markedForReview: boolean }>>(
    `/practice/questions/${questionId}/bookmark`,
    { marked },
  ).then((body) => body.data);
}

// ----------------------------------------------------------------- revision --

export interface RevisionCard {
  questionId: string;
  text: string;
  questionType: string;
  difficulty: string;
  chapterId: string | null;
  intervalDays: number;
  repetitions: number;
  easeFactor: number;
  nextReviewAt: string;
  options: Option[];
}

export interface RevisionStats {
  total: number;
  due: number;
  learning: number;
  mature: number;
  averageIntervalDays: number;
  boxes: Record<string, number>;
}

export function fetchDueCards(limit = 10, signal?: AbortSignal) {
  return request<ApiSuccess<{ cards: RevisionCard[]; count: number }>>(
    `/revision/due?limit=${limit}`,
    signal,
  ).then((body) => body.data);
}

export function fetchRevisionStats(signal?: AbortSignal) {
  return request<ApiSuccess<RevisionStats>>("/revision/stats", signal).then(
    (body) => body.data,
  );
}

export interface ReviewOutcome {
  questionId: string;
  quality: number;
  lapsed: boolean;
  intervalDays: number;
  repetitions: number;
  easeFactor: number;
  box: number;
  nextReviewAt: string;
}

/** SM-2 quality, 0-5. The scale is entirely the student's judgement. */
export function gradeCard(questionId: string, quality: number) {
  return send<ApiSuccess<ReviewOutcome>>("/revision/review", {
    question_id: questionId,
    quality,
  }).then((body) => body.data);
}

// ------------------------------------------------------------------- doubts --

export interface Doubt {
  id: string;
  title: string;
  body: string;
  status: string;
  priority: string;
  subjectId: string | null;
  chapterId: string | null;
  questionId: string | null;
  replyCount: number;
  acceptedReplyId: string | null;
  resolutionNote: string | null;
  createdAt: string;
  lastActivityAt: string;
}

export interface DoubtReply {
  id: string;
  doubtId: string;
  body: string;
  authorRole: string | null;
  isAccepted: boolean;
  createdAt: string;
}

export interface DoubtThread extends Doubt {
  replies: DoubtReply[];
}

export function fetchDoubts(page = 1, limit = 20, signal?: AbortSignal) {
  return request<ApiList<Doubt>>(`/doubts?page=${page}&limit=${limit}`, signal);
}

export function fetchDoubtThread(id: string, signal?: AbortSignal) {
  return request<ApiSuccess<DoubtThread>>(`/doubts/${id}`, signal).then(
    (body) => body.data,
  );
}

export function createDoubt(input: {
  title: string;
  body: string;
  subjectId?: string | null;
  chapterId?: string | null;
  priority?: string;
}) {
  return send<ApiSuccess<Doubt>>("/doubts", {
    title: input.title,
    body: input.body,
    subject_id: input.subjectId ?? null,
    chapter_id: input.chapterId ?? null,
    priority: input.priority ?? "NORMAL",
  }).then((body) => body.data);
}

export function replyToDoubt(doubtId: string, body: string) {
  return send<ApiSuccess<DoubtReply>>(`/doubts/${doubtId}/replies`, {
    body,
  }).then((body) => body.data);
}

export function acceptReply(doubtId: string, replyId: string) {
  return send<ApiSuccess<{ doubtId: string; acceptedReplyId: string }>>(
    `/doubts/${doubtId}/replies/${replyId}/accept`,
  ).then((body) => body.data);
}

// ----------------------------------------------------------------- progress --

export interface ProgressOverview {
  totals: {
    attempts: number;
    correct: number;
    pendingReview: number;
    accuracy: number | null;
  };
  bySubject: {
    subjectId: string;
    name: string;
    attempts: number;
    accuracy: number;
  }[];
  focusAreas: {
    entityId: string;
    name: string;
    attempts: number;
    accuracy: number;
  }[];
  strongAreas: {
    entityId: string;
    name: string;
    attempts: number;
    accuracy: number;
  }[];
  profile: {
    totalPoints: number;
    currentStreak: number;
    longestStreak: number;
    level: string;
    nextLevel: string | null;
    pointsToNextLevel: number;
    levelProgress: number;
    overallAccuracy: number | null;
  };
  recentActivity: {
    date: string;
    questionsAttempted: number;
    correctCount: number;
    minutesStudied: number;
  }[];
  revision: RevisionStats;
}

export function fetchProgress(days = 30, signal?: AbortSignal) {
  return request<ApiSuccess<ProgressOverview>>(
    `/progress/overview?days=${days}`,
    signal,
  ).then((body) => body.data);
}

// -------------------------------------------------------------------- mocks --

export interface MockTest {
  id: string;
  courseId: string;
  subjectId: string | null;
  title: string;
  kind: string;
  durationMin: number;
  totalMarks: number;
  isPremium: boolean;
  locked: boolean;
  questionCount: number;
}

export function fetchMocks(limit = 20, signal?: AbortSignal) {
  return request<ApiList<MockTest>>(`/mocks?page=1&limit=${limit}`, signal);
}

export interface AttemptQuestion {
  id: string;
  text: string;
  questionType: string;
  difficulty: string;
  marks: number;
  negativeMarks: number;
  subjectId: string | null;
  chapterId: string | null;
  /** No `isCorrect` here: a timed paper must not carry its own answer key. */
  options: Option[];
}

export interface Attempt {
  attemptId: string;
  mockTestId: string;
  title: string;
  kind: string;
  status: string;
  startedAt: string;
  expiresAt: string;
  durationMin: number;
  totalMarks: number;
  autoSubmitted: boolean;
  score: number | null;
  maxScore: number | null;
  resumed?: boolean;
  questions: AttemptQuestion[];
}

export interface AttemptSummary extends Omit<Attempt, "questions" | "resumed"> {
  submittedAt: string | null;
  correctCount: number | null;
  wrongCount: number | null;
  unattemptedCount: number | null;
  timeTakenSeconds: number | null;
}

export interface ScoreResult {
  attemptId: string;
  mockTestId: string;
  score: number;
  maxScore: number;
  correct: number;
  wrong: number;
  unattempted: number;
  pendingReview: number;
  rank: number;
  totalAttempts: number;
  percentile: number;
  autoSubmitted: boolean;
  timeTakenSeconds: number | null;
}

export interface ReportQuestion {
  questionId: string;
  text: string;
  marks: number;
  difficulty: string;
  chosenOption: number | null;
  correctOption: number | null;
  outcome: "CORRECT" | "WRONG" | "UNATTEMPTED" | "PENDING_REVIEW";
  explanation: string | null;
  options: { label: string; text: string; isCorrect: boolean }[];
}

export interface AttemptReport {
  attemptId: string;
  mockTestId: string;
  title: string;
  kind: string;
  status: string;
  submittedAt: string | null;
  autoSubmitted: boolean;
  timeTakenSeconds: number | null;
  score: number | null;
  maxScore: number | null;
  correct: number | null;
  wrong: number | null;
  unattempted: number | null;
  pendingReview: number | null;
  questions: ReportQuestion[];
}

export function startAttempt(mockId: string, _signal?: AbortSignal) {
  return send<ApiSuccess<Attempt>>(`/mocks/${mockId}/attempts`, {
    mock_test_id: mockId,
  }).then((body) => body.data);
}

export function fetchAttempt(attemptId: string, signal?: AbortSignal) {
  return request<ApiSuccess<Attempt>>(
    `/mock-attempts/${attemptId}`,
    signal,
  ).then((body) => body.data);
}

export function fetchAttempts(limit = 20, signal?: AbortSignal) {
  return request<ApiSuccess<AttemptSummary[]>>(
    `/mock-attempts?limit=${limit}`,
    signal,
  ).then((body) => body.data);
}

export function submitAttempt(
  attemptId: string,
  payload: {
    startedAt: string;
    durationMin: number;
    answers: { question_id: string; chosen_option: number | null }[];
  },
) {
  return send<ApiSuccess<ScoreResult>>(`/mock-attempts/${attemptId}/submit`, {
    started_at: payload.startedAt,
    duration_min: payload.durationMin,
    answers: payload.answers,
  }).then((body) => body.data);
}

export function fetchReport(attemptId: string, signal?: AbortSignal) {
  return request<ApiSuccess<AttemptReport>>(
    `/mock-attempts/${attemptId}/report`,
    signal,
  ).then((body) => body.data);
}

// ------------------------------------------------------------------- search --

export interface SearchResult {
  id: string;
  snippet: string;
  text: string;
  questionType: string;
  difficulty: string;
  marks: number;
  subjectId: string | null;
  subjectName: string | null;
  chapterId: string | null;
  chapterName: string | null;
  rank: number;
}

export function search(query: string, limit = 20, signal?: AbortSignal) {
  return request<
    ApiSuccess<{ query: string; count: number; results: SearchResult[] }>
  >(`/search?q=${encodeURIComponent(query)}&limit=${limit}`, signal).then(
    (body) => body.data,
  );
}

/**
 * One question, in full - what a search result opens into.
 *
 * `options` are label + text only: the server does not read `is_correct` for an
 * unanswered question, so there is nothing to strip here. `reveal` appears only
 * when `attempted` is true, which is the same gate the answer endpoint uses.
 */
export interface QuestionDetail {
  id: string;
  text: string;
  questionType: string;
  difficulty: string;
  marks: number;
  negativeMarks: number;
  subjectId: string;
  chapterId: string | null;
  topicId: string | null;
  source: string | null;
  isPremium: boolean;
  year: number | null;
  examSession: string | null;
  syllabusScheme: string;
  bookmarked: boolean;
  attempted: boolean;
  options: { label: string; text: string }[];
  yourAnswer?: string | null;
  yourResult?: boolean | null;
  reveal?: {
    correctAnswer: string | null;
    explanation: string | null;
    modelAnswer: string | null;
  };
}

export function fetchQuestion(questionId: string, signal?: AbortSignal) {
  return request<ApiSuccess<QuestionDetail>>(
    `/questions/${encodeURIComponent(questionId)}`,
    signal,
  ).then((body) => body.data);
}

// ---------------------------------------------------------------- payments --

export interface Plan {
  code: string;
  tier: string;
  label: string;
  amountPaise: number;
  amountRupees: number;
  currency: string;
  durationDays: number;
  tagline: string;
  features: string[];
  /** Which card the pricing table highlights. Decided by the server, not the page. */
  recommended: boolean;
}

/**
 * The plan catalogue.
 *
 * PUBLIC, and the only call in this file that is. The pricing page is read by
 * people who have not signed up, so asking Supabase for a session first would put
 * an auth round trip in front of the most important page in the funnel - and a
 * visitor with no session would get an empty pricing table for no reason.
 *
 * The prices are READ, never sent: the server looks the amount up from its own
 * catalogue when an order is created, so a tampered response here cannot change
 * what anyone is charged.
 */
export function fetchPlans(signal?: AbortSignal) {
  return requestPublic<ApiSuccess<{ plans: Plan[] }>>(
    "/payments/plans",
    signal,
  ).then((body) => body.data.plans);
}

export function fetchSubscription(signal?: AbortSignal) {
  return request<ApiSuccess<Entitlements>>(
    "/payments/subscription",
    signal,
  ).then((body) => body.data);
}

/**
 * A created payment order.
 *
 * `providerKeyId` is the PUBLIC key id - Checkout cannot open without it, and Razorpay
 * intends it to be visible. `providerOrderId` is the gateway's id for this attempt.
 * There is no secret in this payload and no field for one; the key secret and the
 * webhook secret exist only in the API's environment.
 */
export interface PaymentOrder {
  orderId: string;
  planCode: string;
  amountPaise: number;
  /** Sent as well as the paise figure so the screen never does its own arithmetic. */
  amountRupees: number;
  currency: string;
  providerKeyId: string;
  providerOrderId: string | null;
  receipt: string;
}

export interface ConfirmResult {
  orderId: string;
  tier?: string;
  expiresAt?: string | null;
  extended?: boolean;
  /** True when the webhook or a retry already applied this payment. */
  alreadyProcessed?: boolean;
}

/**
 * Start a purchase. The body carries a PLAN CODE and nothing else.
 *
 * No amount, no currency, no tier: the server looks the price up in its own catalogue,
 * so tampering with this request cannot change what is charged. That is also why the
 * response's amount is displayed rather than the page's own.
 */
export function createPaymentOrder(planCode: string) {
  // SNAKE_CASE ON THE WIRE. The whole client speaks snake_case in request bodies and
  // camelCase in response fields, because the API's inbound schemas are strict and
  // forbid unknown fields - a camelCase body is rejected with 422 ("plan_code: Field
  // required" plus "planCode: Extra inputs are not permitted"), not silently coerced.
  //
  // This was caught by a live call against a running API, not by the unit tests: a
  // stubbed `fetch` proves what this file SENDS, and nothing about what the server
  // ACCEPTS. Every other call in this file already used snake_case; the checkout was
  // the first one written without a working example beside it.
  return send<ApiSuccess<PaymentOrder>>("/payments/order", {
    plan_code: planCode,
  }).then((body) => body.data);
}

/**
 * Hand the three values Checkout returned to the server.
 *
 * The server re-verifies the signature over the GATEWAY order id and re-reads the
 * payment from Razorpay before activating anything. A browser that calls this with
 * invented values gets a 400 and no entitlement.
 */
export function confirmPayment(input: {
  orderId: string;
  razorpayPaymentId: string;
  razorpaySignature: string;
}) {
  // Same convention as above, and the field names are the API's, not Razorpay's: the
  // server wants ITS OWN order id (scoped to this user) rather than the gateway's.
  return send<ApiSuccess<ConfirmResult>>("/payments/confirm", {
    order_id: input.orderId,
    razorpay_payment_id: input.razorpayPaymentId,
    razorpay_signature: input.razorpaySignature,
  }).then((body) => body.data);
}

// ------------------------------------------------------------------- admin --
//
// THE EDITORIAL CHAIN, FROM THE BROWSER.
//
// Every one of these endpoints exists and is tested; until now nothing in the app
// called them, so the flow could only be driven with curl. The order matters and is
// the same on the server: mint an upload URL, PUT the bytes straight to storage
// (the API never sees the file), start the job, review the drafts it produced, then
// publish - and a draft becomes a question in DRAFT, so publishing is a second,
// deliberate act performed by someone who may not be the reviewer.
//
// The role gate is on the SERVER. `hasRole` in the UI only decides whether to show
// the screen; a student who navigates to /admin gets 403s, not content.

export interface IngestionJob {
  jobId: string;
  stage: string;
  bucket: string;
  storagePath: string;
  draftsCreated: number;
  pageCount: number | null;
  extractionTier: number | null;
  needsManualReview: boolean;
  errorReason: string | null;
  /** True once the job will not advance on its own - including a failure. */
  isTerminal: boolean;
  createdAt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface DraftSummary {
  draftId: string;
  jobId: string;
  reviewStatus: string;
  /** Server-truncated, so every client truncates in the same place. */
  preview: string;
  sourcePage: number | null;
  detectedYear: number | null;
  detectedAttempt: string | null;
  detectedMarks: number | null;
  detectedQuestionType: string | null;
  detectionConfidence: number | null;
  createdAt: string;
}

export function fetchIngestionJobs(
  options: { stage?: string | undefined; page?: number; limit?: number } = {},
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    page: String(options.page ?? 1),
    limit: String(options.limit ?? 20),
  });
  if (options.stage) params.set("stage", options.stage);
  return request<ApiList<IngestionJob>>(`/ingestion/jobs?${params}`, signal);
}

export function fetchDrafts(
  options: {
    reviewStatus?: string;
    jobId?: string | undefined;
    page?: number;
    limit?: number;
  } = {},
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    review_status: options.reviewStatus ?? "PENDING",
    page: String(options.page ?? 1),
    limit: String(options.limit ?? 20),
  });
  if (options.jobId) params.set("job_id", options.jobId);
  return request<ApiList<DraftSummary>>(`/ingestion/drafts?${params}`, signal);
}

export interface UploadTicket {
  bucket: string;
  path: string;
  uploadUrl: string;
  token: string | null;
  expiresInSeconds: number;
  jobId: string | null;
  jobCreated: boolean;
}

export function requestUpload(payload: {
  filename: string;
  contentType: string;
  sizeBytes: number;
  courseId?: string | undefined;
  subjectId?: string | undefined;
}) {
  return send<ApiSuccess<UploadTicket>>("/ingestion/uploads", {
    filename: payload.filename,
    content_type: payload.contentType,
    size_bytes: payload.sizeBytes,
    course_id: payload.courseId ?? null,
    subject_id: payload.subjectId ?? null,
  }).then((body) => body.data);
}

/**
 * PUT the file straight to storage on the signed URL.
 *
 * NOT through the API client, and deliberately so: the URL is a self-contained
 * bearer capability for one path, and it is the reason a 200 MB PDF never has to
 * pass through the API process. `fetch` rather than the typed client because the
 * response is an empty 200 from the storage gateway, not this platform's envelope.
 */
export async function uploadToSignedUrl(
  ticket: { uploadUrl: string; expiresInSeconds?: number },
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<void> {
  // XMLHttpRequest, not fetch, for one reason: fetch cannot report upload progress.
  // A 500-file batch with no per-file movement looks hung, and the operator's next
  // action is to refresh and start again - which is how duplicates get created.
  return new Promise<void>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", ticket.uploadUrl);
    request.setRequestHeader("Content-Type", file.type || "application/pdf");

    request.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress)
        onProgress(event.loaded / event.total);
    };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) {
        onProgress?.(1);
        resolve();
        return;
      }
      reject(
        new QueryError(
          new ApiError(
            request.status,
            {
              type: "https://api.caprep.in/errors/upload",
              title: "Upload failed",
              status: request.status,
              detail: `Storage refused the upload (${request.status}). A signed URL is short-lived${ticket.expiresInSeconds ? ` (${ticket.expiresInSeconds}s)` : ""} - start the batch again and retry.`,
            },
            null,
          ),
        ),
      );
    };
    request.onerror = () =>
      reject(
        new QueryError(
          new ApiError(
            0,
            {
              type: "https://api.caprep.in/errors/upload",
              title: "Upload failed",
              status: 0,
              detail:
                "The transfer was interrupted. Check the connection and retry this file.",
            },
            null,
          ),
        ),
      );
    request.send(file);
  });
}

export function startIngestionJob(jobId: string) {
  return send<ApiSuccess<{ jobId: string; stage: string; status: string }>>(
    `/ingestion/jobs/${jobId}/start`,
    {},
  ).then((body) => body.data);
}

/**
 * `| undefined` on every optional field is not decoration: this project compiles
 * with `exactOptionalPropertyTypes`, so `{subjectId: undefined}` is a DIFFERENT type
 * from `{}` and the call site that conditionally spreads a value must say so.
 */
export interface ReviewDecision {
  decision: "APPROVE" | "REJECT" | "DUPLICATE";
  subjectId?: string | undefined;
  chapterId?: string | undefined;
  questionType?: string | undefined;
  marks?: number | undefined;
  difficulty?: string | undefined;
  correctAnswer?: string | undefined;
  explanation?: string | undefined;
  note?: string | undefined;
}

export function reviewDraft(draftId: string, decision: ReviewDecision) {
  return send<ApiSuccess<Record<string, unknown>>>(
    `/ingestion/drafts/${draftId}/review`,
    {
      decision: decision.decision,
      subject_id: decision.subjectId ?? null,
      chapter_id: decision.chapterId ?? null,
      question_type: decision.questionType ?? null,
      marks: decision.marks ?? null,
      difficulty: decision.difficulty ?? "MEDIUM",
      correct_answer: decision.correctAnswer ?? null,
      explanation: decision.explanation ?? null,
      note: decision.note ?? null,
    },
  ).then((body) => body.data);
}

export function publishQuestion(questionId: string) {
  return send<
    ApiSuccess<{ questionId: string; status: string; verifiedBy: string }>
  >(`/admin/questions/${questionId}/publish`, {}).then((body) => body.data);
}

export function publishMock(mockId: string) {
  return send<
    ApiSuccess<{ mockTestId: string; status: string; questionCount: number }>
  >(`/admin/mocks/${mockId}/publish`, {}).then((body) => body.data);
}

export function assignRole(userId: string, role: string, reason?: string) {
  return send<
    ApiSuccess<{
      userId: string;
      previousRole: string;
      role: string;
      claimUpdated: boolean;
      note: string | null;
    }>
  >(`/users/${userId}/role`, { role, reason: reason ?? null }).then(
    (body) => body.data,
  );
}

// -------------------------------------------------------------- collections --
//
// Shapes read from the live routes in `app/api/v1/collections.py`.
//
// THE TWO LISTS ARE NOT THE SAME LIST, and the UI must not blur them:
//   * a COLLECTION is a named container the student owns, with membership rows and
//     a note per row (`POST /collections`, `POST /collections/{id}/questions`).
//   * the LDR list (`GET /ldr`) is the practice loop's own flag: one boolean on the
//     progress row, set when a question is met and not yet mastered. It is
//     read-only here because the flag is written where the question is answered.

export interface Collection {
  id: string;
  name: string;
  description: string | null;
  kind: "MANUAL" | "SMART" | string;
  filters: Record<string, unknown> | null;
  isSystem: boolean;
  isPublic?: boolean;
  questionCount: number;
  createdAt?: string;
  updatedAt?: string;
}

export interface CollectionQuestion {
  questionId: string;
  text: string;
  questionType: string;
  difficulty: string;
  marks: number;
  subjectId: string;
  chapterId: string | null;
  isHistorical: boolean;
  financeActYear: string | null;
  /**
   * Repealed-provision warning. Travels with the question into every list on
   * purpose: a collection is where a student revises from, and revising a withdrawn
   * provision without noticing is the failure this field exists to prevent.
   */
  disclaimer: string | null;
  note: string | null;
  options: { label: string; text: string }[];
}

export interface CollectionDetail extends Collection {
  questions: CollectionQuestion[];
  count: number;
  total: number;
  page: number;
  limit: number;
  hasMore: boolean;
}

export interface LdrQuestion {
  questionId: string;
  text: string;
  questionType: string;
  difficulty: string;
  marks: number;
  subjectId: string;
  chapterId: string | null;
  isHistorical: boolean;
  financeActYear: string | null;
  disclaimer: string | null;
  attempts: number;
  accuracy: number | null;
  markedAt: string;
}

export function fetchCollections(signal?: AbortSignal) {
  return request<ApiList<Collection>>("/collections", signal);
}

export function fetchCollection(collectionId: string, signal?: AbortSignal) {
  return request<ApiSuccess<CollectionDetail>>(
    `/collections/${collectionId}`,
    signal,
  ).then((body) => body.data);
}

export function fetchLdr(signal?: AbortSignal) {
  return request<ApiList<LdrQuestion>>("/ldr", signal);
}

export function createCollection(input: {
  name: string;
  kind?: "MANUAL" | "SMART";
  description?: string | undefined;
}) {
  return send<ApiSuccess<Collection>>("/collections", {
    name: input.name,
    kind: input.kind ?? "MANUAL",
    description: input.description ?? null,
  }).then((body) => body.data);
}

export function renameCollection(
  collectionId: string,
  patch: { name?: string | undefined; description?: string | undefined },
) {
  // PATCH, not the PUT-shaped `send`, so an omitted field is left alone rather than
  // being sent as null and clearing it.
  return api
    .patch<ApiSuccess<Collection>>(`/collections/${collectionId}`, {
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.description !== undefined
        ? { description: patch.description }
        : {}),
    })
    .then((body) => body.data)
    .catch((error: unknown) => {
      throw new QueryError(error);
    });
}

export function deleteCollection(collectionId: string) {
  return api
    .delete<ApiSuccess<{ deleted: boolean; collectionId: string }>>(
      `/collections/${collectionId}`,
    )
    .then((body) => body.data)
    .catch((error: unknown) => {
      throw new QueryError(error);
    });
}

/**
 * Add a batch of questions.
 *
 * The response distinguishes `added` from `requested`, because a screen that shows
 * "12 added" when three were already in the collection has told the student
 * something untrue.
 */
export function addQuestionsToCollection(
  collectionId: string,
  questionIds: string[],
  note?: string | undefined,
) {
  return send<
    ApiSuccess<{
      collectionId: string;
      added: number;
      requested: number;
      alreadyPresent: number;
    }>
  >(`/collections/${collectionId}/questions`, {
    question_ids: questionIds,
    note: note ?? null,
  }).then((body) => body.data);
}

export function removeQuestionFromCollection(
  collectionId: string,
  questionId: string,
) {
  return api
    .delete<
      ApiSuccess<{ collectionId: string; questionId: string; removed: boolean }>
    >(`/collections/${collectionId}/questions/${questionId}`)
    .then((body) => body.data)
    .catch((error: unknown) => {
      throw new QueryError(error);
    });
}

// -------------------------------------------------------------- admin console --
//
// ONE FUNCTION PER ADMIN ENDPOINT, WRITTEN FROM THE RESPONSES THE API ACTUALLY SENDS.
//
// Types here were copied from the route payloads in `app/api/v1/admin.py` and
// `app/api/v1/access.py`, not invented. Where a field is absent from the payload it is
// marked optional rather than assumed present, because a screen that renders
// `undefined` in the middle of a sentence looks like a bug in the product.
//
// REQUEST BODIES ARE snake_case (see `createPaymentOrder` for why that is not a style
// preference), and every one of these routes is permission-gated on the SERVER. The UI
// hides sections a role cannot use; the API refuses them regardless.

export interface PlatformCounts {
  students: { total: number; active: number; newThisWeek: number };
  content: {
    documents: number;
    processing: number;
    failed: number;
    indexed: number;
    pages: number;
    extractedChars: number;
    storageBytes: number;
  };
  curriculum: {
    courses: number;
    subjects: number;
    chapters: number;
    questions: number;
    publishedQuestions: number;
    mockTests: number;
  };
  activity: {
    mockAttempts: number;
    completedAttempts: number;
    notificationsSent: number;
    aiEvents: number;
  };
  money: {
    revenueRupees: number;
    successfulPayments: number;
    failedPayments: number;
    activeSubscriptions: number;
  };
  generatedAt: string;
}

export function fetchAdminDashboard(signal?: AbortSignal) {
  return request<ApiSuccess<PlatformCounts>>("/admin/dashboard", signal).then(
    (b) => b.data,
  );
}

export interface AdminUser {
  id: string;
  email: string | null;
  displayName: string | null;
  role: string;
  isActive: boolean;
  avatarUrl: string | null;
  phone: string | null;
  targetLevel: string | null;
  targetExamDate: string | null;
  syllabusScheme: string;
  createdAt: string | null;
  lastActiveAt: string | null;
  deletedAt: string | null;
  subscription: {
    tier: string;
    status: string;
    expiresAt: string | null;
  } | null;
}

export interface AdminUserList extends ApiList<AdminUser> {
  data: AdminUser[];
}

export function fetchAdminUsers(
  params: {
    q?: string;
    role?: string;
    status?: string;
    page?: number;
    limit?: number;
  } = {},
  signal?: AbortSignal,
) {
  const query = new URLSearchParams();
  if (params.q) query.set("q", params.q);
  if (params.role) query.set("role", params.role);
  if (params.status) query.set("status", params.status);
  query.set("page", String(params.page ?? 1));
  query.set("limit", String(params.limit ?? 50));
  return request<ApiList<AdminUser>>(`/admin/users?${query}`, signal);
}

export interface AdminUserDetail {
  user: AdminUser;
  entitlements: {
    tier: string;
    status: string;
    entitlements: string[];
    isPremium: boolean;
  };
  progress: {
    questionsTouched: number;
    attempts: number;
    correct: number;
    accuracy: number | null;
  };
  badges: { code: string; earnedAt: string | null }[];
}

export function fetchAdminUser(userId: string, signal?: AbortSignal) {
  return request<ApiSuccess<AdminUserDetail>>(
    `/admin/users/${encodeURIComponent(userId)}`,
    signal,
  ).then((b) => b.data);
}

/** Change a role or suspend an account. Both are audited server-side. */
export function updateAdminUser(
  userId: string,
  changes: {
    role?: string;
    isActive?: boolean;
    displayName?: string;
    note?: string;
  },
) {
  return send<ApiSuccess<AdminUser & { claimUpdated: boolean }>>(
    `/admin/users/${encodeURIComponent(userId)}`,
    changes,
  ).then((b) => b.data);
}

export interface PermissionMatrix {
  roles: {
    role: string;
    rank: number;
    assignable: boolean;
    permissions: string[];
  }[];
  permissions: { key: string; ownerOnly: boolean }[];
}

export function fetchPermissionMatrix(signal?: AbortSignal) {
  return request<ApiSuccess<PermissionMatrix>>(
    "/admin/permissions",
    signal,
  ).then((b) => b.data);
}

/** What THIS caller may do, straight from the server's own answer. */
export function fetchMyPermissions(signal?: AbortSignal) {
  return request<
    ApiSuccess<{ role: string; permissions: string[]; isOwner: boolean }>
  >("/admin/me/permissions", signal).then((b) => b.data);
}

export interface PlatformSetting {
  key: string;
  /** Stored as `{"value": ...}` so a setting can hold a bool, a number or a string. */
  value: { value: unknown };
  description: string | null;
  isPublic: boolean;
  updatedAt: string | null;
}

export function fetchSettings(signal?: AbortSignal) {
  return request<ApiSuccess<{ settings: PlatformSetting[] }>>(
    "/admin/settings",
    signal,
  ).then((b) => b.data.settings);
}

export function updateSetting(key: string, value: unknown) {
  return sendWith<
    ApiSuccess<{ key: string; value: unknown; updatedAt: string | null }>
  >("PUT", "/admin/settings", { key, value: { value } }).then((b) => b.data);
}

export interface AuditEntry {
  id: string;
  action: string;
  summary: string | null;
  actorEmail: string | null;
  actorRole: string | null;
  actorUserId: string | null;
  targetType: string | null;
  targetId: string | null;
  changes: Record<string, unknown> | null;
  ipAddress: string | null;
  createdAt: string | null;
}

export function fetchAuditLog(
  params: {
    action?: string;
    actorUserId?: string;
    page?: number;
    limit?: number;
  } = {},
  signal?: AbortSignal,
) {
  const query = new URLSearchParams();
  if (params.action) query.set("action", params.action);
  if (params.actorUserId) query.set("actor_user_id", params.actorUserId);
  query.set("page", String(params.page ?? 1));
  query.set("limit", String(params.limit ?? 50));
  return request<ApiList<AuditEntry>>(`/admin/audit?${query}`, signal);
}

/**
 * A checkout intent, as an operator sees it.
 *
 * Amounts arrive as whole rupees plus a remainder in paise. The screen formats those
 * two numbers; it does not divide. `entitlement` is the live subscription, which is
 * the only thing that grants access — a PAID order with a null entitlement is the
 * row an operator is here to find.
 */
export interface AdminPaymentOrder {
  id: string;
  email: string | null;
  displayName: string | null;
  userId: string;
  planCode: string;
  tier: string;
  amountPaise: number;
  amountRupees: number;
  amountRemainderPaise: number;
  currency: string;
  status: string;
  provider: string;
  providerOrderId: string | null;
  providerPaymentId: string | null;
  providerStatus: string | null;
  receipt: string;
  paidAt: string | null;
  failureReason: string | null;
  createdAt: string | null;
  entitlement: {
    status: string;
    tier: string;
    expiresAt: string | null;
  } | null;
}

export interface AdminPaymentOrderList {
  data: AdminPaymentOrder[];
  meta: ApiListMeta & {
    paidOrders: number;
    paidPaise: number;
    paidRupees: number;
    paidRemainderPaise: number;
    byStatus: Record<string, number>;
  };
}

export function fetchAdminPaymentOrders(
  params: {
    status?: string;
    planCode?: string;
    q?: string;
    page?: number;
    limit?: number;
  } = {},
  signal?: AbortSignal,
) {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.planCode) query.set("plan_code", params.planCode);
  if (params.q) query.set("q", params.q);
  query.set("page", String(params.page ?? 1));
  query.set("limit", String(params.limit ?? 50));
  return request<AdminPaymentOrderList>(
    `/admin/payments/orders?${query}`,
    signal,
  );
}

/**
 * A webhook row with the body left out.
 *
 * `eventId` is the provider's idempotency key, not a secret. The raw payload is not
 * a field on this type, and the page must not invent one from a leftover key.
 */
export interface AdminPaymentEvent {
  id: string;
  provider: string;
  eventId: string;
  eventType: string;
  signatureVerified: boolean;
  processedAt: string | null;
  processingError: string | null;
  userId: string | null;
  email: string | null;
  createdAt: string | null;
}

export interface AdminPaymentEventList {
  data: AdminPaymentEvent[];
  meta: ApiListMeta & {
    payloadOmitted: boolean;
    unprocessed: number;
  };
}

export function fetchAdminPaymentEvents(
  params: {
    eventType?: string;
    q?: string;
    page?: number;
    limit?: number;
  } = {},
  signal?: AbortSignal,
) {
  const query = new URLSearchParams();
  if (params.eventType) query.set("event_type", params.eventType);
  if (params.q) query.set("q", params.q);
  query.set("page", String(params.page ?? 1));
  query.set("limit", String(params.limit ?? 50));
  return request<AdminPaymentEventList>(
    `/admin/payments/events?${query}`,
    signal,
  );
}

export interface AnalyticsSummary {
  windowDays: number;
  totals: { event: string; count: number }[];
  daily: { date: string; count: number }[];
  totalEvents: number;
}

export function fetchAnalytics(days = 30, signal?: AbortSignal) {
  return request<ApiSuccess<AnalyticsSummary>>(
    `/admin/analytics?days=${days}`,
    signal,
  ).then((b) => b.data);
}

export interface AdminBadge {
  id: string;
  code: string;
  name: string;
  description: string | null;
  icon: string | null;
  criteriaKind: string;
  criteriaValue: number | null;
  pointsReward: number;
  isActive: boolean;
  displayOrder: number;
  awardedCount: number;
}

export function fetchBadges(signal?: AbortSignal) {
  return request<ApiSuccess<{ badges: AdminBadge[] }>>(
    "/admin/badges",
    signal,
  ).then((b) => b.data.badges);
}

export function awardBadge(badgeId: string, userId: string) {
  return send<
    ApiSuccess<{ userId: string; badgeCode: string; awarded: boolean }>
  >(
    `/admin/badges/${encodeURIComponent(badgeId)}/award?user_id=${encodeURIComponent(userId)}`,
  ).then((b) => b.data);
}

export interface BroadcastResult {
  broadcastId: string;
  recipients: number;
}

/**
 * Send an announcement.
 *
 * `audience` is resolved SERVER-SIDE into recipients (all students, one role, or every
 * active subscriber). The client names the audience and never the list - a client that
 * could name its own recipients is a way to send mail to arbitrary accounts.
 *
 * `linkUrl` must be a path on this site; the API rejects an absolute URL, which is what
 * stops a notification from becoming a phishing link inside a trusted inbox.
 */
export function sendBroadcast(input: {
  title: string;
  body: string;
  kind?: string;
  audience: string;
  role?: string | null;
  linkUrl?: string | null;
}) {
  return send<ApiSuccess<BroadcastResult>>("/admin/notifications", {
    title: input.title,
    body: input.body,
    kind: input.kind ?? "ANNOUNCEMENT",
    audience: input.audience,
    role: input.role ?? null,
    link_url: input.linkUrl ?? null,
  }).then((b) => b.data);
}

export interface BroadcastRow {
  broadcastId: string;
  title: string;
  kind: string;
  recipients: number;
  read: number;
  sentAt: string | null;
}

export function fetchBroadcasts(page = 1, limit = 25, signal?: AbortSignal) {
  return request<ApiList<BroadcastRow>>(
    `/admin/notifications?page=${page}&limit=${limit}`,
    signal,
  );
}

// ---------------------------------------------------------- content library --
//
// The admin's view of the library, which is a different question from the student's:
// archived rows are visible (behind a flag), drafts are visible, and every row carries
// the pipeline state. The student's list never shows any of that - it is filtered by
// `document_filter` server-side.

export interface AdminDocument {
  id: string;
  title: string;
  kind: string;
  status: string;
  accessTier: string;
  isPublished: boolean;
  pageCount: number | null;
  error: string | null;
  originalFilename: string;
  sizeBytes: number;
  courseId: string | null;
  subjectId: string | null;
  chapterId: string | null;
  topicId: string | null;
  module: string | null;
  difficulty: string;
  tags: string[];
  batchId: string | null;
  ingestionJobId: string | null;
  uploadedBy: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  deletedAt: string | null;
  /** Set only on a search response: why this row matched. `TEXT` means the phrase was
   *  found inside the document's extracted pages, not in its title or file name. */
  matchSource?: "TEXT" | "METADATA";
  /** The page the match is on, when it came from the extracted text. */
  matchedPage?: number | null;
}

export function fetchAdminDocuments(
  params: {
    q?: string;
    status?: string;
    kind?: string;
    accessTier?: string;
    courseId?: string;
    includeArchived?: boolean;
    batchId?: string;
    page?: number;
    limit?: number;
  } = {},
  signal?: AbortSignal,
) {
  const query = new URLSearchParams();
  if (params.q) query.set("q", params.q);
  if (params.status) query.set("status", params.status);
  if (params.kind) query.set("kind", params.kind);
  if (params.accessTier) query.set("access_tier", params.accessTier);
  if (params.courseId) query.set("course_id", params.courseId);
  if (params.includeArchived) query.set("include_archived", "true");
  if (params.batchId) query.set("batch_id", params.batchId);
  query.set("page", String(params.page ?? 1));
  query.set("limit", String(params.limit ?? 25));
  return request<ApiList<AdminDocument>>(
    `/admin/content/documents?${query}`,
    signal,
  );
}

/** One upload manifest entry, and what the batch endpoint decided about it. */
export interface UploadTicket {
  filename: string;
  documentId: string;
  uploadUrl: string;
  path: string;
  subjectId?: string | null;
  /** Present on the batch response, so every row knows its batch without a second call. */
  batchId?: string;
}

export interface UploadBatch {
  batchId: string;
  acceptedCount: number;
  skippedCount: number;
  accepted: UploadTicket[];
  skipped: {
    filename: string;
    reason: string;
    existingDocumentId?: string;
    detail?: string;
  }[];
  /** The concurrency the server suggests for the PUT phase. */
  recommendedConcurrency?: number;
}

/**
 * Create a batch: one call for up to 500 files.
 *
 * Each entry declares its size and sha256 BEFORE any bytes move, which is what lets the
 * server detect duplicates against the whole library and hand back a signed URL per
 * file. The browser then PUTs straight to storage - the PDFs never pass through the API.
 *
 * The checksum is computed in the browser (`crypto.subtle.digest`) and re-verified
 * server-side after the upload, so a truncated transfer is caught rather than indexed.
 */
export function createUploadBatch(input: {
  files: {
    filename: string;
    sizeBytes: number;
    contentType: string;
    checksumSha256: string;
  }[];
  kind?: string;
  courseId?: string | null;
  subjectId?: string | null;
  chapterId?: string | null;
  module?: string | null;
  difficulty?: string;
  accessTier?: string;
  tags?: string[];
  skipDuplicates?: boolean;
}) {
  return send<ApiSuccess<UploadBatch>>("/admin/content/uploads", {
    files: input.files.map((file) => ({
      filename: file.filename,
      size_bytes: file.sizeBytes,
      content_type: file.contentType,
      checksum_sha256: file.checksumSha256,
    })),
    kind: input.kind ?? "STUDY_MATERIAL",
    course_id: input.courseId ?? null,
    subject_id: input.subjectId ?? null,
    chapter_id: input.chapterId ?? null,
    module: input.module ?? null,
    difficulty: input.difficulty ?? "MEDIUM",
    access_tier: input.accessTier ?? "PREMIUM",
    tags: input.tags ?? [],
    skip_duplicates: input.skipDuplicates ?? true,
  }).then((b) => b.data);
}

export interface BatchProgress {
  batchId: string;
  total: number;
  byStatus: Record<string, number>;
  uploaded: number;
  processing: number;
  indexed: number;
  failed: number;
  remaining: number;
  documents: {
    id: string;
    title: string;
    status: string;
    error: string | null;
    pageCount: number | null;
  }[];
}

export function fetchBatchProgress(batchId: string, signal?: AbortSignal) {
  return request<ApiSuccess<BatchProgress>>(
    `/admin/content/batches/${encodeURIComponent(batchId)}`,
    signal,
  ).then((b) => b.data);
}

/** Queue processing for one document whose bytes are in storage. */
export function startProcessing(documentId: string) {
  return send<
    ApiSuccess<{
      documentId: string;
      jobId: string;
      status: string;
      enqueued: boolean;
    }>
  >(`/admin/content/documents/${encodeURIComponent(documentId)}/start`).then(
    (b) => b.data,
  );
}

export function reprocessDocument(documentId: string) {
  return send<
    ApiSuccess<{ documentId: string; jobId: string | null; requeued: boolean }>
  >(
    `/admin/content/documents/${encodeURIComponent(documentId)}/reprocess`,
  ).then((b) => b.data);
}

export function archiveDocument(documentId: string) {
  return api
    .delete<ApiSuccess<{ documentId: string; archived: boolean }>>(
      `/admin/content/documents/${encodeURIComponent(documentId)}`,
    )
    .then((b) => b.data)
    .catch((error: unknown) => {
      throw new QueryError(error);
    });
}

export function restoreDocument(documentId: string) {
  return send<ApiSuccess<{ documentId: string; restored: boolean }>>(
    `/admin/content/documents/${encodeURIComponent(documentId)}/restore`,
  ).then((b) => b.data);
}

export function updateDocumentMetadata(
  documentId: string,
  changes: {
    title?: string;
    kind?: string;
    accessTier?: string;
    courseId?: string | null;
    subjectId?: string | null;
    chapterId?: string | null;
    module?: string | null;
    difficulty?: string;
    isPublished?: boolean;
    tags?: string[];
  },
) {
  return sendWith<ApiSuccess<AdminDocument>>(
    "PATCH",
    `/admin/content/documents/${encodeURIComponent(documentId)}`,
    {
      ...(changes.title !== undefined ? { title: changes.title } : {}),
      ...(changes.kind !== undefined ? { kind: changes.kind } : {}),
      ...(changes.accessTier !== undefined
        ? { access_tier: changes.accessTier }
        : {}),
      ...(changes.courseId !== undefined
        ? { course_id: changes.courseId }
        : {}),
      ...(changes.subjectId !== undefined
        ? { subject_id: changes.subjectId }
        : {}),
      ...(changes.chapterId !== undefined
        ? { chapter_id: changes.chapterId }
        : {}),
      ...(changes.module !== undefined ? { module: changes.module } : {}),
      ...(changes.difficulty !== undefined
        ? { difficulty: changes.difficulty }
        : {}),
      ...(changes.isPublished !== undefined
        ? { is_published: changes.isPublished }
        : {}),
      ...(changes.tags !== undefined ? { tags: changes.tags } : {}),
    },
  ).then((b) => b.data);
}

export interface ExtractedText {
  documentId: string;
  title: string;
  pageCount: number | null;
  pages: { pageNumber: number; text: string; charCount: number }[];
}

export function fetchDocumentText(documentId: string, signal?: AbortSignal) {
  return request<ApiSuccess<ExtractedText>>(
    `/admin/content/documents/${encodeURIComponent(documentId)}/text`,
    signal,
  ).then((b) => b.data);
}

export function fetchDocumentDownloadUrl(documentId: string) {
  return request<ApiSuccess<{ url: string; expiresInSeconds: number }>>(
    `/admin/content/documents/${encodeURIComponent(documentId)}/download`,
  ).then((b) => b.data);
}

/** Categorise many documents in one call. Also how the bulk-upload form finishes. */
export function bulkUpdateMetadata(input: {
  documentIds: string[];
  courseId?: string | null;
  subjectId?: string | null;
  chapterId?: string | null;
  topicId?: string | null;
  kind?: string;
  accessTier?: string;
  difficulty?: string;
  module?: string | null;
  tags?: string[];
  isPublished?: boolean;
}) {
  return send<ApiSuccess<{ updated: number; documentIds: string[] }>>(
    "/admin/content/bulk-metadata",
    {
      document_ids: input.documentIds,
      course_id: input.courseId ?? null,
      subject_id: input.subjectId ?? null,
      chapter_id: input.chapterId ?? null,
      topic_id: input.topicId ?? null,
      kind: input.kind ?? null,
      access_tier: input.accessTier ?? null,
      difficulty: input.difficulty ?? null,
      module: input.module ?? null,
      tags: input.tags ?? null,
      is_published: input.isPublished ?? null,
    },
  ).then((b) => b.data);
}

// ------------------------------------------------------------- access control --

export interface AccessGrant {
  id: string;
  whoScope: "USER" | "ROLE" | "TIER" | "PLAN";
  effect: "ALLOW" | "DENY";
  userId: string | null;
  role: string | null;
  tier: string | null;
  planCode: string | null;
  courseId: string | null;
  subjectId: string | null;
  kind: string | null;
  reason: string | null;
  grantedBy: string | null;
  expiresAt: string | null;
  revokedAt: string | null;
  createdAt: string | null;
  isLive: boolean;
  covers: {
    courseId: string | null;
    subjectId: string | null;
    kind: string | null;
    wholeLibrary: boolean;
  };
}

export function fetchGrants(
  params: {
    userId?: string;
    courseId?: string;
    whoScope?: string;
    effect?: string;
    includeRevoked?: boolean;
  } = {},
  signal?: AbortSignal,
) {
  const query = new URLSearchParams();
  if (params.userId) query.set("user_id", params.userId);
  if (params.courseId) query.set("course_id", params.courseId);
  if (params.whoScope) query.set("who_scope", params.whoScope);
  if (params.effect) query.set("effect", params.effect);
  if (params.includeRevoked) query.set("include_revoked", "true");
  return request<ApiList<AccessGrant>>(`/admin/access/grants?${query}`, signal);
}

export function createGrant(input: {
  whoScope: string;
  effect?: string;
  userId?: string | null;
  role?: string | null;
  tier?: string | null;
  planCode?: string | null;
  courseId?: string | null;
  subjectId?: string | null;
  kind?: string | null;
  reason?: string | null;
  expiresAt?: string | null;
}) {
  return send<ApiSuccess<AccessGrant>>("/admin/access/grants", {
    who_scope: input.whoScope,
    effect: input.effect ?? "ALLOW",
    user_id: input.userId ?? null,
    role: input.role ?? null,
    tier: input.tier ?? null,
    plan_code: input.planCode ?? null,
    course_id: input.courseId ?? null,
    subject_id: input.subjectId ?? null,
    kind: input.kind ?? null,
    reason: input.reason ?? null,
    expires_at: input.expiresAt ?? null,
  }).then((b) => b.data);
}

export function revokeGrant(grantId: string) {
  return api
    .delete<ApiSuccess<AccessGrant>>(
      `/admin/access/grants/${encodeURIComponent(grantId)}`,
    )
    .then((b) => b.data)
    .catch((error: unknown) => {
      throw new QueryError(error);
    });
}

export interface StudentAccess {
  userId: string;
  email: string | null;
  displayName: string | null;
  role: string;
  isActive: boolean;
  tier: string;
  planCode: string | null;
  courseIds: string[];
  grants: AccessGrant[];
  allowGrants: number;
  denyGrants: number;
}

export function fetchStudentAccess(userId: string, signal?: AbortSignal) {
  return request<ApiSuccess<StudentAccess>>(
    `/admin/access/users/${encodeURIComponent(userId)}`,
    signal,
  ).then((b) => b.data);
}

/** Run the student's own access decision for one document and get the reason. */
export function explainAccess(userId: string, documentId: string) {
  return request<
    ApiSuccess<{
      userId: string;
      documentId: string;
      documentTitle: string;
      documentTier: string;
      documentStatus: string;
      isPublished: boolean;
      viewerTier: string;
      decision: string;
      canRead: boolean;
      reason: string;
    }>
  >(
    `/admin/access/explain?user_id=${encodeURIComponent(userId)}&document_id=${encodeURIComponent(documentId)}`,
  ).then((b) => b.data);
}

// ------------------------------------------------------- studio and assistant

export interface AdminQuestion {
  id: string;
  text: string;
  truncated: boolean;
  explanation: string | null;
  questionType: string;
  difficulty: string;
  marks: number;
  negativeMarks: number;
  correctAnswer: string | null;
  modelAnswer: string | null;
  status: string;
  isHistorical: boolean;
  financeActYear: string | null;
  disclaimerText: string | null;
  courseId: string;
  subjectId: string;
  chapterId: string | null;
  topicId: string | null;
  year: number | null;
  isPremium: boolean;
  source: string | null;
  createdAt: string;
}

export function fetchAdminQuestions(
  params: { q?: string; status?: string; page?: number } = {},
  signal?: AbortSignal,
) {
  const query = new URLSearchParams();
  if (params.q) query.set("q", params.q);
  if (params.status) query.set("status", params.status);
  if (params.page) query.set("page", String(params.page));
  return request<ApiList<AdminQuestion>>(`/admin/questions?${query}`, signal);
}

export function createAdminQuestion(body: {
  course_id: string;
  subject_id: string;
  text: string;
  question_type: string;
  difficulty?: string;
  correct_answer?: string;
  is_historical?: boolean;
  disclaimer_text?: string;
  finance_act_year?: string;
  options?: { label: string; text: string }[];
}) {
  return send<ApiSuccess<AdminQuestion>>("/admin/questions", body).then((b) => b.data);
}

export interface AdminMock {
  id: string;
  courseId: string;
  subjectId: string | null;
  title: string;
  kind: string;
  durationMin: number;
  totalMarks: number;
  status: string;
  isPremium: boolean;
  syllabusScheme: string;
  questionIds: string[];
  questionCount: number;
  createdAt: string;
}

export function fetchAdminMocks(signal?: AbortSignal) {
  return request<ApiSuccess<{ mocks: AdminMock[] }>>("/admin/mocks", signal).then(
    (b) => b.data.mocks,
  );
}

export function createAdminMock(body: {
  course_id: string;
  title: string;
  kind: string;
  duration_min: number;
  total_marks: number;
  question_ids?: string[];
}) {
  return send<ApiSuccess<AdminMock>>("/admin/mocks", body).then((b) => b.data);
}

export interface AdminCurriculum {
  courses: {
    id: string;
    code: string;
    name: string;
    level: string;
    syllabusScheme: string;
    isActive: boolean;
    description: string | null;
  }[];
  subjects: {
    id: string;
    courseId: string;
    code: string;
    name: string;
    groupName: string | null;
    paperNumber: number | null;
    isActive: boolean;
  }[];
  chapters: {
    id: string;
    subjectId: string;
    code: string;
    name: string;
    sequence: number;
    weightage: number;
    isActive: boolean;
  }[];
  topics: {
    id: string;
    chapterId: string;
    code: string;
    name: string;
    sequence: number;
    isActive: boolean;
  }[];
}

export function fetchAdminCurriculum(signal?: AbortSignal) {
  return request<ApiSuccess<AdminCurriculum>>("/admin/curriculum", signal).then(
    (b) => b.data,
  );
}

export function createAdminCourse(body: {
  code: string;
  name: string;
  level: string;
  syllabus_scheme?: string;
}) {
  return send<ApiSuccess<{ id: string }>>("/admin/curriculum/courses", body).then(
    (b) => b.data,
  );
}

export function retireAdminCourse(courseId: string, isActive: boolean) {
  return sendWith<ApiSuccess<{ id: string }>>(
    "PATCH",
    `/admin/curriculum/courses/${encodeURIComponent(courseId)}`,
    { is_active: isActive },
  ).then((b) => b.data);
}

export interface CataloguePlan {
  code: string;
  tier: string;
  label: string;
  amountPaise: number;
  amountRupees: number;
  durationDays: number;
  tagline: string;
  features: string[];
  entitlements: string[];
  recommended: boolean;
}

export function fetchAdminPlans(signal?: AbortSignal) {
  return request<
    ApiSuccess<{ editable: boolean; reason: string; plans: CataloguePlan[] }>
  >("/admin/plans", signal).then((b) => b.data);
}

export function saveAdminPlan(body: {
  code: string;
  amount_paise: number;
  duration_days: number;
}) {
  return sendWith<
    ApiSuccess<{ code: string; amountPaise: number; amountRupees: number; durationDays: number }>
  >("PUT", "/admin/plans", body).then((b) => b.data);
}

export interface GatewayStatus {
  checkoutReady: boolean;
  webhookReady: boolean;
  source: "environment" | "saved" | "missing";
  keyId: string | null;
  secretSet: boolean;
  webhookSecretSet: boolean;
  missingForCheckout: string[];
  missingForWebhook: string[];
  note: string;
}

export function fetchGatewayStatus(signal?: AbortSignal) {
  return request<ApiSuccess<GatewayStatus>>("/admin/payments/gateway", signal).then(
    (b) => b.data,
  );
}

export function saveGatewayKeys(body: {
  key_id?: string;
  key_secret?: string;
  webhook_secret?: string;
}) {
  return sendWith<ApiSuccess<GatewayStatus>>("PUT", "/admin/payments/gateway", body).then(
    (b) => b.data,
  );
}

export interface UnclassifiedQuestion {
  id: string;
  text: string;
  subjectCode: string;
  subjectName: string;
  chapterId: string | null;
}

export function fetchUnclassified(signal?: AbortSignal) {
  return request<
    ApiSuccess<{
      total: number;
      questions: UnclassifiedQuestion[];
      components: { id: string; code: string; name: string; parentSubjectId: string }[];
      note: string;
    }>
  >("/admin/questions/unclassified", signal).then((b) => b.data);
}

export function assignQuestionComponent(questionId: string, componentId: string) {
  return sendWith<ApiSuccess<{ answerChanged: boolean; componentCode: string }>>(
    "PATCH",
    `/admin/questions/${questionId}/component`,
    { component_id: componentId },
  ).then((b) => b.data);
}

export function recordLawNotice(citation: string, summary: string) {
  return send<
    ApiSuccess<{ matched: number; flagged: number; answersChanged: boolean; capped: boolean }>
  >("/admin/law-notices", { citation, summary }).then((b) => b.data);
}

export interface AiConfiguration {
  enabled: boolean;
  providerConfigured: boolean;
  missingEnv: string[];
  monthlyCeilingUsd: number;
  freeQueriesPerDay: number;
  grounding: string;
  groundingOptional: boolean;
  generatesAnswers: boolean;
  note: string;
}

export function fetchAdminAi(signal?: AbortSignal) {
  return request<ApiSuccess<AiConfiguration>>("/admin/ai", signal).then((b) => b.data);
}

export interface StorageStatus {
  configured: boolean;
  missingEnv: string[];
  objects: null;
  note: string;
}

export function fetchAdminStorage(signal?: AbortSignal) {
  return request<ApiSuccess<StorageStatus>>("/admin/storage", signal).then((b) => b.data);
}

export interface AssistantStatus {
  enabled: boolean;
  generatesAnswers: boolean;
  providerConfigured: boolean;
  note: string;
}

export function fetchAssistantStatus(signal?: AbortSignal) {
  return request<ApiSuccess<AssistantStatus>>("/assistant/status", signal).then(
    (b) => b.data,
  );
}

export interface AssistantAnswer {
  query: string;
  answer: string | null;
  answerKind?: "study_suggestion" | null;
  notLegalAuthority?: boolean;
  generatesAnswers: boolean;
  note: string;
  quotations: {
    documentId: string;
    title: string;
    pageNumber: number;
    excerpt: string;
  }[];
  questions: { questionId: string; excerpt: string }[];
  remainingToday: number;
}

export function askAssistant(query: string) {
  return send<ApiSuccess<AssistantAnswer>>("/assistant/ask", { query }).then(
    (b) => b.data,
  );
}

export function reportQuestion(questionId: string, reason: string, detail: string) {
  return send<ApiSuccess<{ id: string; status: string }>>(
    `/practice/questions/${encodeURIComponent(questionId)}/flags`,
    { reason, detail },
  ).then((b) => b.data);
}

export interface QuestionFlagRow {
  id: string;
  questionId: string;
  questionText: string;
  reason: string;
  detail: string | null;
  status: string;
  resolutionNote: string | null;
  createdAt: string;
}

export function fetchQuestionFlags(signal?: AbortSignal) {
  return request<ApiSuccess<{ flags: QuestionFlagRow[]; note: string }>>(
    "/admin/question-flags?status=OPEN",
    signal,
  ).then((b) => b.data);
}

export function resolveQuestionFlag(flagId: string, status: string) {
  return sendWith<ApiSuccess<{ id: string; status: string }>>(
    "PATCH",
    `/admin/question-flags/${encodeURIComponent(flagId)}`,
    { status, resolution_note: "Reviewed in the question bank. The question itself is unchanged." },
  ).then((b) => b.data);
}
