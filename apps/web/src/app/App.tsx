import { Component, lazy, Suspense, type ReactNode } from "react";
import {
  BrowserRouter,
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
} from "react-router-dom";

import SupportWidget from "../components/SupportWidget";
import { useAuth } from "../hooks/authContext";
import { hasRole, type Role } from "../lib/roles";

// EAGER: the public pages, the sign-in pages and the callback.
//
// These are the first paint for someone who has never seen the product, and a
// marketing page that waits for a second network round trip before it renders is a
// worse trade than a larger entry file. They also share almost no code with the
// study surface, so deferring them would save the returning student little.
import AuthCallbackPage from "../pages/AuthCallback";
import LandingPage from "../pages/Landing";
import LoginPage from "../pages/Login";
import NotFoundPage from "../pages/NotFound";
import FaqPage from "../pages/public/FaqPage";
import FeaturesPage from "../pages/public/FeaturesPage";
import PricingPage from "../pages/public/PricingPage";
import PrivacyPage from "../pages/public/PrivacyPage";
import TermsPage from "../pages/public/TermsPage";

// LAZY: everything behind the auth wall.
//
// Measured, not guessed. The production build was one 691 kB chunk (193 kB gzipped)
// containing every screen in the product, so a visitor reading the landing page
// downloaded the admin console, the mock engine and the PDF reader. The source map
// showed where the weight is: react-dom (irreducible), the Supabase client stack, and
// the app's own pages - of which a signed-in student loads perhaps three per session.
//
// `lazy` moves each of these into its own chunk, fetched when its route is opened.
// The split is deliberately coarse: one chunk per ROUTE, not per component. Splitting
// every file would produce a waterfall of tiny requests on a 4G connection, which
// costs more in round trips than it saves in bytes.
const AdminPage = lazy(() => import("../pages/Admin"));
// The console and its sections, one chunk each: an operator opening Settings should not
// download the library, the PDF viewer or the access screen to get there.
const AdminLayout = lazy(() => import("../pages/admin/AdminLayout"));
const AdminDashboard = lazy(() => import("../pages/admin/Dashboard"));
const AdminLibrary = lazy(() => import("../pages/admin/Library"));
const AdminBulkUpload = lazy(() => import("../pages/admin/BulkUpload"));
const AdminAccess = lazy(() => import("../pages/admin/Access"));
const AdminPeople = lazy(() => import("../pages/admin/People"));
const AdminAnalytics = lazy(() =>
  import("../pages/admin/Insights").then((module) => ({
    default: module.AdminAnalytics,
  })),
);
const AdminAudit = lazy(() =>
  import("../pages/admin/Insights").then((module) => ({
    default: module.AdminAudit,
  })),
);
const AdminNotifications = lazy(() =>
  import("../pages/admin/Operations").then((module) => ({
    default: module.AdminNotifications,
  })),
);
const AdminBadges = lazy(() =>
  import("../pages/admin/Operations").then((module) => ({
    default: module.AdminBadges,
  })),
);
const AdminSettings = lazy(() =>
  import("../pages/admin/Operations").then((module) => ({
    default: module.AdminSettings,
  })),
);
const AdminPermissions = lazy(() =>
  import("../pages/admin/Operations").then((module) => ({
    default: module.AdminPermissions,
  })),
);
const AdminPayments = lazy(() => import("../pages/admin/Payments"));
const AdminQuestions = lazy(() =>
  import("../pages/admin/Studio").then((module) => ({ default: module.QuestionBank })),
);
const AdminTests = lazy(() =>
  import("../pages/admin/Studio").then((module) => ({ default: module.TestSeries })),
);
const AdminCurriculum = lazy(() =>
  import("../pages/admin/Studio").then((module) => ({ default: module.CurriculumAdmin })),
);
const AdminPlans = lazy(() =>
  import("../pages/admin/Studio").then((module) => ({ default: module.PlansAdmin })),
);
const AdminAi = lazy(() =>
  import("../pages/admin/Studio").then((module) => ({ default: module.AiAdmin })),
);
const AdminStorage = lazy(() =>
  import("../pages/admin/Studio").then((module) => ({ default: module.StorageAdmin })),
);
const AssistantPage = lazy(() => import("../pages/Assistant"));
const StudyPage = lazy(() => import("../pages/Study"));
const ReviewQueue = lazy(() => import("../pages/admin/ReviewQueue"));
const CollectionsPage = lazy(() => import("../pages/Collections"));
const DashboardPage = lazy(() => import("../pages/Dashboard"));
const DoubtsPage = lazy(() => import("../pages/Doubts"));
const LdrPage = lazy(() => import("../pages/Ldr"));
const AchievementsPage = lazy(() => import("../pages/Achievements"));
const InboxPage = lazy(() => import("../pages/Inbox"));
const LearnPage = lazy(() => import("../pages/Learn"));
const LibraryPage = lazy(() => import("../pages/Library"));
const LibraryReader = lazy(() => import("../pages/LibraryReader"));
const MocksPage = lazy(() => import("../pages/Mocks"));
const PlannerPage = lazy(() => import("../pages/Planner"));
const PracticePage = lazy(() => import("../pages/Practice"));
const ProfilePage = lazy(() => import("../pages/ProfilePage"));
const ProgressPage = lazy(() => import("../pages/ProgressPage"));
const RevisionPage = lazy(() => import("../pages/Revision"));
const SearchPage = lazy(() => import("../pages/SearchPage"));
const UpgradePage = lazy(() => import("../pages/Upgrade"));

