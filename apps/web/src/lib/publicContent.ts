/**
 * The copy behind the public site, in one module.
 *
 * WHY NOT INLINE IN THE PAGES
 *
 * The same three facts appear on the landing page, `/features` and `/faq`: which
 * levels are covered, how a past paper becomes a question, and what happens to a
 * historical taxation question. When they live in JSX, the day one changes the
 * other two keep asserting the old version - and the FAQ is exactly where that is
 * most damaging, because it is the page a cautious visitor reads to check whether
 * the marketing is true.
 *
 * WHAT IS DELIBERATELY ABSENT
 *
 * No testimonials, no customer counts, no logos, no rankings, no partnerships.
 * There are no users yet; a fabricated logo bar or a made-up quote from "Ananya
 * S., AIR 12" is the most common way a page in this category lies, and it is the
 * kind of lie a student cannot check until they have already paid. The honest
 * substitute for social proof is verifiable third-party DATA (the ICAI figures,
 * which live in `lib/examData.ts`) and statements about what the product does.
 *
 * Every claim below maps to something built and tested in `apps/api` or
 * `apps/web`. If a feature is not built, it is in `ROADMAP`, labelled as such.
 */

/** The one sign-up entry point, so no CTA can drift away from the flow. */
export const SIGNUP_HREF = '/signup'
export const SIGNIN_HREF = '/login'
export const DASHBOARD_HREF = '/dashboard'

export interface PublicLink {
  href: string
  label: string
}

/**
 * The public nav.
 *
 * Five items. A visitor deciding whether to sign up has four questions (what is
 * it, does it work, what does it cost, can I trust it) and every extra link is
 * another way to leave without answering them.
 *
 * `anchor` links stay on the landing page; route links go to their own page.
 * Kept as data so the desktop bar and the mobile sheet cannot disagree.
 */
export const PUBLIC_NAV: PublicLink[] = [
  { href: '/features', label: 'Features' },
  // Absolute, not `#how`: this nav is rendered on every public page, and a bare
  // anchor does nothing at all when you are on /pricing.
  { href: '/#how', label: 'How it works' },
  { href: '/pricing', label: 'Pricing' },
  { href: '/faq', label: 'FAQ' },
]

/** Footer columns. Legal pages are real routes, not dead links. */
export const FOOTER_COLUMNS: { heading: string; links: PublicLink[] }[] = [
  {
    heading: 'Product',
    links: [
      { href: '/features', label: 'Features' },
      { href: '/#how', label: 'How it works' },
      { href: '/pricing', label: 'Pricing' },
      { href: '/#calculator', label: 'Attempt health check' },
    ],
  },
  {
    heading: 'Study',
    links: [
      { href: '/features#question-bank', label: 'Question bank' },
      { href: '/features#practice', label: 'Practice' },
      { href: '/features#mocks', label: 'Mock exams' },
      { href: '/features#planner', label: 'Study planner' },
    ],
  },
  {
    heading: 'Support',
    links: [
      { href: '/faq', label: 'FAQ' },
      { href: '/faq#content', label: 'How content is sourced' },
      { href: '/faq#pricing', label: 'Plans and payment' },
      { href: '/privacy', label: 'Privacy' },
    ],
  },
]

/**
 * The trust strip.
 *
 * Capability statements, not social proof - see the module note. Each one is a
 * property of the system that a visitor can verify within a minute of signing up,
 * which is the only kind of claim worth making before there are users.
 */
export const TRUST_POINTS: { title: string; body: string }[] = [
  {
    title: 'Every question is traceable',
    body: 'Each one records the paper, the year, the session and the page it came from, plus the confidence of the extraction that produced it.',
  },
  {
    title: 'A human signs off before students see it',
    body: 'Nothing reaches your practice screen straight from a PDF. Extraction produces drafts; a Content Manager reviews and publishes, and the verifier is recorded on the question.',
  },
  {
    title: 'Dated tax is labelled, never silently current',
    body: 'Questions governed by an earlier Finance Act keep their year and carry a visible disclaimer, so you are not revising a repealed provision by accident.',
  },
  {
    title: 'Your plan moves when your week does',
    body: 'The planner is rebuilt from what you actually completed. It will tell you which chapters it cannot fit rather than quietly dropping them.',
  },
]

