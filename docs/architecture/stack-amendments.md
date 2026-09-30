# Stack Amendments

The master blueprint v3.0 is a **frozen** document. Changes to it are recorded
here rather than made silently in code, so that a future reader can see what
diverged, why, and what would have to be true to revisit it.

Two rules govern this file:

1. **Every amendment names the affected blueprint section.** An amendment that
   does not say what it is diverging from is not auditable.
2. **Nothing is amended without a reason that would survive scrutiny.** "We
   preferred it" is not a reason; "the original choice had a privilege-escalation
   bug" is.

---

## SA-05 — Authentication provider: Supabase Auth → Firebase Auth

**Status:** SUPERSEDED by SA-09 — read that one instead. Kept because the reasoning
that led here (and the privilege-escalation bug it fixed) is why SA-09 is careful
about metadata in the first place.
**Date:** 2026-09-24
**Blueprint sections affected:** §2 (stack), §5 (architecture), §8 (auth),
§12 (storage), §24 (rejected choices)

### What changed

| Concern | Blueprint v3.0 | Now |
|---|---|---|
| Identity / credentials | Supabase Auth | **Firebase Auth** |
| Token format | Supabase JWT (HS256 or RS256) | Firebase ID token (RS256) |
| JWKS source | `<project>.supabase.co/auth/v1/.well-known/jwks.json` | `googleapis.com/.../securetoken@system.gserviceaccount.com` |
| Role claim location | `user_metadata.role` / `app_metadata.role` | `https://caprep.in/role` (custom claim) |
| Audience claim | `authenticated` | the Firebase project id |
| Issuer claim | `<project>.supabase.co/auth/v1` | `https://securetoken.google.com/<project-id>` |
| File storage | Supabase Storage | **unchanged — still Supabase Storage** |
| Postgres / Redis / API / OCR | unchanged | unchanged |

### Reason

The primary reason is a **privilege-escalation bug in the Supabase
implementation that shipped in this repository's own previous commit.**

It read the application role from `user_metadata`:

```python
# SUPERSEDED — do not restore
raw_role = claims.get("user_metadata", {}).get("role")
```

In Supabase, `user_metadata` is **writable by the authenticated user**. A student
could run:

```js
await supabase.auth.updateUser({ data: { role: 'ADMIN' } })
```

and then receive a freshly issued token containing `role: "ADMIN"`. The token is
validly signed — it was just signed *after* the user changed their own claim. No
amount of signature, issuer or audience verification would have caught it,
because nothing about the token was forged.

Firebase custom claims are settable **only through the Admin SDK**, from a
server holding service-account credentials. The escalation path is therefore not
merely closed but structurally absent, which is a property worth more than a
carefully-written check.

Supporting reasons, in order of weight:

1. **Phone auth and MFA are first-class.** This matters for the target market:
   an Indian student signing up on a phone is far more likely to use an OTP flow
   than to type an email and password, and Firebase's phone auth is mature.
2. **Revocation exists.** `revokeRefreshTokens(uid)` invalidates a session
   server-side. Supabase requires the same via a different mechanism, but
   Firebase's is well-trodden and pairs with `auth_time` checks.
3. **Google sign-in is a one-liner** and already configured in `AuthProvider`.

### Consequences, and what had to change

**1. Supabase Storage RLS can no longer authorise a student.**

This is the most important consequence and it is easy to miss. Supabase Storage
row-level-security policies evaluate `auth.uid()` from a **Supabase Auth JWT**.
The platform no longer issues those, so RLS policies on the storage buckets
cannot identify a Firebase user, and the current RLS setup is effectively a
no-op for student requests.

Access is therefore **backend-brokered**:

```
client --(Firebase ID token)--> API
                                 |
                                 | verify token, apply role check,
                                 | validate upload metadata
                                 v
                        signed upload URL (service role, 5 min TTL)
                                 |
client --(PUT file bytes)-------> Supabase Storage
                                 |
client --(storage path)--------> API --> enqueue ingestion job
```

The API is now the **only** authorization boundary for file access. This has a
benefit that was not the reason for the change: large files never transit the
API, so a 50 MB scanned paper does not hit Render's request-size limit or occupy a
worker for the duration of the transfer.

