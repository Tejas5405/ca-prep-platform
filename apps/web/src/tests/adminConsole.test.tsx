/**
 * The admin console.
 *
 * WHAT THESE TESTS ARE GUARDING
 *
 * The console is a set of screens over an API that already existed, so the failures
 * worth catching are the ones that make an operator believe something happened when it
 * did not: a grant form that posts the wrong audience, a bulk upload that queues a file
 * it never sent, a retry that re-uploads the 497 files that succeeded to fix the 3 that
 * did not, a sidebar that offers a section the server will refuse. Each test below
 * asserts the REQUEST, not the rendering, for that reason - the rendering is the easy
 * half and the request is the half that decides whether the data changes.
 *
 * The signed-URLs PUT is stubbed with a fake XMLHttpRequest (`./support/signedUpload`)
 * because `uploadToSignedUrl` deliberately does not use fetch.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthContext } from "../hooks/authContext";
import type { Role } from "../lib/roles";
import AdminLayout from "../pages/admin/AdminLayout";
import AdminDashboard from "../pages/admin/Dashboard";
import AdminLibrary from "../pages/admin/Library";
import AdminAccess from "../pages/admin/Access";
import AdminPayments from "../pages/admin/Payments";
import { PlansAdmin, StorageAdmin } from "../pages/admin/Studio";
import AssistantPage from "../pages/Assistant";
import BulkUpload from "../pages/admin/BulkUpload";
import { AdminSettings } from "../pages/admin/Operations";
import { makeAuth } from "./authStub";
import { stubSignedUploads } from "./support/signedUpload";
import { installBrowserCrypto } from "./support/webcrypto";

// ---------------------------------------------------------------------------- stubs

function ok(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const envelope = <T,>(data: T) => ({ data, meta: { requestId: "test" } });
const list = <T,>(data: T[], extra: Record<string, unknown> = {}) => ({
  data,
  meta: {
    requestId: "test",
    total: data.length,
    page: 1,
    limit: 25,
    hasMore: false,
    ...extra,
  },
});

interface Call {
  url: string;
  method: string;
  body: unknown;
}

/** Route by URL substring and record every request. An unrouted URL is a 404 body. */
function stubApi(
  routes: [string, unknown][],
  options: { status?: number } = {},
) {
  const calls: Call[] = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    let body: unknown = null;
    if (typeof init?.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    calls.push({ url, method, body });
    if (options.status && options.status >= 400) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            title: "refused",
            status: options.status,
            detail: "no",
          }),
          {
            status: options.status,
            headers: { "Content-Type": "application/json" },
          },
        ),
      );
    }
    for (const [needle, response] of routes) {
      if (url.includes(needle)) return Promise.resolve(ok(response));
    }
    return Promise.resolve(
      new Response(
        JSON.stringify({ title: `no stub for ${url}`, status: 404 }),
        { status: 404 },
      ),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    calls,
    urlCalls: (needle: string) =>
      calls.filter((call) => call.url.includes(needle)),
    last: (needle: string) =>
      calls.filter((call) => call.url.includes(needle)).at(-1),
  };
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderAs(role: Role, element: React.ReactNode, route = "/admin") {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <AuthContext.Provider value={makeAuth({ role })}>
        {element}
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------- fixtures

const COUNTS = {
  students: { total: 128, active: 91, newThisWeek: 12 },
  content: {
    documents: 43,
    indexed: 40,
    processing: 1,
    failed: 2,
    pages: 5120,
    extractedChars: 9_500_000,
    storageBytes: 734_003_200,
  },
  curriculum: {
    courses: 3,
    subjects: 9,
    chapters: 61,
    questions: 4100,
    publishedQuestions: 3950,
    mockTests: 14,
  },
  activity: {
    mockAttempts: 620,
    completedAttempts: 590,
    notificationsSent: 22,
    aiEvents: 0,
  },
  money: { revenueRupees: 418_120, successfulPayments: 210 },
};

const DOCUMENT = {
  id: "doc-1",
  title: "May 2026 R2 Advanced Accounting",
  filename: "may-2026-r2.pdf",
  kind: "PAST_PAPER",
  accessTier: "PREMIUM",
  status: "INDEXED",
  pageCount: 24,
  extractedChars: 55_000,
  archivedAt: null,
  courseId: null,
  subjectId: null,
  chapterId: null,
  uploadedAt: "2026-09-01T10:00:00Z",
};

const GRANT = {
  id: "grant-1",
  whoScope: "USER",
  effect: "ALLOW",
  userId: "11111111-1111-1111-1111-111111111111",
  role: null,
  tier: null,
  planCode: null,
  isLive: true,
  revokedAt: null,
  createdAt: "2026-09-20T10:00:00Z",
  expiresAt: null,
  reason: "Scholarship cohort",
  covers: {
    wholeLibrary: false,
    courseId: null,
    subjectId: null,
    kind: "PAST_PAPER",
  },
};

const EDITOR_PERMISSIONS = envelope({
  role: "EDITOR",
  permissions: ["VIEW_CONTENT", "MANAGE_CONTENT", "MANAGE_QUESTIONS"],
  isOwner: false,
});

// ---------------------------------------------------------------------------- the gate

describe("the console gate", () => {
  it("shows a student a refusal and never asks the server what they may do", async () => {
    const api = stubApi([["/admin/me/permissions", EDITOR_PERMISSIONS]]);

    renderAs("STUDENT", <AdminLayout />);

    expect(await screen.findByText(/editors only/i)).toBeInTheDocument();
    // The important half: no request. A refusal that still calls the admin API would
    // leak the shape of the console even though nothing renders.
    expect(api.calls).toHaveLength(0);
  });

  it("tells an editor which sections their role cannot open, using the server’s answer", async () => {
    stubApi([["/admin/me/permissions", EDITOR_PERMISSIONS]]);

    renderAs("EDITOR", <AdminLayout />, "/admin");

    const nav = await screen.findByRole("navigation", {
      name: /admin sections/i,
    });

    // MANAGE_SETTINGS is not in this token's permission list, so Settings must not be a
    // link. The row stays visible (greyed) because "it exists, it is not yours" is a
    // better answer than an absent menu item.
    //
    // Re-queried INSIDE the wait, not captured first: until the permission list arrives
    // the row renders as an enabled `<a>`, and React replaces that node with a `<span>`
    // when the answer lands. A reference taken on the first paint stays pointed at the
    // detached anchor - it would log as disabled and assert as null.
    await waitFor(() =>
      expect(within(nav).getByText("Platform settings")).toHaveAttribute(
        "aria-disabled",
        "true",
      ),
    );
    expect(within(nav).getByText("Platform settings").closest("a")).toBeNull();

    // ...and a section it does hold is a real link.
    expect(
      within(nav).getByRole("link", { name: /content library/i }),
    ).toHaveAttribute("href", "/admin/library");
  });

  it("links the question bank now that a screen exists", async () => {
    stubApi([
      [
        "/admin/me/permissions",
        envelope({
          role: "ADMIN",
          permissions: ["MANAGE_QUESTIONS"],
          isOwner: false,
        }),
      ],
    ]);

    renderAs("ADMIN", <AdminLayout />);

    const nav = await screen.findByRole("navigation", {
      name: /admin sections/i,
    });
    await waitFor(() =>
      expect(
        within(nav).getByRole("link", { name: "Question bank" }),
      ).toHaveAttribute("href", "/admin/questions"),
    );
  });
});

// ---------------------------------------------------------------------------- dashboard

describe("the dashboard", () => {
  it("leads with the failures, because that is what needs a person", async () => {
    stubApi([["/admin/dashboard", envelope(COUNTS)]]);

    renderAs("ADMIN", <AdminDashboard />);

    expect(
      await screen.findByText(/2 documents failed processing/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /open the library/i }),
    ).toHaveAttribute("href", "/admin/library?status=FAILED");
    expect(screen.getByText("128")).toBeInTheDocument();
  });

  it("says nothing about failures when the pipeline is clean", async () => {
    stubApi([
      [
        "/admin/dashboard",
        envelope({ ...COUNTS, content: { ...COUNTS.content, failed: 0 } }),
      ],
    ]);

    renderAs("ADMIN", <AdminDashboard />);

    await screen.findByText("128");
    expect(screen.queryByText(/failed processing/i)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------- library

describe("the content library", () => {
  it("reads the filter out of the URL, so a link to a status is a real link", async () => {
    const api = stubApi([["/admin/content/documents", list([DOCUMENT])]]);

    renderAs("EDITOR", <AdminLibrary />, "/admin/library?status=FAILED");

    expect(
      await screen.findByText(/may 2026 r2 advanced accounting/i),
    ).toBeInTheDocument();
    expect(api.last("/admin/content/documents")?.url).toContain(
      "status=FAILED",
    );
  });

  it("says a result was found inside the text, and on which page", async () => {
    // The server searches the extracted PAGE TEXT, so a row whose title does not
    // contain the words typed into the box is a correct result. Without the badge the
    // operator reads it as a broken filter - which is what the search label used to
    // promise ("by title or filename") before it stopped being true.
    const api = stubApi([
      [
        "/admin/content/documents",
        list([{ ...DOCUMENT, matchSource: "TEXT", matchedPage: 12 }]),
      ],
    ]);

    renderAs("EDITOR", <AdminLibrary />, "/admin/library?q=deferred%20tax");

    const row = (
      await screen.findByText(/may 2026 r2 advanced accounting/i)
    ).closest("li");
    expect(
      within(row as HTMLElement).getByText(/found in text · page 12/i),
    ).toBeInTheDocument();
    expect(api.last("/admin/content/documents")?.url).toContain("q=deferred");
    // The label tells the truth about what is being searched.
    expect(
      screen.getByText(
        /search titles, filenames and the text inside the documents/i,
      ),
    ).toBeInTheDocument();
  });

  it("does not claim a text match when the row matched on metadata", async () => {
    const api = stubApi([
      [
        "/admin/content/documents",
        list([{ ...DOCUMENT, matchSource: "METADATA", matchedPage: null }]),
      ],
    ]);

    renderAs("EDITOR", <AdminLibrary />, "/admin/library?q=advanced");
    await screen.findByText(/may 2026 r2 advanced accounting/i);

    expect(screen.queryByText(/found in text/i)).not.toBeInTheDocument();
    expect(api.last("/admin/content/documents")?.url).toContain("q=advanced");
  });

  it("archives and then re-reads the list rather than editing its own copy", async () => {
    const user = userEvent.setup();
    const api = stubApi([
      ["/admin/content/documents", list([DOCUMENT])],
      [
        "/admin/content/documents/doc-1",
        envelope({ documentId: "doc-1", archived: true }),
      ],
    ]);

    renderAs("EDITOR", <AdminLibrary />);

    const row = (
      await screen.findByText(/may 2026 r2 advanced accounting/i)
    ).closest("li");
    await user.click(
      within(row as HTMLElement).getByRole("button", { name: /archive/i }),
    );

    await waitFor(() => {
      expect(api.last("/admin/content/documents/doc-1")?.method).toBe("DELETE");
    });
    // Two list reads: the first paint and the one after the write. The screen never
    // guesses what the server did to the row.
    await waitFor(() =>
      expect(api.urlCalls("/admin/content/documents?").length).toBe(2),
    );
  });
});

// ---------------------------------------------------------------------------- bulk upload

const TICKETS = {
  batchId: "batch-1",
  acceptedCount: 1,
  skippedCount: 1,
  accepted: [
    {
      filename: "new-paper.pdf",
      documentId: "doc-new",
      uploadUrl:
        "https://storage.test/object/upload/sign/source-pdfs/new-paper.pdf?token=t",
      token: "t",
      expiresInSeconds: 120,
    },
  ],
  skipped: [{ filename: "already-here.pdf", reason: "DUPLICATE" }],
};

describe("bulk upload", () => {
  // jsdom cannot hand Node's crypto a buffer it accepts (see the helper). Installed for
  // these tests only, so the fingerprinting path is really exercised.
  beforeEach(() => installBrowserCrypto());

  const files = [
    new File(["%PDF-1.4 first"], "new-paper.pdf", { type: "application/pdf" }),
    new File(["%PDF-1.4 second"], "already-here.pdf", {
      type: "application/pdf",
    }),
  ];

  it("fingerprints every file, lets the server skip the duplicate, and puts only the rest", async () => {
    const user = userEvent.setup();
    const storage = stubSignedUploads();
    const api = stubApi([
      ["/courses", list([{ id: "course-1", name: "CA Intermediate" }])],
      ["/admin/content/uploads", envelope(TICKETS)],
      [
        "/admin/content/documents/doc-new/start",
        envelope({
          documentId: "doc-new",
          jobId: "job-1",
          status: "QUEUED",
          enqueued: true,
        }),
      ],
      [
        "/admin/content/batches/batch-1",
        envelope({
          batchId: "batch-1",
          indexed: 1,
          processing: 0,
          failed: 0,
          documents: [
            { id: "doc-new", title: "new-paper.pdf", status: "QUEUED" },
          ],
        }),
      ],
    ]);

    const { container } = renderAs("EDITOR", <BulkUpload />);

    const input = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    await user.upload(input, files);
    await user.click(screen.getByRole("button", { name: /^upload/i }));

    // 1. The manifest carries a sha256 per file, computed in the browser - that is what
    //    makes "already in the library" a server answer before any bytes move.
    await waitFor(() =>
      expect(api.last("/admin/content/uploads")).toBeDefined(),
    );
    const manifest = api.last("/admin/content/uploads")?.body as {
      files: {
        filename: string;
        checksum_sha256: string;
        size_bytes: number;
      }[];
    };
    // Known answers, computed outside this suite (`printf '%PDF-1.4 first' | sha256sum`).
    // A format check (64 hex characters) would also pass for a digest of the wrong buffer,
    // or of an empty one - and the SERVER compares this value against the object it
    // receives, so a wrong-but-well-formed digest is a broken upload, not a cosmetic bug.
    const KNOWN = new Map([
      [
        "new-paper.pdf",
        "3348a305b313dea86722bf9db00704a77410139d47402e0b841df20fa8e032f5",
      ],
      [
        "already-here.pdf",
        "01795e291900913555a8a738f39cef73dc8e83274d032d57b3c983608f4a8bb4",
      ],
    ]);
    const contents = new Map(files.map((file) => [file.name, file]));
    expect(manifest.files.map((entry) => entry.filename).sort()).toEqual([
      "already-here.pdf",
      "new-paper.pdf",
    ]);
    for (const entry of manifest.files) {
      expect(entry.checksum_sha256).toBe(KNOWN.get(entry.filename));
      expect(entry.size_bytes).toBe(contents.get(entry.filename)?.size);
    }
    // Different bytes, different fingerprints: the dedupe key is the content, not the name.
    expect(
      new Set(manifest.files.map((entry) => entry.checksum_sha256)).size,
    ).toBe(2);

    // 2. Only the accepted file is transferred, and only to storage.
    await waitFor(() => expect(storage.uploads).toHaveLength(1));
    expect(storage.uploads[0]?.url).toContain("new-paper.pdf");

    // 3. The duplicate is reported as one, and the uploaded file is confirmed to the API
    //    (which is what queues extraction - an unconfirmed upload sits in UPLOADED).
    expect(
      await screen.findByText(/already in the library, identical checksum/i),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        api.urlCalls("/admin/content/documents/doc-new/start"),
      ).toHaveLength(1),
    );
    expect(
      await screen.findByText(/queued for extraction/i),
    ).toBeInTheDocument();
  });

  it("retries only the failed file, not the batch", async () => {
    const user = userEvent.setup();
    const storage = stubSignedUploads({
      status: (url) => (url.includes("bad.pdf") ? 500 : 200),
    });
    stubApi([
      ["/courses", list([])],
      [
        "/admin/content/uploads",
        envelope({
          batchId: "batch-2",
          acceptedCount: 2,
          skippedCount: 0,
          accepted: [
            {
              filename: "good.pdf",
              documentId: "doc-good",
              uploadUrl: "https://storage.test/good.pdf",
              token: "t",
            },
            {
              filename: "bad.pdf",
              documentId: "doc-bad",
              uploadUrl: "https://storage.test/bad.pdf",
              token: "t",
            },
          ],
          skipped: [],
        }),
      ],
      [
        "/admin/content/documents/doc-good/start",
        envelope({
          documentId: "doc-good",
          jobId: "j",
          status: "QUEUED",
          enqueued: true,
        }),
      ],
      [
        "/admin/content/documents/doc-bad/start",
        envelope({
          documentId: "doc-bad",
          jobId: "j",
          status: "QUEUED",
          enqueued: true,
        }),
      ],
      [
        "/admin/content/batches/batch-2",
        envelope({
          batchId: "batch-2",
          indexed: 0,
          processing: 1,
          failed: 1,
          documents: [],
        }),
      ],
    ]);

    const { container } = renderAs("EDITOR", <BulkUpload />);
    const input = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    await user.upload(input, [
      new File(["a"], "good.pdf", { type: "application/pdf" }),
      new File(["b"], "bad.pdf", { type: "application/pdf" }),
    ]);
    await user.click(screen.getByRole("button", { name: /^upload/i }));

    const retry = await screen.findByRole("button", {
      name: /retry 1 failed/i,
    });
    expect(storage.attempts("https://storage.test/good.pdf")).toBe(1);

    await user.click(retry);
    await user.click(screen.getByRole("button", { name: /^upload/i }));

    await waitFor(() =>
      expect(storage.attempts("https://storage.test/bad.pdf")).toBe(2),
    );
    // The point of the whole feature: the good file was not sent twice. A 500-file batch
    // with 3 failures must cost 3 re-uploads.
    expect(storage.attempts("https://storage.test/good.pdf")).toBe(1);
  });

  it("says why it cannot start when the page is not a secure context", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("crypto", { getRandomValues: (array: Uint8Array) => array });
    stubApi([["/courses", list([])]]);
    const { container } = renderAs("EDITOR", <BulkUpload />);

    const input = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    await user.upload(
      input,
      new File(["%PDF-1.4"], "paper.pdf", { type: "application/pdf" }),
    );
    await user.click(screen.getByRole("button", { name: /^upload/i }));

    // A page served over plain HTTP has no crypto.subtle. The honest answer names the
    // origin, not the file - otherwise 500 rows read "Could not read the file" and the
    // operator starts re-saving PDFs that were never the problem.
    expect(
      await screen.findByText(/must be served over https/i),
    ).toBeInTheDocument();
  });

  it("refuses a file over the storage limit before reading it", async () => {
    const user = userEvent.setup();
    stubApi([["/courses", list([])]]);
    const { container } = renderAs("EDITOR", <BulkUpload />);

    const huge = new File(["%PDF"], "huge.pdf", { type: "application/pdf" });
    Object.defineProperty(huge, "size", { value: 60 * 1024 * 1024 });
    const input = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    await user.upload(input, huge);

    expect(await screen.findByText(/larger than 50 mb/i)).toBeInTheDocument();
    // Nothing to upload means the button stays disabled: no half-empty batch is sent.
    expect(screen.getByRole("button", { name: /^upload$/i })).toBeDisabled();
  });
});

// ---------------------------------------------------------------------------- access control

describe("access control", () => {
  it("creates a role grant and re-reads the list from the server", async () => {
    const user = userEvent.setup();
    const api = stubApi([
      ["/admin/access/grants?", list([GRANT])],
      ["/admin/access/grants", envelope({ ...GRANT, id: "grant-2" })],
    ]);

    renderAs("ADMIN", <AdminAccess />, "/admin/access");

    await screen.findByText(/scholarship cohort/i);

    await user.selectOptions(screen.getByLabelText(/^who$/i), "ROLE");
    await user.selectOptions(screen.getByLabelText(/role/i), "STUDENT");
    await user.click(screen.getByRole("button", { name: /grant access/i }));

    const posted = api.calls.find((call) => call.method === "POST");
    expect(posted?.url).toContain("/admin/access/grants");
    expect(posted?.body).toMatchObject({
      who_scope: "ROLE",
      effect: "ALLOW",
      role: "STUDENT",
      // The audience fields the server did not get must be explicitly null rather than
      // absent: the API's CHECK is "exactly one audience", and an omitted key is not one.
      user_id: null,
      tier: null,
      plan_code: null,
    });
    await waitFor(() =>
      expect(api.urlCalls("/admin/access/grants?").length).toBe(2),
    );
  });

  it("sends a denial as a denial, and says what a denial outranks", async () => {
    const user = userEvent.setup();
    const api = stubApi([
      ["/admin/access/grants?", list([])],
      ["/admin/access/grants", envelope({ ...GRANT, effect: "DENY" })],
    ]);

    renderAs("ADMIN", <AdminAccess />);

    await user.selectOptions(await screen.findByLabelText(/^who$/i), "TIER");
    await user.selectOptions(screen.getByLabelText(/effect/i), "DENY");
    expect(await screen.findByText(/outranks any allow/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /deny access/i }));

    expect(
      api.calls.find((call) => call.method === "POST")?.body,
    ).toMatchObject({
      who_scope: "TIER",
      effect: "DENY",
    });
  });

  it("revokes without deleting the row, and keeps showing it as revoked", async () => {
    const user = userEvent.setup();
    const api = stubApi([
      ["/admin/access/grants?", list([GRANT])],
      [
        "/admin/access/grants/grant-1",
        envelope({
          ...GRANT,
          isLive: false,
          revokedAt: "2026-09-26T09:00:00Z",
        }),
      ],
    ]);

    renderAs("ADMIN", <AdminAccess />);

    await user.click(await screen.findByRole("button", { name: /revoke/i }));

    await waitFor(() =>
      expect(api.urlCalls("/admin/access/grants/grant-1")[0]?.method).toBe(
        "DELETE",
      ),
    );
    // The history is the point: "who could read this in August" must stay answerable.
    await waitFor(() =>
      expect(api.urlCalls("/admin/access/grants?").length).toBe(2),
    );
  });

  it("explains a decision by asking the server to run the student’s own check", async () => {
    const user = userEvent.setup();
    const api = stubApi([
      ["/admin/access/grants?", list([])],
      [
        "/admin/access/explain",
        envelope({
          userId: GRANT.userId,
          documentId: "doc-1",
          documentTitle: "May 2026 R2",
          documentTier: "PREMIUM",
          documentStatus: "INDEXED",
          isPublished: true,
          viewerTier: "FREE",
          decision: "NEEDS_UPGRADE",
          canRead: false,
          reason: "Free tier does not include PREMIUM material.",
        }),
      ],
      [
        "/admin/access/users/",
        envelope({
          userId: GRANT.userId,
          email: "a@b.test",
          displayName: "A",
          role: "STUDENT",
          tier: "FREE",
          subscriptionStatus: "NONE",
          courseIds: [],
          allowGrants: 0,
          denyGrants: 0,
        }),
      ],
    ]);

    renderAs("ADMIN", <AdminAccess />);

    await user.type(await screen.findByLabelText(/student id/i), GRANT.userId);
    await user.type(screen.getByLabelText(/document id/i), "doc-1");
    await user.click(screen.getByRole("button", { name: /explain/i }));

    expect(
      await screen.findByText(/does not include premium/i),
    ).toBeInTheDocument();
    expect(
      api.calls.some(
        (call) =>
          call.url.includes("user_id=") && call.url.includes("document_id="),
      ),
    ).toBe(true);
  });
});

// ---------------------------------------------------------------------------- settings

describe("platform settings", () => {
  const SETTINGS = envelope({
    settings: [
      // `value` is the platform_settings JSONB column verbatim: {"value": N}. The screen
      // unwraps it, so a bare 30 here would render an empty box.
      {
        key: "free_daily_question_limit",
        value: { value: 30 },
        description: "Free questions per day",
        updatedAt: null,
      },
      {
        key: "ai_assistant_enabled",
        value: { value: false },
        description: "AI assistant",
        updatedAt: null,
      },
    ],
  });

  it("writes one setting at a time and reports when it was accepted", async () => {
    const user = userEvent.setup();
    const api = stubApi([
      ["/admin/settings", SETTINGS],
      [
        "/admin/settings",
        envelope({
          key: "free_daily_question_limit",
          value: 40,
          updatedAt: "2026-09-26T09:00:00Z",
        }),
      ],
    ]);

    renderAs("ADMIN", <AdminSettings />);

    // Numbers are edited in place and written when the field loses focus - there is no
    // Save button, because a settings row is its own form.
    const field = await screen.findByDisplayValue("30");
    await user.clear(field);
    await user.type(field, "40");
    await user.tab();

    await waitFor(() => {
      const put = api.calls.find((call) => call.method === "PUT");
      expect(put?.body).toEqual({
        key: "free_daily_question_limit",
        value: { value: 40 },
      });
    });
    // Re-read after the write, so the field shows what the server stored and not what
    // was typed. Saved-ness is asserted separately because a PUT can be accepted and a
    // read can then fail.
    expect(await screen.findByText(/saved/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------- payments

const ORDER = {
  id: "order-1",
  email: "payee@example.com",
  displayName: "Payee",
  userId: "user-1",
  planCode: "PREMIUM_PLUS",
  tier: "PREMIUM_PLUS",
  amountPaise: 129_900,
  amountRupees: 1299,
  amountRemainderPaise: 0,
  currency: "INR",
  status: "PAID",
  provider: "razorpay",
  providerOrderId: "order_test",
  providerPaymentId: "pay_test",
  providerStatus: "captured",
  receipt: "rcpt_1",
  paidAt: "2026-09-26T09:00:00Z",
  failureReason: null,
  createdAt: "2026-09-26T08:00:00Z",
  entitlement: null,
  payload: { card: "SHOULD_NOT_LEAK_rzp_test_secret" },
};

const EVENT = {
  id: "event-1",
  provider: "razorpay",
  eventId: "evt_test",
  eventType: "payment.captured",
  signatureVerified: true,
  processedAt: null,
  processingError: "entitlement was not written",
  userId: "user-1",
  email: "payee@example.com",
  createdAt: "2026-09-26T09:01:00Z",
  payload: { card: "SHOULD_NOT_LEAK_rzp_test_secret" },
};

describe("payments", () => {
  it("links the section once a screen exists, instead of greying it as soon", async () => {
    stubApi([
      [
        "/admin/me/permissions",
        envelope({
          role: "ADMIN",
          permissions: ["VIEW_PAYMENTS"],
          isOwner: false,
        }),
      ],
    ]);

    renderAs("ADMIN", <AdminLayout />);

    const nav = await screen.findByRole("navigation", {
      name: /admin sections/i,
    });
    await waitFor(() =>
      expect(
        within(nav).getByRole("link", { name: "Payments" }),
      ).toHaveAttribute("href", "/admin/payments"),
    );
  });

  it("shows the captured amount from the server and does not offer a refund", async () => {
    stubApi([
      [
        "/admin/payments/orders",
        list([ORDER], {
          paidOrders: 1,
          paidPaise: 129_900,
          paidRupees: 1299,
          paidRemainderPaise: 0,
          byStatus: { PAID: 1 },
        }),
      ],
    ]);

    renderAs("ADMIN", <AdminPayments />, "/admin/payments");

    expect((await screen.findAllByText(/₹1,?299/)).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/paid, no active entitlement/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/RAZORPAY_KEY_ID/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /refund/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/SHOULD_NOT_LEAK/)).not.toBeInTheDocument();
  });

  it("lists webhook events without the raw body", async () => {
    const user = userEvent.setup();
    stubApi([
      [
        "/admin/payments/orders",
        list([], {
          paidOrders: 0,
          paidPaise: 0,
          paidRupees: 0,
          paidRemainderPaise: 0,
          byStatus: {},
        }),
      ],
      [
        "/admin/payments/events",
        list([EVENT], { payloadOmitted: true, unprocessed: 1 }),
      ],
    ]);

    renderAs("ADMIN", <AdminPayments />, "/admin/payments");
    await user.click(
      await screen.findByRole("button", { name: /webhook events/i }),
    );

    expect(await screen.findByText("payment.captured")).toBeInTheDocument();
    expect(
      screen.getByText(/entitlement was not written/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/raw webhook body is not shown/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/SHOULD_NOT_LEAK/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /refund/i }),
    ).not.toBeInTheDocument();
  });
});

describe("the sections that must not pretend", () => {
  it("saves a price and says checkout will charge it", async () => {
    stubApi([
      [
        "/admin/plans",
        envelope({
          editable: true,
          reason: "Checkout reads this catalogue.",
          plans: [
            {
              code: "PREMIUM_PLUS_YEARLY",
              tier: "PREMIUM_PLUS",
              label: "Premium Plus",
              amountPaise: 129900,
              amountRupees: 1299,
              durationDays: 365,
              tagline: "Mocks and priority support",
              features: ["Exam-day mock pack"],
              entitlements: ["mocks.exam_pack"],
              recommended: false,
            },
          ],
        }),
      ],
    ]);

    renderAs("ADMIN", <PlansAdmin />);
    expect(await screen.findByText("₹1,299")).toBeInTheDocument();
    expect(screen.getByText(/entitlements are not edited here/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save price/i })).toBeInTheDocument();
  });

  it("names the missing storage credential instead of a usage number", async () => {
    stubApi([
      [
        "/admin/storage",
        envelope({
          configured: false,
          missingEnv: ["SUPABASE_URL", "SUPABASE_SECRET_KEY"],
          objects: null,
          note: "Object counts are not reported until the secret key is set.",
        }),
      ],
    ]);

    renderAs("SUPER_ADMIN", <StorageAdmin />);
    expect(await screen.findByText(/SUPABASE_SECRET_KEY/)).toBeInTheDocument();
    expect(screen.getByText(/no count is shown/i)).toBeInTheDocument();
  });

  it("does not render a generated explanation", async () => {
    stubApi([
      [
        "/assistant/status",
        envelope({
          enabled: true,
          generatesAnswers: false,
          providerConfigured: false,
          note: "Quotations from documents you may read.",
        }),
      ],
    ]);

    renderAs("STUDENT", <AssistantPage />, "/assistant");
    expect(await screen.findByText(/does not write an explanation/i)).toBeInTheDocument();
    expect(screen.queryByText(/here is an explanation/i)).not.toBeInTheDocument();
  });
});
