# F-02 — JWKS fetch failed TLS verification on a stock python.org macOS install

**Status: FIXED** · **Severity: P1/P2** · **Milestone: P1 defect remediation** · **Reference commit: `a3113af`**

---

## 1. Original defect

On the python.org macOS interpreter used here (Python 3.13.0,
`/Library/Frameworks/Python.framework/Versions/3.13`), every JWKS fetch failed:

```
ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
unable to get local issuer certificate
```

The failure was reported to the user as:

```
401 {"detail":"Invalid authentication token"}
```

That message is the dangerous part. **A transport-layer failure was being reported
as a credential rejection.** A perfectly valid Supabase access token was refused
because the process could not download the keys needed to check its signature — so
the service looked like it was rejecting credentials, when in reality it was
unable to establish trust in the key server. Anything debugging this from the
outside — a developer, a support engineer, an operator reading a 401 in a log —
would start looking for expired sessions and revoked users, and would not find
the cause.

The workaround found during verification was to export
`SSL_CERT_FILE=$(python3 -m certifi)`, which made it work. That is a **manual,
undocumented, per-shell** step that nothing in the repository asked for, so every
new terminal, CI job and teammate reproduced the 401.

---

## 2. Root cause

**The application had no control over its own trust store.** It inherited whatever
the interpreter happened to provide.

`PyJWKClient` fetches the JWKS document with `urllib`, using the interpreter's
*default* SSL context. On a stock python.org macOS install that context contains
**no CA bundle at all** until the user runs `/Applications/Python 3.x/Install
Certificates.command`. Consequently:

| Environment | Default SSL context | JWKS fetch |
|---|---|---|
| python.org macOS, before `Install Certificates.command` | empty trust store | `CERTIFICATE_VERIFY_FAILED` → 401 |
| Same machine, with `Install Certificates.command` run | system Keychain | works |
| Linux / Render image (`certifi` already a transitive dependency) | populated | works |

Two properties of this made it a genuine defect rather than a local quirk:

1. **The application is not the interpreter.** It cannot require every operator to
   have run a `.command` file, and it cannot detect when they have not. The
   *deployment image* was fine while the *developer machine* was broken, so CI
   would have stayed green and the defect would have shipped.
2. **The result was silent and misleading.** A transport error surfaced as an
   authentication verdict, pointing debugging effort at the wrong system entirely.

`httpx` already pulls in `certifi` transitively, but the code path that actually
performs the fetch — `urllib`, inside PyJWT — never used it. Depending on a
transitive dependency for a security-critical trust decision is also fragile: a
resolution change in `httpx` could have removed the trust store from under token
verification with nothing failing.

---

## 3. Fix

`apps/api/app/core/security.py` — `SupabaseTokenVerifier._get_jwk_client()` now
builds an explicit SSL context and passes it to `PyJWKClient`:

```python
import ssl
import certifi
from jwt import PyJWKClient

context = ssl.create_default_context(cafile=certifi.where())

self._jwk_client = PyJWKClient(self.jwks_url, cache_keys=True, ssl_context=context)
```

`apps/api/requirements.txt` — `certifi` is now an explicit, pinned dependency:

```
certifi==2026.5.20
```

### Why this is a documented, standard strategy

- **`ssl.create_default_context()` is the strict default.** It creates a context
  with `verify_mode=CERT_REQUIRED` and `check_hostname=True`. The code supplies
  **only** the CA bundle file; every security-relevant default is left at its
  strictest setting. There is no code path that relaxes them.
- **certifi is the reference CA bundle for Python.** It is Mozilla's NSS root
  store, shipped as a package precisely so that applications do not depend on the
  host's trust configuration.
- **It makes the trust store identical everywhere** — macOS developer laptops,
  Linux CI, and the Render image now verify against the same 119 roots instead of
  three different stores.
- **It is declared, not inherited.** The comment in `requirements.txt` states the

### What was explicitly NOT done

Every one of these was available and would have "worked":