**2. Live recommendation:** **delete or replace** the existing Supabase Storage
RLS policies. A policy that appears in the dashboard but silently never matches is
worse than no policy, because it reads as protection that is not there. The
buckets must remain **private** and the service-role key must never leave the
server.

**3. `users.auth_user_id` now holds a Firebase UID, not a Supabase UUID.**

The column name was chosen specifically to avoid encoding the vendor, so **no
migration is required.** Existing rows would need a re-link if any real users
existed — they do not, since the platform has not launched.

**4. Backend changes:**

- `app/core/security.py` — rewritten: RS256 pinned (single algorithm, so no
  confusion attack), `aud` and `iss` verified, role read from a namespaced custom
  claim only, `require_recent_auth` added for sensitive operations.
- `app/core/config.py` — `FIREBASE_PROJECT_ID`, `FIREBASE_SERVICE_ACCOUNT_JSON`;
  the Supabase JWT settings were removed rather than left unused, so a stale
  setting cannot be mistaken for a working one.
- `tests/test_security.py` — 46 tests, including a hand-rolled RS256→HS256
  confusion-attack token and a regression suite for the escalation bug above.

**5. Frontend changes:** `apps/web/src/lib/firebase.ts`,
`apps/web/src/hooks/AuthProvider.tsx`. Firebase web config uses `VITE_` variables
because it is **public by design** — it identifies the project, it does not
authorise access.

### Verification

Two mutation checks confirm the guards are load-bearing rather than decorative:

| Mutation applied | Test result |
|---|---|
| `ALLOWED_ALGORITHMS` widened to include `HS256` | **fails** — the confusion-attack test catches it |
| Role read from a `user_metadata`-shaped claim | **fails** — `test_a_user_writable_style_claim_is_ignored_entirely` catches it |

### Trigger to revisit

Revisit if any of the following becomes true:

- Firebase's pricing or quota limits become binding at the platform's growth rate
- A hard requirement appears for Supabase-native RLS on storage, which would
  mean issuing Supabase tokens alongside Firebase ones (two identity systems — a
  strong argument against)
- The platform needs to support enterprise SSO/SAML, where a dedicated IdP is a
  better fit

---

## SA-06 — Search: PostgreSQL FTS only, trigram added

**Status:** Adopted (extension of §9.3, not a reversal)
**Date:** 2026-09-24
**Blueprint sections affected:** §2, §9.3

### What changed

The blueprint requires a GIN index over `to_tsvector('english', text)`
(§9.3) and says "PostgreSQL FTS is sufficient; do not add a search server yet"
(§2). Both are honoured.

Added alongside it: the **`pg_trgm` extension and a trigram GIN index** on
`questions.text`.

### Reason

Full-text search matches whole **lexemes**. A student typing a partial word —
`deduc` — gets nothing, because `to_tsvector('english', 'Deductions')` produces
`deduc` only as a *stem of the full word*, not as a prefix match. Trigram
similarity is what makes substring search work, and searching a question bank by
half-remembered phrase is a core use case.

### Scope

This does **not** add a search service. It stays inside the single PostgreSQL
instance the blueprint mandates, and it is created in the initial migration so
there is no runtime dependency to manage. The "no search server yet" instruction
is intact.

### Trigger to revisit

The same trigger the blueprint already states for a search server. If that
trigger is reached, both indexes are replaced together rather than incrementally.

---

## SA-07 — Frontend pinning: TypeScript 5.x and Vitest 4.x

**Status:** Adopted
**Date:** 2026-09-24
**Blueprint sections affected:** §2 (frontend), §19 (tooling)

### What changed

Both packages are held one major version behind their latest release
deliberately:

| Package | Latest | Pinned | Because |
|---|---|---|---|
| TypeScript | 7.0.2 | `~5.9.3` | `typescript-eslint@8` declares `typescript: >=4.8.4 <6.1.0` |
| Vitest | 5.0.1 | `^4.1.11` | Vitest 5 requires Node `^22.12`; this project targets Node 20 |
| jsdom | 30.1.1 | `^29.0.0` | jsdom 30 requires Node `^22.22` and its undici build crashes on Node 20 |
| jest-dom | 7.0.1 | `^6.9.1` | Requires Node `>=22` |