/**
 * Feature catalogue - the source for the landing feature grid, `/features` and the
 * "Study" footer column.
 *
 * `status` drives whether a card is presented as available or as roadmap. Nothing
 * is ever described as available without a route and a test behind it.
 */
export interface Feature {
  id: string
  title: string
  body: string
  /** Longer form, used on /features. */
  detail: string
  status: 'AVAILABLE' | 'IN_BUILD'
}

export const FEATURES: Feature[] = [
  {
    id: 'question-bank',
    title: 'A question bank you can actually search',
    body: 'Filter by course, paper, chapter, year, marks, question type and attempt - then open any question with its options.',
    detail:
      'Full-text search over the published bank, ranked with PostgreSQL relevance, plus structured filters for the things ICAI questions are actually organised by: level, paper, chapter, session, marks and question type. Results open into the question itself with its options and its source, so a search is a way into practice rather than a list you have to leave.',
    status: 'AVAILABLE',
  },
  {
    id: 'practice',
    title: 'Practice that tells you why',
    body: 'Answer a chapter at a time, get the verdict and the explanation, and let anything you get wrong enter the revision queue.',
    detail:
      'Draw a set by chapter or difficulty, answer, and the server marks it - the answer key never reaches the browser before you commit, so a leaked key is not possible. Wrong answers are scheduled for spaced repetition automatically, which is why the revision queue fills itself instead of asking you to maintain a list of mistakes.',
    status: 'AVAILABLE',
  },
  {
    id: 'mocks',
    title: 'Timed mocks on the ICAI pattern',
    body: 'A real paper, a running clock, a question palette with answered and marked states, and server-side scoring when you submit.',
    detail:
      'The attempt lives on the server, so refreshing, closing the tab or losing signal does not lose the paper - reopening resumes the same attempt with the same clock. Submission is scored server-side and the report shows every question with your answer, the key and the explanation.',
    status: 'AVAILABLE',
  },
  {
    id: 'progress',
    title: 'Progress you can act on',
    body: 'Accuracy by chapter and paper, time spent, streaks and the weak areas worth your next hour.',
    detail:
      'Per-chapter accuracy with weightage, so focus areas are ranked by how much the chapter matters rather than by how recently you touched it. The dashboard is a single request, which is why it renders in one pass instead of assembling itself from six loading states.',
    status: 'AVAILABLE',
  },
  {
    id: 'planner',
    title: 'A dated study plan',
    body: 'Your attempt, your hours, every chapter placed on a day - and a re-plan when the week goes wrong.',
    detail:
      'The planner reads your attempt date, the papers you are sitting, the hours you actually have and the chapters where your accuracy is weakest. Ask it to re-plan and it rebuilds from what you completed, including an explicit list of what does not fit rather than a plan that is quietly unachievable.',
    status: 'AVAILABLE',
  },
  {
    id: 'revision',
    title: 'Revision that finds what you are forgetting',
    body: 'Every wrong answer schedules itself, and the queue tells you what is due now.',
    detail:
      'An SM-2 style scheduler decides when a question comes back, with the interval held server-side: a client sends a grade, never a due date. The queue, the review action and its health are three endpoints, so nothing in the browser decides when you should see something again.',
    status: 'AVAILABLE',
  },
  {
    id: 'doubts',
    title: 'Ask a doubt without leaving the question',
    body: 'Threaded doubts, staff answers, and the reply that resolved it marked as the answer.',
    detail:
      'A doubt is a thread rather than a question-and-answer pair, because the message that resolves a doubt is rarely the first reply. Whoever asked can mark the reply that resolved it, which is also the only signal in the product that says whether support is working.',
    status: 'AVAILABLE',
  },
  {
    id: 'ldr',
    title: 'Marked for later, and collections',
    body: 'Flag a question mid-practice and it waits for you. Group what you flag into collections you name.',
    detail:
      'The bookmark is written by the practice loop itself, so it is a property of a question you have already met rather than a second list to maintain. Collections group those flags by what you are worried about - "revision before mock 3", "lost marks in costing" - instead of by where ICAI files them.',
    status: 'AVAILABLE',
  },
  {
    id: 'ingestion',
    title: 'Past papers become questions',
    body: 'Upload a paper; extraction, OCR fallback and question detection produce drafts for review.',
    detail:
      'A PDF is uploaded to private storage, its text is extracted, and pages that do not yield text fall back to OCR. Candidate questions are segmented, their metadata guessed, and each one lands in a review queue with a confidence score and its source page. Nothing is published by the pipeline.',
    status: 'AVAILABLE',
  },
  {
    id: 'historical',
    title: 'Historical taxation, handled honestly',
    body: 'Old-law questions stay in the bank with their Finance Act year and a disclaimer.',
    detail:
      'Taxation is the one subject where a correctly-answered question can teach you the wrong law. Questions that depend on a superseded Finance Act keep their year, are searchable and filterable as historical, and display a disclaimer in the admin review screen and in front of the student.',
    status: 'AVAILABLE',
  },
  {
    id: 'ai',
    title: 'A grounded AI assistant',
    body: 'A labelled study suggestion, only from excerpts you may already read.',
    detail:
      'Asking the library is off until an owner turns features.ai_assistant on. With AI_PROVIDER_API_KEY set, a suggestion is written only when an excerpt the student may read was found, and it is labelled as not a legal authority. Without excerpts, or without the key, no answer is invented.',
    /*
     * IN_BUILD, not AVAILABLE, and that is a statement about the deployment rather
     * than about the code. `features.ai_assistant` is False in
     * `app/services/platform_defaults.py` and no deployment in `infra/` sets
     * AI_PROVIDER_API_KEY, so no student can generate an answer today. The landing
     * grid renders only AVAILABLE features, so this card must not sit in it; the
     * roadmap names the assistant as not built, and the two lists have to agree.
     */
    status: 'IN_BUILD',
  },
]