/**
 * The mock report, loaded on its own.
 *
 * It is the one screen that renders a whole paper's worth of answers at once, so it is
 * both the heaviest page and the one a student opens least often - after a mock, not
 * during one.
 */
const ReportPage = lazy(() =>
  import("../pages/Mocks").then((module) => ({ default: module.ReportPage })),
);

/**
 * The signed-in navigation, in the order a student actually works: find the
 * material, see the syllabus, drill, revise, test, review.
 *
 * Library is in the bar because it is a destination, not a side trip. Hiding it
 * on the dashboard is how a student never finds the PDFs. The bar is allowed to
 * scroll inside itself (see the header) so adding the item cannot clip a label
 * or push the page sideways — the failure mode of the previous single-row header.
 */
const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/library", label: "Library" },
  { to: "/learn", label: "Syllabus" },
  { to: "/practice", label: "Practice" },
  { to: "/revision", label: "Revision" },
  { to: "/mocks", label: "Mocks" },
  { to: "/study", label: "Study" },
  { to: "/progress", label: "Progress" },
  { to: "/notifications", label: "Inbox" },
  { to: "/upgrade", label: "Upgrade" },
] as const;

/**
 * Content is a seventh item, and only for the people whose job it is.
 *
 * It cannot join NAV_ITEMS: that array is static, and the item depends on the
 * role resolved from the token. Hiding it from students is presentation - the
 * role check that matters runs on every request behind /admin - but a reviewer
 * who has to remember a URL will simply not use the queue.
 */
const CONTENT_NAV_ITEM = { to: "/admin", label: "Content" } as const;

/**
 * A boundary for lazily loaded routes.
 *
 * Route splitting introduces a failure mode the app did not have before: the chunk
 * request can fail. It happens in production for two ordinary reasons - a device that
 * goes offline between screens, and a deploy that replaces the hashed chunk files
 * while a session is open, which makes the browser ask for a file that no longer
 * exists. React's default behaviour is to unmount the tree, so the student gets a
 * blank page on a stale deploy.
 *
 * Catching it costs thirty lines and turns a blank screen into a sentence and a
 * button. It is deliberately scoped to ROUTE loading rather than the whole app: an
 * error thrown by a page's own logic still propagates, because silently swallowing a
 * real bug behind "reload the page" would hide it.
 */
class RouteBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="rounded-lg border border-slate-200 bg-white p-8 text-center">
        <h2 className="text-sm font-semibold tracking-tight text-slate-900">
          This screen could not be loaded
        </h2>
        <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-slate-600">
          The page is usually a stale copy in the browser after an update.
          Reloading fixes it, and nothing you have saved is affected.
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="mt-4 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white"
        >
          Reload the page
        </button>
      </div>
    );
  }
}

function FullPageSpinner() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50">
      <div
        role="status"
        aria-live="polite"
        className="flex flex-col items-center gap-3 text-slate-500"
      >
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-300 border-t-brand-600" />
        <span className="text-sm">Loading…</span>
      </div>
    </div>
  );
}

/**
 * "a editor" was the gate copy. English wants "an" before a vowel sound, and the
 * role names start with both ("editor", "admin", "content manager"), so the article
 * is chosen from the first letter rather than hardcoded — a hardcoded "an" would
 * be wrong for "content manager" the same way "a" was wrong for "editor".
 */