### Reason

Adopting any of them turns a working, lint-clean, tested frontend into a broken
one. The TypeScript case is the clearest: TypeScript 7 is the native compiler
rewrite and `typescript-eslint` does not support it yet, so upgrading means
either losing linting or losing types.

Node 20 is the target because it is what Vercel's build image defaults to. The
alternative — moving to Node 22 — is available and probably the right call
before launch, but it is a deployment decision rather than a package bump.

These are **not** "we did not get around to it" pins. Each has a named blocker
and each is checkable in one command.

### Trigger to revisit

- Node 22 adopted on Vercel → Vitest, jsdom and jest-dom can all move to latest
- `typescript-eslint` supports TypeScript 7 → TypeScript can move

---

## SA-08 — Supabase publishable/secret API keys replace anon/service_role

**Status:** Adopted, with one clause AMENDED by SA-09: the publishable key is now
used in the browser, because Supabase Auth runs there. The "publishable key is
deliberately unused" section below is historical — see SA-09 for the boundary as
it stands.

**Status:** adopted · **Blueprint:** v3 §5.1 (Supabase Storage) · **Supersedes:** the
implicit use of the legacy `service_role` JWT

### What changed

Supabase is retiring the legacy `anon` and `service_role` JWTs by the end of 2026
in favour of opaque keys:

| Legacy | Replacement | Format | Where it may live |
|---|---|---|---|
| `anon` | Publishable key | `sb_publishable_...` | Public: browsers, source, CLIs |
| `service_role` | Secret key | `sb_secret_...` | **Backend only** |

The backend variable is renamed `SUPABASE_SERVICE_ROLE_KEY` →
`SUPABASE_SECRET_KEY`. Both names are accepted (`AliasChoices` in
`app/core/config.py`), so an environment can migrate by changing one value.

### Why this is not a drop-in swap

The new keys are **opaque strings, not JWTs**, and they authenticate on a
different header. Verified against the live project:

| Headers sent | Result |
|---|---|
| `apikey: sb_secret_...` | **200** |
| `Authorization: Bearer sb_secret_...` | **403 `Invalid Compact JWS`** |
| both, with identical values | 200 |

The original client sent only `Authorization: Bearer`. That works with a legacy
`service_role` JWT and fails with every current-format key, so the storage
integration would have broken completely on the new key system — and would have
kept working in any environment still holding a legacy key, which is exactly how
this class of bug survives testing.

`SupabaseStorage._build_headers` now detects the format: `sb_` keys go on
`apikey` only; legacy JWT keys go on both.

### Two further defects the live project exposed

1. **Bucket names cannot contain a path separator.** `BUCKETS` held
   `"question-pdfs/originals"`. Supabase rejects that with
   `400 InvalidBucketName`. The blueprint's `originals/`/`processed/` split is a
   *path prefix* within one bucket, now modelled as `PREFIXES`. Nothing caught
   this because the URL is `/object/{bucket}/{path}` — a slash in the bucket
   silently shifts a path segment into the bucket position.
2. **The buckets did not exist.** Created as private: `question-pdfs`,
   `question-media`, `user-uploads`, all with a 50 MB limit and `public: false`.

### The publishable key is deliberately unused

The frontend needs **no** Supabase key at all. Every upload and download is
brokered by the API as a signed URL, and a signed upload URL is a self-contained
bearer capability — verified by uploading from an unauthenticated client with no
key present (200). Adding the publishable key to the browser bundle would create
a second path to student files outside the backend's authorization boundary,
which is the boundary Supabase RLS cannot enforce for Firebase users (SA-05).

### Trigger to revisit

- Supabase disables the legacy keys → the `AliasChoices` fallback and the
  legacy-JWT header branch can be removed
- A student-facing direct-to-Supabase feature is proposed → re-read the note
  above before shipping the publishable key

---

## SA-09 — Authentication provider: Firebase Auth → Supabase Auth

