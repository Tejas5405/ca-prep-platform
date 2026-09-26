/**
 * The admin console shell: 23 sections in the spec, grouped so a human can find things.
 *
 * WHAT THIS FILE IS
 *
 * The navigation, the permission gate, and the routing between sections. Each section
 * is its own lazily loaded module, because an operator who opens "Settings" should not
 * download the PDF viewer, the question bank and the payment reconciliation screens.
 *
 * THE GATE IS A COURTESY, NOT A CONTROL
 *
 * `hasRole` decides what this shell RENDERS. It protects nothing: every request the
 * sections make is refused by the API for a token without the permission, which is why
 * each section also renders the server's own answer (`GET /admin/me/permissions`) rather
 * than trusting the role in the browser. A student who types `/admin` gets the "editors
 * only" page; a student who calls `/api/v1/admin/users` with their own token gets a 403
 * and no data. Those are different things and only the second one matters.
 *
 * WHY SECTIONS ARE MARKED "PLANNED" RATHER THAN HIDDEN
 *
 * The spec lists more sections than have screens or endpoints behind them today. Hiding
 * them would make the console look complete; showing them as unavailable makes the gap
 * visible to whoever is deciding what to build next. Each one names what it needs, so
 * "not built" is a task rather than a mystery.
 */

import { hasRole, isRole } from "../../lib/roles";

export interface AdminSection {
  /** Path segment under /admin. */
  path: string;
  label: string;
  group: "Overview" | "Content" | "People" | "Money" | "Operations";
  /** The server permission this section's endpoints require. */
  permission: string | null;
  /** What the section is for, in one line, shown in the nav's tooltip and its header. */
  purpose: string;
  /** True when the section has no screen yet, and why. */
  planned?: string;
}

/**
 * The console's map.
 *
 * `permission` is the same string the API checks, copied from `ROLE_PERMISSIONS`. When
 * it is null the section is readable by any admin role. A section the caller cannot use
 * is shown DISABLED rather than removed: an editor who cannot find "Settings" assumes
 * the feature does not exist, whereas a greyed row tells them it exists and is not
 * theirs, which is a better conversation.
 */
export const ADMIN_SECTIONS: AdminSection[] = [
  {
    path: "",
    label: "Dashboard",
    group: "Overview",
    permission: null,
    purpose: "Students, content pipeline, curriculum and money at a glance.",
  },
  {
    path: "analytics",
    label: "Analytics",
    group: "Overview",
    permission: "VIEW_ANALYTICS",
    purpose: "What students actually did, by event and by day.",
  },
  {
    path: "audit",
    label: "Audit log",
    group: "Overview",
    permission: "VIEW_AUDIT",
    purpose: "Every administrative action, who took it and when.",
  },

  {
    path: "library",
    label: "Content library",
    group: "Content",
    permission: "VIEW_CONTENT",
    purpose: "Every document, its pipeline state, extracted text and preview.",
  },
  {
    path: "uploads",
    label: "Bulk upload",
    group: "Content",
    permission: "MANAGE_CONTENT",
    purpose:
      "500 PDFs in one batch, with duplicates detected before a byte moves.",
  },
  {
    path: "editorial",
    label: "Review & publish",
    group: "Content",
    permission: "MANAGE_QUESTIONS",
    purpose:
      "The extraction queue: review a draft against its page, then publish.",
  },
  {
    path: "questions",
    label: "Question bank",
    group: "Content",
    permission: "MANAGE_QUESTIONS",
    purpose: "Drafts and live questions. Publishing stays on the verifier route.",
  },
  {
    path: "tests",
    label: "Test series",
    group: "Content",
    permission: "MANAGE_TESTS",
    purpose: "Compose a draft paper. A published paper is not edited here.",
  },
  {
    path: "curriculum",
    label: "Courses & syllabus",
    group: "Content",
    permission: "MANAGE_CURRICULUM",
    purpose: "Add a course, subject, chapter or topic. Retire a row; do not delete it.",
  },

  {
    path: "access",
    label: "Access control",
    group: "People",
    permission: "MANAGE_ACCESS",
    purpose:
      "Grant or deny material by student, role, plan or course - and see why.",
  },
  {
    path: "users",
    label: "Students",
    group: "People",
    permission: "VIEW_USERS",
    purpose: "Search accounts, see progress and subscription, change a role.",
  },
  {
    path: "permissions",
    label: "Roles & permissions",
    group: "People",
    permission: null,
    purpose:
      "The role matrix the API enforces, and what your own token may do.",
  },

  {
    path: "payments",
    label: "Payments",
    group: "Money",
    permission: "VIEW_PAYMENTS",
    purpose:
      "Orders and webhook events. A paid order is not an entitlement. Refunds are not a ledger, so this screen does not record one.",
  },
  {
    path: "plans",
    label: "Plans & pricing",
    group: "Money",
    permission: "MANAGE_PLANS",
    purpose: "The prices checkout charges. Read-only: a saved copy would not change the charge.",
  },

  {
    path: "notifications",
    label: "Notifications",
    group: "Operations",
    permission: "MANAGE_NOTIFICATIONS",
    purpose:
      "Announce to a role or to every subscriber, and see what was sent.",
  },
  {
    path: "badges",
    label: "Gamification",
    group: "Operations",
    permission: "MANAGE_GAMIFICATION",
    purpose:
      "The badge catalogue, award by hand, and see which nobody has earned.",
  },
  {
    path: "settings",
    label: "Platform settings",
    group: "Operations",
    permission: "MANAGE_SETTINGS",
    purpose: "Feature flags and quotas, without a deploy. Owner-only.",
  },
  {
    path: "review",
    label: "Question review",
    group: "Content",
    permission: "MANAGE_QUESTIONS",
    purpose: "Re-verification marks a review state. It does not rewrite an answer or publish.",
  },
  {
    path: "ai",
    label: "AI configuration",
    group: "Operations",
    permission: "MANAGE_AI",
    purpose: "Whether the assistant is on. A suggestion is written only from excerpts, and labelled as not a legal authority.",
  },
  {
    path: "storage",
    label: "Storage & objects",
    group: "Operations",
    permission: "MANAGE_SETTINGS",
    purpose: "Whether the server can list a bucket. No usage number is invented.",
  },
];

/** Every distinct permission the console asks about, for the "your access" summary. */
export const ADMIN_PERMISSIONS = Array.from(
  new Set(ADMIN_SECTIONS.map((section) => section.permission).filter(Boolean)),
).sort() as string[];

export const ADMIN_GROUPS: AdminSection["group"][] = [
  "Overview",
  "Content",
  "People",
  "Money",
  "Operations",
];

export function sectionsForGroup(group: AdminSection["group"]): AdminSection[] {
  return ADMIN_SECTIONS.filter((section) => section.group === group);
}

/**
 * True when this role may open the console at all. EDITOR is the lowest door.
 *
 * Takes a `string` because the caller's role comes from the auth context, which can hold
 * a value this build has never heard of (a role added on the server first). Unknown roles
 * fail closed - treated as a student - which is the same answer the API gives them.
 */
export function canOpenConsole(role: string): boolean {
  return isRole(role) && hasRole(role, "EDITOR");
}