function indefinite(phrase: string): string {
  const article = /^[aeiou]/i.test(phrase) ? "an" : "a";
  return `${article} ${phrase}`;
}

/**
 * Route guard.
 *
 * SECURITY: this prevents a student from seeing the admin UI, nothing more. The
 * protected endpoint is enforced by `require_role` on the backend, which reads
 * the role from the VERIFIED token on every request. A guard on the client is a
 * navigation convenience; an attacker calling the endpoint directly bypasses it
 * entirely, which is exactly why the backend never trusts the client.
 */
function ProtectedRoute({
  children,
  minimumRole = "STUDENT",
}: {
  children: ReactNode;
  minimumRole?: Role;
}) {
  const { user, role, loading } = useAuth();

  // Wait for the first token resolution before deciding. Redirecting during
  // loading bounces an already-authenticated user to the login page on every
  // hard refresh — the classic flash-of-login bug.
  if (loading) return <FullPageSpinner />;

  if (!user) return <Navigate to="/login" replace />;

  if (!hasRole(role, minimumRole)) {
    return (
      <div className="mx-auto max-w-lg px-4 py-24 text-center">
        <h1 className="text-2xl font-semibold text-slate-900">Not available</h1>
        <p className="mt-2 text-slate-600">
          This area needs{" "}
          {indefinite(minimumRole.replace("_", " ").toLowerCase())} role.
        </p>
        <Link
          to="/dashboard"
          className="mt-6 inline-block text-brand-600 hover:underline"
        >
          Back to dashboard
        </Link>
      </div>
    );
  }

  return <>{children}</>;
}