/** The 5-step story, used by the landing page and `/features`. */
export const STEPS: { title: string; body: string }[] = [
  {
    title: 'Choose your attempt',
    body: 'Your level, which groups you are sitting, and the session you are targeting. ICAI examines groups separately, so the plan is built per group rather than per level.',
  },
  {
    title: 'Find questions by topic',
    body: 'Search the published bank or browse the syllabus down to a chapter. Every question shows its source paper and whether the law it tests has moved on.',
  },
  {
    title: 'Practise and get marked',
    body: 'Answer, submit, and see the verdict with the explanation. Nothing you get wrong disappears into a score - it enters the revision queue.',
  },
  {
    title: 'Follow the plan',
    body: 'Chapters are placed on days against the hours you have. Weak chapters come back sooner; the ones you have nailed come back later.',
  },
  {
    title: 'Review and correct',
    body: 'Mock reports and chapter accuracy show where marks are leaking, and the next week is rebuilt around that rather than around the original timetable.',
  },
]

/**
 * The ingestion pipeline, as the public explanation.
 *
 * The last two steps are the point of the section. A visitor reading "PDF to
 * questions" needs to know it is not an automatic content mill, because an
 * unreviewed question is worse than no question: it can be wrong, it can test
 * repealed law, and the student has no way to tell.
 */
export const INGESTION_STEPS: { title: string; body: string }[] = [
  {
    title: 'Upload',
    body: 'A past paper goes to private storage on a signed URL. It is never public, and the file does not pass through the API process.',
  },
  {
    title: 'Extract',
    body: 'Text is pulled page by page. Pages that yield no usable text fall back to OCR, and the resulting quality decides whether the paper can be processed at all.',
  },
  {
    title: 'Segment',
    body: 'The page text is split into candidate questions, with the page number kept so a reviewer can always check the original.',
  },
  {
    title: 'Detect metadata',
    body: 'Year, session, marks and question type are guessed and stored with a confidence score, because a wrong marks value is a scoring bug that outlives everyone who saw it.',
  },
  {
    title: 'Human QA',
    body: 'A reviewer works a queue, corrects the guesses, places the question in the syllabus and approves it. Approval creates a draft question - not a live one.',
  },
  {
    title: 'Publish',
    body: 'A Content Manager publishes, and the question becomes visible to students with the verifier recorded against it.',
  },
]

