# Staging Data Policy

**Milestone:** repository ownership + staging readiness · **Baseline commit:** `49adb42`

Staging exists to prove the system works before real students meet it. That goal is
defeated the moment real data enters it: the environment stops being a safe place
to experiment, and a mistake costs a person something.

**The governing rule: staging contains only data that could be published on a
public website with no consequence.**

---

## 1. Absolute prohibitions

| Never | Why |
|---|---|
| Real student personal data | names, emails, phone numbers of actual people |
| Real payment credentials or transactions | Razorpay **TEST mode only**; no live keys, ever |
| Production user data, in any form | including "just a few rows for debugging" |
| Real uploaded documents | past papers, books, scans the platform does not own |
| Production database backups restored into staging | a restore copies production personal data with it |
| Real email addresses | email is triggered by `RESEND_API_KEY`; a real address means a real inbox |
| Real phone numbers | the schema carries a `phone` column and OTP flows exist |
| Screenshots or logs containing any of the above | including in a bug report or a chat |

A staging database restored from a production backup is the most likely accidental
breach, because it looks like a convenience rather than a copy of user data. It is
prohibited. Use `python -m app.seed` instead.

---

## 2. What staging may contain

### Synthetic users

| Field | Value |
|---|---|
| Email | `student+staging-01@example.invalid`, `admin+staging@example.invalid` |
| Password | random per environment, stored in the password manager, never in git |
| `auth_user_id` | `staging-…` prefix, obviously synthetic |
| Name / display name | `Staging Student One`, `Staging Admin` |
| Phone | **omitted**, or a reserved non-routable placeholder |

`.invalid` is an RFC 2606 reserved TLD: it can never be registered or receive
mail, so a synthetic identity cannot become a real one and cannot email anyone.
The P1 milestone used exactly this convention locally (`content@seed.invalid`,
`s@pb.invalid`) and it worked well.

### Synthetic reference content

`python -m app.seed` is the only sanctioned source. It is idempotent by
construction and uses deterministic `uuid5` ids, so the same content has the same
id in every environment — which is what makes an attempt recorded in staging
comparable to one recorded elsewhere.


### Synthetic documents

| Rule | Detail |
|---|---|
| Only generated PDFs | produced by a script in this repository, or hand-written for the test |
| No third-party scans | no ICAI papers, no textbook pages, no question-bank exports |
| Personal data inside the PDF | none — synthetic names, synthetic amounts |
| Naming | `staging-<topic>.pdf` so provenance is obvious in the bucket listing |
| Retention | delete after the test; staging buckets are not archives |

Two tiers must both be exercised, because they take different code paths:

- **text-layer PDF** → PyMuPDF tier;
- **image-only PDF** → Tesseract OCR tier, the one that silently no-ops if the
  binaries are missing.

A scanned document is the important one. It is precisely the case where a missing
OCR binary produces zero drafts, no error on the job, and a healthy-looking worker.

### Synthetic payments

| Rule | Detail |
|---|---|
| Razorpay mode | **TEST only** |
| Cards | Razorpay's published test card numbers |
| Amounts | small and round, e.g. ₹1 / ₹99 |
| Webhook secret | staging-only; different from production |
| Real money | **never moves**; TEST mode cannot move it |
| Reconciliation | not applicable — nothing settles |

### Test AI prompts

| Rule | Detail |
|---|---|
| Content | questions about the seeded public syllabus, or about the platform's own behaviour |
| Never | real student questions, real dispute documents, anything confidential |
| Expectation | a *grounded* answer or an honest refusal — **both are acceptable**; an invented section or rate is not |
| Volume | low, with `AI_MONTHLY_CEILING_USD` set low in staging |

`AI_PROVIDER_API_KEY` is a real, billed credential even in staging. Quotas exist
(`AI_FREE_QUERIES_PER_DAY`), and a runaway loop would be a real cost.

---

## 3. Verification before staging is called ready

| # | Check | Pass condition |
|---|---|---|
| 1 | No real email domain in any row | only `*.invalid` / `example.com` |
| 2 | No real phone numbers | column empty or placeholder |
| 3 | No document predating the staging environment | uploaded files match `staging-*` |
| 4 | Razorpay dashboard shows TEST mode | keys are test keys; no live transactions exist |
| 5 | No production backup was restored | provenance of every table documented |
| 6 | Secrets are staging secrets | each differs from production; none appear in git |
| 7 | Deletion works | synthetic users and documents can be removed, and a synthetic order cancelled |

Checks 1 and 2 are the ones most likely to be skipped and most costly if they are,
so they should be run as a query against the staging database before the
environment is declared usable, not assumed.

---

## 4. Retention

| Data | Retention in staging |
|---|---|
| Synthetic users | until the next clean rebuild; delete on request |
| Seeded content | permanent (regenerable, deterministic) |
| Test documents | delete after the test that created them |
| Test orders | keep for the length of a payment test cycle, then purge |
| Logs | per the provider's retention; must not contain secrets or tokens |

**Staging is not an archive.** If something is needed for evidence, the test
output is the evidence — not the row.

---

## 5. Why this is written down

Because "it's only staging" is the same reasoning that destroyed the V1 development
database during the P1 milestone. That data was genuinely disposable, and it was
still destroyed by a process that had no business touching it. Disposability
describes the *data*; it does not confer permission on some other actor to delete
it — and equally, it does not make real data safe just because the environment is
temporary.

The same discipline applies in reverse: staging data must be recognisably
synthetic so that a future reader can tell, from the row alone, that no person is
behind it.

Seeded content: 3 courses, 16 subjects, 35 chapters, 27 questions, 3 mock papers,
3 exam sessions. All of it is real CA syllabus structure, which is public
information and the platform's reason to exist.