**Status:** Adopted (reverses SA-05)
**Date:** 2026-09-25
**Blueprint sections affected:** §2 (stack), §5 (architecture), §8 (auth),
§12 (storage), §24 (rejected choices)
**Supersedes:** SA-05

### What changed

| Concern | SA-05 (Firebase) | Now |
|---|---|---|
| Identity / credentials | Firebase Auth | **Supabase Auth** |
| Token format | Firebase ID token (RS256) | Supabase access token (**ES256** on this project) |
| JWKS source | `googleapis.com/.../securetoken@...` | `<project>.supabase.co/auth/v1/.well-known/jwks.json` |
| Role claim | `https://caprep.in/role` custom claim | `app_metadata.role`, then `https://caprep.in/role` |
| Audience claim | the Firebase project id | `authenticated` |
| Issuer claim | `https://securetoken.google.com/<id>` | `<project>.supabase.co/auth/v1` |
| Sign-in methods | email/password + Google | **unchanged — email/password + Google** |
| Database | Supabase PostgreSQL | unchanged |
| File storage | Supabase Storage | unchanged |
| API / Redis / OCR | unchanged | unchanged |

Firebase is removed from the codebase: no SDK in `package.json`, no
`firebase.ts`, no `FIREBASE_*` environment variable anywhere, and
`test_infra_contract.py` now has no Firebase references at all.

### What this amendment does NOT change: who authorizes

SA-09 is about the **identity provider** — who holds credentials and who signs the
token. It says nothing about **authorization**, and the two are easy to conflate
because both are "role" in casual speech.

**The role claim is not the authority.** The table above lists where the claim lives
(`app_metadata.role`) because that is where the token's copy of the role lives. The
API does not read it to decide access:

| Question | Answered from | Not from |
|---|---|---|
| Who is this? (`sub` → user) | the verified JWT | anything else — never a header, query or body |
| What role do they have? | `users.role` in Postgres | the token claim |
| What may they do? | `ROLE_PERMISSIONS`, via `require_permission` | a role-name comparison |

`resolve_role()` reads the row that `get_current_user()` already loaded, and falls
back to `STUDENT`. A role change therefore takes effect on the **next request**
rather than when the user's hour-long token expires — which is the entire reason
the claim is treated as advisory.

Supabase Auth therefore owns *credentials and signatures* in this architecture, and
nothing else. The claim is still written on a role change
(`SupabaseAuthAdmin.set_role_claim`) so that the Supabase-side row and the client's
token converge, but that write is best-effort and non-blocking: a failure is a
delay, not an outage, and it cannot grant access that the row does not.

