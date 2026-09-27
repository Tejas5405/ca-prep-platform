# F-05 — The AI "fallback" model was identical to the primary, so it could never fire

**Status: FIXED** · **Severity: P3** · **Milestone: P1 defect remediation** · **Reference commit: `a3113af`**

---

## 1. Original defect

`app/services/gemini.py` carried a module constant:

```python
_FALLBACK_MODEL = "gemini-3.8-flash"
```

and the retry logic was:

```python
text = await _call(api_key, model, prompt)
if text is None and model != _FALLBACK_MODEL:
    text = await _call(api_key, _FALLBACK_MODEL, prompt)
```

`AI_PROVIDER_MODEL` was also `gemini-3.8-flash` — it is the default in
`app/core/config.py`. So for any default-configured deployment:

- `model == _FALLBACK_MODEL`, therefore
- `model != _FALLBACK_MODEL` is `False`, therefore
- **the retry never ran.**

This was not a rare edge case; it was the *only* behaviour the code could have on
a default deployment. The fallback was unreachable code, and the branch that would
have revealed it was silently skipped.

### Why it matters

The condition is not cosmetic. A provider outage is precisely when the retry is
supposed to help, and an outage on a model is almost always an outage *for that
model* — same region, same quota pool, same upstream capacity. So even if the
condition had been reached, retrying `gemini-3.8-flash` after `gemini-3.8-flash`
failed would have re-issued the identical request against the identical failing
dependency. The branch encoded the *appearance* of redundancy while providing
none.

The failure was also **silent and user-visible**: during live verification the
provider returned intermittent `503 high demand`, and every such request silently
degraded to a quotations-only answer. Nothing in the logs said a fallback had been
skipped, because nothing had decided to skip one.

---

## 2. Intended architecture

The project **does** intend to support fallback — the retry branch exists, the
service returns `None` (not an exception) on provider failure, and the assistant
route treats a `None` suggestion as a graceful degradation to quotations. So the
correct outcome was to make the existing mechanism real, not to delete it and
reclassify the feature as unsupported.

Two design decisions followed:

1. **The fallback is configuration, not a constant.** Which model to retry with is
   a deployment choice: it depends on what the operator's provider account can
   reach, and it must be changeable without a code change. It is now
   `AI_PROVIDER_FALLBACK_MODEL`.
2. **An identical pair is rejected at startup.** Silently accepting
   `AI_PROVIDER_FALLBACK_MODEL == AI_PROVIDER_MODEL` would let an operator believe
   they had redundancy while having none. The honest moment to say so is at
   configuration load, not during the outage the fallback was meant to cover.

---

## 3. Fix

### 3a. `app/core/config.py` — the setting, plus validation

```python
ai_provider_model: str = "gemini-3.8-flash"
ai_provider_fallback_model: str | None = None

@model_validator(mode="after")
def _ai_fallback_must_differ(self) -> Settings:
    if (self.ai_provider_fallback_model
            and self.ai_provider_fallback_model == self.ai_provider_model):
        raise ValueError(
            "AI_PROVIDER_FALLBACK_MODEL must differ from AI_PROVIDER_MODEL: "
            "a fallback on the same model retries the same outage."
        )
    return self
```

`None` (the default) means **no fallback** — one attempt, honestly. It is not
treated as "fall back to the default", because that would reintroduce exactly the
defect being fixed.

### 3b. `app/services/gemini.py` — the constant is gone

```python
async def write_suggestion(*, api_key, model, query, excerpts,
                           fallback_model: str | None = None) -> str | None:
    …
    text = await _call(api_key, model, prompt)
    if text is None and fallback_model and fallback_model != model:
        text = await _call(api_key, fallback_model, prompt)
```

The `fallback_model != model` guard is kept **in addition to** the config
validator, as defence in depth: a caller that passes an identical pair cannot
turn a failed primary into a pointless retry of itself.

### 3c. `app/api/v1/assistant.py` — configuration is actually threaded through

```python
answer = await gemini.write_suggestion(
    …,
    model=settings.ai_provider_model,
    fallback_model=settings.ai_provider_fallback_model,
)
```

Without this the setting would have been declared and validated but never read —
the same class of defect, one layer out.