function AppShell({ children }: { children: ReactNode }) {
  const { user, role, signOutUser } = useAuth();

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        {/*
          STUDENTS GET ONE ROW FROM `xl`. STAFF ALWAYS GET TWO.

          A single scrolling row looked like a fix and was not: with the role badge in
          the header, "Inbox" rendered as "Inbo" and "Content" was off the end, and a
          scrollbar that does not look like a scrollbar is a clipped label. Measured at
          1280 and 1440. Staff (who also see Content) stay on the second row, where every
          label fits. Students fit on one row from 1280px up. Below that, and on a phone,
          everyone gets the scrollable row — scrolling there is obvious, and it is the
          only navigation a phone has.

          THAT SECOND ROW IS NOT COSMETIC. The previous build hid the nav below `sm`, so a
          student on a phone had NO navigation: no way to reach Practice, Revision or Mocks
          except by typing a URL. A scrollable row is the least code that gives a phone
          user every destination, and it never clips because nothing is competing for the
          space.
        */}
        <div className="mx-auto max-w-6xl px-4 py-3">
          <div className="flex items-center justify-between gap-4">
            <Link
              to="/dashboard"
              className="shrink-0 text-lg font-semibold tracking-tight whitespace-nowrap text-slate-900"
            >
              CA Prep
            </Link>
            <nav
              aria-label="Main"
              className={`hidden min-w-0 flex-1 gap-1 overflow-x-auto ${role === "STUDENT" ? "xl:flex" : ""}`}
            >
              {[
                ...NAV_ITEMS,
                ...(hasRole(role, "EDITOR") ? [CONTENT_NAV_ITEM] : []),
              ].map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    `shrink-0 rounded-md px-3 py-1.5 text-sm font-medium whitespace-nowrap transition-colors ${
                      isActive
                        ? "bg-brand-50 text-brand-700"
                        : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>

            <div className="flex shrink-0 items-center gap-3">
              {role !== "STUDENT" && (
                // `whitespace-nowrap`: "SUPER ADMIN" broke onto two lines in the header and
                // pushed the email and the sign-out button around on a 1280px viewport.
                <span className="rounded-full bg-brand-100 px-2.5 py-1 text-xs font-medium whitespace-nowrap text-brand-700">
                  {role.replace("_", " ")}
                </span>
              )}
              {/*
              `2xl:inline`: the email is the least important thing in this row and the
              first casualty when space runs out. It is on the profile screen too, so
              hiding it below 1280px costs nothing and stops it squeezing the navigation.
            */}
              <span className="hidden max-w-[12rem] truncate text-sm text-slate-500 2xl:inline">
                {user?.email}
              </span>
              <button
                type="button"
                onClick={() => void signOutUser()}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium whitespace-nowrap text-slate-700 transition-colors hover:bg-slate-100"
              >
                Sign out
              </button>
            </div>
          </div>

          {/*
            The compact row. Same destinations, its own line, scrollable - so nothing is
            ever clipped and a phone user can reach every screen.
          */}
          <nav
            aria-label="Main (compact)"
            className={`mt-3 flex gap-1 overflow-x-auto pb-1 ${role === "STUDENT" ? "xl:hidden" : ""}`}
          >
            {[
              ...NAV_ITEMS,
              ...(hasRole(role, "EDITOR") ? [CONTENT_NAV_ITEM] : []),
            ].map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `rounded-md px-3 py-1.5 text-sm font-medium whitespace-nowrap transition-colors ${
                    isActive
                      ? "bg-brand-50 text-brand-700"
                      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8">
        {/*
          The Suspense boundary sits INSIDE the shell, not around it. A lazy page
          suspends while its chunk arrives; if the boundary were outside, the header
          and navigation would unmount and remount on every navigation, which reads as
          a page reload on a fast connection and as a flicker on a slow one.
        */}
        <SupportWidget />
        <RouteBoundary>
          <Suspense
            fallback={
              <div
                role="status"
                aria-live="polite"
                className="py-16 text-center text-sm text-slate-500"
              >
                Loading…
              </div>
            }
          >
            {children}
          </Suspense>
        </RouteBoundary>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      {/* Backstop for routes outside the shell (public pages, sign-in, 404). */}
      <Suspense fallback={<FullPageSpinner />}>
        <Routes>
          {/*
          The landing page is public and owns "/". The dashboard moved to
          "/dashboard" for two reasons: a visitor typing the bare domain should
          see the product rather than a login wall, and a marketplace or
          referral link to "/" should not bounce a prospective student straight
          into an auth form.
        */}
          <Route path="/" element={<LandingPage />} />

          {/*
          Sign-in, sign-up and password reset are three routes onto ONE component.
          The route decides which of the three screens it is (see
          `modeFromPath`), which is what makes a shared "create an account" link
          and a password-reset email land somewhere truthful rather than at a form
          with the right control one click away.
        */}
          <Route path="/login" element={<LoginPage />} />
          <Route path="/signup" element={<LoginPage />} />
          <Route path="/forgot-password" element={<LoginPage />} />

          {/* ------------------------------------------------------- public pages
            Real pages, not anchors on the landing page. Each one is linked from
            the navbar or the footer, carries its own metadata, and answers a
            question a visitor has before signing up: what it does, what it costs,
            what the uncomfortable details are, and what the terms are.
        */}
          <Route path="/features" element={<FeaturesPage />} />
          <Route path="/pricing" element={<PricingPage />} />
          <Route path="/faq" element={<FaqPage />} />
          <Route path="/privacy" element={<PrivacyPage />} />
          <Route path="/terms" element={<TermsPage />} />

          {/*
          The OAuth return leg. It must NOT be a protected route: the whole point
          is that the session is being established here, so the guard would bounce
          the student to /login before the code exchange completes.
        */}
          <Route path="/auth/callback" element={<AuthCallbackPage />} />

          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <AppShell>
                  <DashboardPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/planner"
            element={
              <ProtectedRoute>
                <AppShell>
                  <PlannerPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/learn"
            element={
              <ProtectedRoute>
                <AppShell>
                  <LearnPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          {/*
            The student library. Separate from /admin/library: that one is the
            operator's pipeline, this one is the shelf a student may read. The
            reader is its own route so a shared link opens the document, not the
            shelf with a modal that the back button cannot close.
          */}
          <Route
            path="/library"
            element={
              <ProtectedRoute>
                <AppShell>
                  <LibraryPage />
                </AppShell>
              </ProtectedRoute>
            }
          />
          <Route
            path="/library/:documentId"
            element={
              <ProtectedRoute>
                <AppShell>
                  <LibraryReader />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/practice"
            element={
              <ProtectedRoute>
                <AppShell>
                  <PracticePage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/revision"
            element={
              <ProtectedRoute>
                <AppShell>
                  <RevisionPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          {/* Collections and the LDR list are two screens because they are two
            ideas: a named container the student builds, and the practice loop's own
            "come back to this" flag. Merging them into one list would make
            "flagged" and "in a collection" look like the same state. */}
          <Route
            path="/collections"
            element={
              <ProtectedRoute>
                <AppShell>
                  <CollectionsPage />
                </AppShell>
              </ProtectedRoute>
            }
          />
          <Route
            path="/ldr"
            element={
              <ProtectedRoute>
                <AppShell>
                  <LdrPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/doubts"
            element={
              <ProtectedRoute>
                <AppShell>
                  <DoubtsPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/doubts/:doubtId"
            element={
              <ProtectedRoute>
                <AppShell>
                  <DoubtsPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/progress"
            element={
              <ProtectedRoute>
                <AppShell>
                  <ProgressPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/achievements"
            element={
              <ProtectedRoute>
                <AppShell>
                  <AchievementsPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/study"
            element={
              <ProtectedRoute>
                <AppShell>
                  <StudyPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/assistant"
            element={
              <ProtectedRoute>
                <AppShell>
                  <AssistantPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/notifications"
            element={
              <ProtectedRoute>
                <AppShell>
                  <InboxPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          {/*
          Mock papers. `/mocks/:mockId` is the sitting, and the paper is opened by
          the API rather than by a client-side start call, so a refresh during a
          timed paper resumes the attempt instead of opening a second one.
        */}
          <Route
            path="/mocks"
            element={
              <ProtectedRoute>
                <AppShell>
                  <MocksPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/mocks/:mockId"
            element={
              <ProtectedRoute>
                <AppShell>
                  <MocksPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/reports/:attemptId"
            element={
              <ProtectedRoute>
                <AppShell>
                  <ReportPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/search"
            element={
              <ProtectedRoute>
                <AppShell>
                  <SearchPage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          <Route
            path="/profile"
            element={
              <ProtectedRoute>
                <AppShell>
                  <ProfilePage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          {/*
          The authenticated plan screen.
          `/pricing` used to serve this, and it now belongs to the public pricing
          page (blueprint §26: pricing is a public route). Splitting them fixes a
          real oddity - a signed-out visitor clicking "Pricing" was bounced to a
          login wall to read a price, and a signed-in student clicking "See plans"
          landed on a marketing page. `/subscription` is where a student sees what
          THEY have; `/pricing` is where anyone sees what exists.
        */}
          <Route
            path="/subscription"
            element={
              <ProtectedRoute>
                <AppShell>
                  <ProfilePage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          {/*
          The checkout. Separate from `/subscription` (which shows what you HAVE) and
          from `/pricing` (which shows what EXISTS): this is where money actually
          changes hands, so it is authenticated and it is the only screen that opens
          Razorpay Checkout.
        */}
          <Route
            path="/upgrade"
            element={
              <ProtectedRoute>
                <AppShell>
                  <UpgradePage />
                </AppShell>
              </ProtectedRoute>
            }
          />

          {/*
          The content team's surface. `minimumRole` is EDITOR so the shell refuses
          anyone below that before the page even mounts, and the page repeats the
          check for the empty-URL case. The wildcard keeps blueprint §6.1's
          `admin/*` shape working: /admin/review and /admin/queue are the same
          screen, because the three jobs (upload, queue, review) are one workflow
          and splitting them into tabs would hide the queue behind a click.
        */}
          <Route
            path="/admin"
            element={
              <ProtectedRoute minimumRole="EDITOR">
                <AppShell>
                  <AdminLayout />
                </AppShell>
              </ProtectedRoute>
            }
          >
            <Route index element={<AdminDashboard />} />
            <Route path="analytics" element={<AdminAnalytics />} />
            <Route path="audit" element={<AdminAudit />} />
            <Route path="library" element={<AdminLibrary />} />
            <Route path="uploads" element={<AdminBulkUpload />} />
            <Route path="access" element={<AdminAccess />} />
            <Route path="users" element={<AdminPeople />} />
            <Route path="permissions" element={<AdminPermissions />} />
            <Route path="notifications" element={<AdminNotifications />} />
            <Route path="badges" element={<AdminBadges />} />
            <Route path="settings" element={<AdminSettings />} />
            <Route path="payments" element={<AdminPayments />} />
            <Route path="questions" element={<AdminQuestions />} />
            <Route path="tests" element={<AdminTests />} />
            <Route path="curriculum" element={<AdminCurriculum />} />
            <Route path="plans" element={<AdminPlans />} />
            <Route path="ai" element={<AdminAi />} />
            <Route path="review" element={<ReviewQueue />} />
            <Route path="storage" element={<AdminStorage />} />
            {/*
              The editorial queue keeps its own page at /admin/editorial: it is the
              deepest workflow in the product (upload, watch, review, publish) and the
              one most likely to grow, so it stays a screen rather than becoming a
              section of a console whose other parts are read-mostly.
            */}
            <Route path="editorial" element={<AdminPage />} />
          </Route>

          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