| Rejected | Why |
|---|---|
| `verify=False` / `CERT_NONE` | A JWKS fetch that skips verification accepts **any** key set, so an attacker who can intercept the connection can mint tokens the API will accept as valid. This is not a shortcut, it is a total loss of authentication. |
| `check_hostname=False` | Disables hostname verification, so a valid certificate for *any* host would be accepted. |
| Suppressing the `SSLError` | Would convert a hard failure into a silent one — the exact "401 for a transport problem" confusion being fixed. |
| A custom/insecure `SSLContext()` | Defaults to not verifying anything unless told otherwise. |
| Hardcoding a certificate or fingerprint | Pinned to one project, breaks Supabase's key rotation, and cannot be justified when a maintained bundle exists. |
| Shipping a `.pem` in the repository | Certificate material in git is a maintenance burden and a rotation hazard. |

**No TLS security was traded for convenience.** The fix makes verification
*stricter and more predictable*, not looser.

---

## 4. Regression tests

`apps/api/tests/test_security.py` → `class TestJwksTlsContext`. These are offline
— no external request — and they assert the *security properties* of the context,
because the lazy fix (a context with verification disabled) would satisfy "an
SSL context is passed" while defeating the entire purpose.

| Test | Asserts |
|---|---|
| `test_jwk_client_receives_a_verifying_certifi_context` | the client receives an `ssl.SSLContext`; `verify_mode == ssl.CERT_REQUIRED`; `check_hostname is True`; `get_ca_certs()` is **non-empty**; `cache_keys is True`; the URI is the project's JWKS URL |
| `test_context_is_built_from_certifi` | `ssl.create_default_context` is called with exactly `certifi.where()` — so the bundle cannot be quietly swapped for something else |

The second test is the one that catches a future regression to
`create_default_context()` with no `cafile` (back to the empty trust store) or to
a hand-rolled context.

---

## 5. Verification

### 5a. Live JWKS fetch, with `SSL_CERT_FILE` explicitly unset

```bash
$ env -u DEBUG -u SSL_CERT_FILE python3 -c "…"
SSL_CERT_FILE set: False
supabase host: zyrmlnpvylhcpyaoizyz.supabase.co
LIVE JWKS FETCH OK, keys = 1
   kid=17022b79-b6a2-4c76-b4b1-f7c4481bb630 | alg=ES256 | use=sig
```

The **real** Supabase project's key set was fetched over TLS with the real ES256
signing key, and `SSL_CERT_FILE` was removed from the environment specifically so
that nothing outside the code could be supplying the trust store. This is the
defect's original repro, and it now succeeds.

### 5b. End-to-end: valid JWT → JWKS → authentication → protected endpoint

Covered live during the P1 milestone against the real Supabase project:

| Step | Result |
|---|---|
| Real Supabase user signs in (password grant) | access token issued |
| API fetches the JWKS over TLS (no `SSL_CERT_FILE`) | **200**, 1 key |
| Token signature verified against the published key | accepted |
| 6 authenticated routes | **200** |
| 3 admin-only routes, called as `STUDENT` | **403** (authorization still enforced) |

The 403s matter as much as the 200s: making TLS work must not have weakened
authorization, and it did not — a student still cannot reach admin data.

### 5c. Full regression suite

`1020 passed, 0 failed, 1 skipped` (with the database) and `0 failures` without
it. `ruff check` and `ruff format --check` clean.

---

## 6. Files changed

| File | Change |
|---|---|
| `apps/api/app/core/security.py` | build an explicit verifying SSL context from certifi and pass it to `PyJWKClient` |
| `apps/api/requirements.txt` | declare and pin `certifi==2026.5.20` |
| `apps/api/tests/test_security.py` | `TestJwksTlsContext` — 2 offline regression tests |
| `docs/F02_JWKS_TLS_REMEDIATION.md` | this document |

No API contract, no token format, no claim set and no authorization rule was
changed. The fix is confined to how the process establishes trust in the key
server.

---

## 7. Environment note

The supported runtime is now **any** environment that can import `certifi`, which
is every environment this project installs via `requirements.txt`. No
`Install Certificates.command`, no `SSL_CERT_FILE` export, no host trust-store
configuration, and no per-developer setup step is required.

`SSL_CERT_FILE` is no longer needed anywhere. It was never a documented part of
the setup, so nothing has to be un-done for existing users.

  reason: app code imports `certifi` directly, so a change in `httpx`'s resolution
  must not be able to remove the trust store out from under token verification.