### 3d. `apps/docs/api/openapi.json` regenerated

`Settings` is part of the generated OpenAPI document, so the new field appears
there. The repository has a drift test that fails when the committed document does
not match the app; it was allowed to fail, and then satisfied by regenerating with
the exact command the test itself prescribes. The diff is **11 added lines and

---

## 4. Regression tests

`apps/api/tests/test_gemini_fallback.py` — **7 tests, all offline.** The provider
call is monkeypatched, so the assertions are about *which models were asked for*,
not about any account, quota or bill.

| Test | Asserts |
|---|---|
| `test_primary_failure_uses_the_configured_fallback` | **primary failure → fallback attempted → fallback result returned**, and the models asked were exactly `["primary-model", "fallback-model"]` |
| `test_primary_success_does_not_call_the_fallback` | **primary success → fallback NOT called**; `asked == ["primary-model"]` |
| `test_unset_fallback_means_exactly_one_attempt` | no fallback configured → one attempt, and `None` is returned rather than an invented answer |
| `test_an_identical_fallback_is_never_used_as_a_retry` | passing the same model twice is ignored at the call site too |
| `test_config_rejects_identical_primary_and_fallback_models` | `Settings(...)` raises `ValidationError` |
| `test_config_accepts_a_genuinely_different_fallback` | a different model is accepted |
| `test_config_allows_an_unset_fallback` | the default (no fallback) is valid |

The first two are the two halves of the requirement — fallback **is** attempted on
failure, and is **not** attempted on success — and the first one is the test that
would have failed against the original code, because the original could not
produce `["primary-model", "fallback-model"]` for a default deployment.

The fixture returns `None` for any model it has no scripted answer for, which is
exactly how the real `_call` behaves on a provider error.

---

## 5. Verification

| Check | Result |
|---|---|
| New tests | 7 passed |
| `test_api_contract.py` (OpenAPI drift) | passed after regeneration |
| Full suite (Redis ON, isolated DB) | **1020 passed, 0 failed, 1 skipped** |
| Full suite (no DB) | **0 failures** |
| `ruff check .` / `ruff format --check .` | clean |

**No live provider call was made for this fix.** The tests are hermetic by design,
which is also why the fallback path can be asserted deterministically rather than
by trying to induce a real outage — something that would be both unreliable and
billable.

### Cost and production-safety considerations

- No expensive model was introduced. The default remains `gemini-3.8-flash`.
- `AI_PROVIDER_FALLBACK_MODEL` is **unset by default**, so a deployment that has
  not chosen a fallback behaves exactly as before: one attempt, `None` on failure.
- Nothing in `.env` or `.env.example` was given a fallback model. Choosing one is
  a deployment decision, and inventing a default would either cost money or fail
  validation on a real account.

### Known limitation, stated rather than hidden

The fallback is **model-level only, not provider-level.** Both the primary and the
fallback call the same endpoint
(`https://generativelanguage.googleapis.com/…`) with the same API key. So a
fallback to a different model on the same provider protects against a *model*
outage or capacity limit; it does **not** protect against Google Auth being down or
the whole provider being unreachable. Genuine provider-level redundancy would need
a second provider and a second credential, which is a feature addition and outside
this defect-remediation milestone. The same applies to retry policy: there is no
backoff or bounded retry, only a single immediate second attempt against a
different model.

---

## 6. Files changed

| File | Change |
|---|---|
| `apps/api/app/core/config.py` | add `ai_provider_fallback_model`; validate that it differs from the primary model |
| `apps/api/app/services/gemini.py` | delete `_FALLBACK_MODEL`; accept `fallback_model` and use it only when set and different |
| `apps/api/app/api/v1/assistant.py` | pass the configured fallback model through to the service |
| `apps/docs/api/openapi.json` | regenerate (11 lines added, no other change) |
| `apps/api/tests/test_gemini_fallback.py` | **new** — 7 offline regression tests |
| `docs/F05_AI_FALLBACK_REMEDIATION.md` | this document |

No endpoint, request shape or response contract changed. The assistant route
returns exactly what it returned before, including `None` → quotations-only
degradation.

nothing else** — no unrelated churn.