/**
 * How historical content is treated.
 *
 * Kept as structured data because the same three fields are surfaced in three
 * places (the student's question screen, the reviewer's queue and the filter) and
 * they must say the same thing in each.
 */
export const HISTORICAL_POINTS: { title: string; body: string }[] = [
  {
    title: 'It stays in the bank',
    body: 'A question from the 2019 assessment year is still worth practising - it is how the concept was examined. Deleting it would delete the pattern.',
  },
  {
    title: 'It carries its year',
    body: 'The Finance Act year is stored on the question, and questions are filterable and searchable as historical, so you can choose to practise them or avoid them.',
  },
  {
    title: 'It carries a disclaimer',
    body: 'In front of the student and in the reviewer\u2019s queue, a question on superseded law says so, in the place where the answer is given.',
  },
  {
    title: 'It is never presented as current law',
    body: 'This is enforced at the database and API level, not by editorial convention - a question flagged historical is marked historical everywhere it appears.',
  },
]

/**
 * FAQ.
 *
 * Two groups: what the product is, and how it is built. The second group is
 * unusual on a marketing page and deliberate - this is a product about exam
 * content, and the visitor most likely to pay is the one who wants to know where
 * the questions come from before they trust a single one of them.
 *
 * `id` exists so `/faq#content` can deep-link a specific group from the footer,
 * which is where a cautious visitor arrives from.
 */