Full diagram and rationale: [README → Authentication and authorization](../../README.md#authentication-and-authorization).
→ `app/core/permissions.py`, `app/integrations/supabase_auth.py`

### Reason

**One vendor, not two.** SA-05 introduced Firebase for identity while leaving the
database and storage on Supabase — the exact split the blueprint's own
single-vendor argument (§24) rejected. The practical cost showed up immediately:
Supabase Storage evaluates *Supabase* JWTs for row-level security, so a Firebase
token cannot authorise a Storage request at all. SA-05's answer was to broker
every file through the API with the secret key, which is correct but means the
browser holds a session for one vendor while reading data from another, and the
platform pays for two identity vendors, two dashboards, two sets of quotas and
two error vocabularies for a product with one login screen.

With Supabase Auth, the same three services answer to one project: the token
`iss` is the database's project, the storage RLS policies can evaluate the same
`sub`, and the free tier covers the launch phase. This is a reversal of SA-05,
not a refinement of it.

### What the reversal FIXED for free

The Firebase verifier read the role from `user_metadata.role` — which the
authenticated user can write. That was a live privilege-escalation bug, fixed
under SA-05 by moving to a custom claim. A custom claim is the right shape and it
is preserved here, but Supabase carries the same trap in a different costume:

- **`user_metadata` is still user-writable.** It is not read anywhere.
- **Supabase's top-level `role` claim is `"authenticated"`, not an app role.** The
  Firebase-era fallback that read a bare `role` claim had to be DELETED, not
  ported: on Supabase it would have resolved every signed-in student to an unknown
  role — or worse, resolved an `anon` key to the same value. `extract_role()` reads
  `app_metadata.role` (server-set, via the Admin API) and then the namespaced
  `https://caprep.in/role`, and nothing else.
- **API keys cannot authenticate.** `anon`, `publishable` and `service_role` are
  all valid Supabase tokens and all carry `role` values that are not
  `"authenticated"`. `verify()` therefore requires `role == "authenticated"` *and*
  a non-empty `sub`, which rejects every key class outright. Pinned by
  `TestApiKeysCannotAuthenticate`.

### Deliberate weakenings, recorded rather than hidden

**A session token is now verifiable as a JWT, not an opaque handle.** Supabase
access tokens are signed JWTs, so verification is local against the JWKS and no
round trip to the Auth server is needed on the request path. The project's JWKS
is asymmetric — `kty=EC`, `alg=ES256`, one active `kid` — so the API needs no
shared secret. HS256 is deliberately NOT in `ALLOWED_ALGORITHMS`: with a
symmetric algorithm the public key would become a signing key, and the classic
`alg` confusion forgery (sign HS256 with the public key as the HMAC key) is
pinned as a test.

**`require_recent_auth` is honestly weaker than it was on Firebase.** Firebase
put `auth_time` in the token — the moment the user actually authenticated.
Supabase does not, so `Principal.authenticated_at` reads `iat`, which advances on
every silent token refresh. It is therefore a **staleness bound**, not proof of a
fresh password entry: an idle tab that refreshes its token looks "recent". The
docstring and the property both say so. Anything genuinely destructive must
re-authenticate explicitly rather than trusting this.

**Issuer comparison is exact.** The issuer is `<project-url>/auth/v1` with no
trailing slash, and `SUPABASE_URL` is normalised to match. A trailing slash in the
environment would otherwise fail every token; there is a test for that case.

### Google sign-in is NOT live

Email plus Google is the required sign-in set, and the project currently has
**email only**. Supabase returns `400 provider is not enabled` from
`/auth/v1/authorize?provider=google` until it is switched on:

1. Authentication → Providers → Google, enable it.
2. Create a Google Cloud OAuth client (Web application) and paste its id/secret.
3. Authorised redirect URI in Google Cloud:
   `https://<project-ref>.supabase.co/auth/v1/callback`.
4. Authentication → URL Configuration: set the Site URL, and add every origin
   with `/auth/callback` to Redirect URLs.

The app reports each of these failures with the exact value to paste, rather than
failing silently (see `apps/web/src/lib/authErrors.ts`).

### The publishable key is now in the browser — and that is the whole point

SA-08 recorded that the publishable key was deliberately unused because the
browser needed no Supabase access. **Supabase Auth changes that**: sign-in happens
in the browser, so `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` (publishable)
now ship in the bundle. The boundary is unchanged, and it is narrower than it
looks:

- The browser talks to the **Auth API only** — never PostgREST, never Storage.
- A publishable key reaches the Auth API, and verified against the live project it
  lists **zero** storage buckets, so it is not a path to student files.
- The secret key (`sb_secret_...`) is Postgres `service_role` with BYPASSRLS. It
  stays server-side, and tests assert the **built bundle** contains no secret.
- `test_the_only_supabase_variables_in_the_frontend_are_the_public_ones` replaces
  SA-08's blanket "no `VITE_SUPABASE_*`" rule with an allow-list, so the relaxation
  is written down instead of discovered later.

The API remains the single authorization boundary **by design**: one place
enforces entitlement, quota and ownership, and no future table needs an RLS policy
correct enough to be a second one.

### Trigger to revisit

- A student-facing feature needs to read or write tables directly from the browser
  → that is the point at which RLS becomes load-bearing again; re-read the
  boundary argument above before doing it
- Supabase adds `auth_time` (or an equivalent) to the access token → restore a
  real `require_recent_auth`
- Supabase retires the legacy JWT keys at the end of 2026 → the `service_role`
  fallback in `_build_headers` and the legacy-`anon` check in the infra tests can go
- Any third-party admin tool is introduced that needs `service_role` → rotate the
  secret key first; both keys in this project transited a chat window during setup