export const FAQ_GROUPS: { id: string; heading: string; items: { q: string; a: string }[] }[] = [
  {
    id: 'product',
    heading: 'The product',
    items: [
      {
        q: 'What is the platform?',
        a: 'A preparation platform for the CA exams: a question bank built from published ICAI past papers, chapter-wise practice, timed mock exams, a study planner, revision scheduling and progress analytics. It is study material and a practice system, not a coaching course.',
      },
      {
        q: 'Which levels does it cover?',
        a: 'CA Foundation, CA Intermediate and CA Final, on the ICAI New Scheme. Intermediate and Final can be attempted group-wise or together, and each is tracked separately because ICAI marks them separately.',
      },
      {
        q: 'Does CA Final really only have two attempts a year now?',
        a: 'Yes. ICAI\u2019s notification of 6 April 2026 moved CA Final from three attempts a year to two, in May and November, from the May 2026 session onwards. January and September are discontinued for Final. Foundation and Intermediate still run three attempts. The planner reads these separately, so a Final student is never offered a September attempt.',
      },
      {
        q: 'Can I practise chapter-wise?',
        a: 'Yes. Pick your level, the paper and the chapter, and draw a set. You can also filter by difficulty. Answers are marked server-side and the explanation comes back with the verdict, never before it.',
      },
      {
        q: 'Are mock exams available?',
        a: 'Yes. Mocks are timed and scored on the server. The attempt is stored, so closing the tab and returning resumes the same paper with the same clock rather than starting a new one.',
      },
      {
        q: 'How does progress tracking work?',
        a: 'Every answered question writes an attempt and updates a per-chapter summary. The dashboard reads those back as accuracy, time spent, streaks and weak chapters \u2014 with chapter weightage applied, so the areas it tells you to revise are ranked by what they are worth rather than by what you happened to touch last.',
      },
      {
        q: 'What is LDR?',
        a: 'It started as "later-doesn\u2019t-review" \u2014 the list of questions you meant to come back to and did not. Here it is a flag on any question you have met, plus collections that group those flags into the piles you actually think in. Anything you get wrong is scheduled for revision automatically, so the list is for the questions you want back, not the ones the system already tracks.',
      },
      {
        q: 'Can I create collections?',
        a: 'Yes. A collection is yours to name, and you add flagged questions to it as you practise, so it groups questions by what you are worried about rather than by how ICAI files them.',
      },
      {
        q: 'What happens when I miss days?',
        a: 'The plan moves rather than lying to you. It will not schedule more minutes than your calendar holds, and when something genuinely does not fit it tells you which chapters are being dropped instead of quietly skipping them.',
      },
      {
        q: 'Do I need to pay?',
        a: 'No. The free tier needs no card and covers practice, revision, the planner and full mock papers. Paid tiers add the extras listed on the pricing page, and the plan catalogue there is served by the API rather than written into the page.',
      },
    ],
  },
  {
    id: 'content',
    heading: 'Where the content comes from',
    items: [
      {
        q: 'Is this ICAI material?',
        a: 'No, and we will not pretend otherwise. The questions come from published ICAI past papers, and all of it is study material rather than anything ICAI endorses or publishes. Always check notifications on icai.org for exam dates and syllabus changes.',
      },
      {
        q: 'Where do the questions come from?',
        a: 'From published ICAI past papers. Each paper is processed into candidate questions, which are then reviewed by a person before they can be published. Every question keeps its source paper, year, session and page.',
      },
      {
        q: 'How is an uploaded paper processed?',
        a: 'The PDF is stored privately, its text is extracted page by page, and pages with no usable text fall back to OCR. Candidate questions are segmented, their metadata guessed with a confidence score, and each one lands in a review queue. Nothing the pipeline produces is visible to a student: extraction ends at a draft.',
      },
      {
        q: 'Why is human review required?',
        a: 'Because an unreviewed extract can be wrong in ways a student cannot detect \u2014 a garbled option, a guessed marks value, or a question whose answer changed with a Finance Act. A wrong question is worse than a missing one, so a person approves every question and their name is recorded against it.',
      },
      {
        q: 'How are historical questions handled?',
        a: 'Questions that depend on a superseded Finance Act keep their Finance Act year and are marked historical in the database, the API and the interface. When you meet one, it says so where the answer is given. They stay in the bank \u2014 the pattern is still worth practising \u2014 but they are never presented as current law.',
      },
      {
        q: 'Can I report a question that looks wrong?',
        a: 'Reporting goes through the same queue the reviewers use, so a reported question is seen by the person who can fix it rather than by a support inbox. Until it is resolved the question carries its report status rather than being silently withdrawn.',
      },
    ],
  },
  {
    id: 'pricing',
    heading: 'Plans and payment',
    items: [
      {
        q: 'How does subscription work?',
        a: 'Payment is taken by Razorpay. The order is created on the server, the amount is looked up from the plan catalogue rather than accepted from the browser, and the subscription is activated when a signature-verified webhook confirms the payment \u2014 never because a browser said it succeeded. Where payments are not configured on a deployment, those endpoints say so instead of failing mysteriously.',
      },
      {
        q: 'What happens to my plan if I stop paying?',
        a: 'Entitlements fall back to the free tier at the end of the paid period. Your practice history, bookmarks and collections are not removed \u2014 they are the record of your preparation, and holding them hostage would be the wrong way to win you back.',
      },
    ],
  },
]

/** Flattened FAQ, for pages that render one list and for tests. */
export const ALL_FAQS = FAQ_GROUPS.flatMap((group) => group.items)

/**
 * The roadmap, in the product's own words.
 *
 * Given its own labelled section rather than being mixed into the feature grid: a
 * visitor who signs up expecting a feature that is not there was misled by the
 * marketing page, which costs more than a shorter feature list.
 */
export const ROADMAP: string[] = [
  'A generated AI assistant — quotations exist, a written explanation does not',
  'Faculty-answered doubt escalation beyond the staff panel',
  'Offline practice with background sync',
]

/** Section anchors used by the public nav, so an anchor cannot silently rot. */
export const ANCHOR = {
  how: 'how',
  calculator: 'calculator',
} as const
