#!/usr/bin/env bash
# ============================================================================
# Deployment smoke test.
#
# WHAT THIS IS FOR
#
# `pytest` proves the CODE is correct. It says nothing about whether the thing
# that was actually deployed is wired up. Those failures look identical from
# the outside - a 500, or a blank page - and they are invisible until a user
# hits them. This script asserts the handful of things that break silently:
# the process is alive, the rate limiter is mounted, the payment webhook
# rejects a forged signature, and the browser can actually READ the headers the
# frontend needs.
#
#   ./scripts/smoke_test.sh
#   ./scripts/smoke_test.sh https://caprep-api.onrender.com
#
# EXIT CODES
#   0  every check passed
#   1  at least one check failed
#   2  the target is unreachable (a different problem, and a louder one)
# ============================================================================
set -uo pipefail

TARGET_URL="${1:-http://127.0.0.1:8000}"
TARGET_URL="${TARGET_URL%/}"

# The Origin used for the CORS check. It MUST appear in the target's
# CORS_ORIGINS, or the app is correct and the assertion is wrong - so this is
# overridable rather than hardcoded.
CORS_ORIGIN="${SMOKE_CORS_ORIGIN:-http://localhost:3000}"

PASS=0
FAIL=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ -t 1 ]; then
  GREEN=$'\033[32m'; RED=$'\033[31m'; BOLD=$'\033[1m'; DIM=$'\033[2m'; OFF=$'\033[0m'
else
  GREEN=""; RED=""; BOLD=""; DIM=""; OFF=""
fi

pass() { PASS=$((PASS + 1)); printf '  %sPASS%s  %s\n' "$GREEN" "$OFF" "$1"; }
fail() {
  FAIL=$((FAIL + 1))
  printf '  %sFAIL%s  %s\n' "$RED" "$OFF" "$1"
  [ -n "${2:-}" ] && printf '        %s%s%s\n' "$DIM" "$2" "$OFF"
  return 0
}
section() { printf '\n%s%s%s\n' "$BOLD" "$1" "$OFF"; }

# fetch <name> <curl args...> -> body/headers/status under $TMP
fetch() {
  local name="$1"; shift
  curl -sS -o "$TMP/$name.body" -D "$TMP/$name.hdr" -w '%{http_code}' \
    --max-time 15 "$@" > "$TMP/$name.status" 2>"$TMP/$name.err"
}

status_of() { cat "$TMP/$1.status" 2>/dev/null; }
header_of() {
  # Case-insensitive: HTTP header names are not case sensitive, and Render,
  # uvicorn and the test client all spell them differently.
  tr -d '\r' < "$TMP/$1.hdr" | grep -i "^$2:" | head -1 | sed "s/^[^:]*: *//"
}

printf '%s=== SMOKE TEST %s ===%s\n' "$BOLD" "$TARGET_URL" "$OFF"
printf '%sCORS origin under test: %s%s\n' "$DIM" "$CORS_ORIGIN" "$OFF"

# --- 0. Reachability -------------------------------------------------------
# A connection refusal is NOT a failed assertion; it is a different failure
# with a different fix, and reporting it as "health check failed" sends people
# to debug the wrong layer.
section "0. Reachability"
if fetch root "$TARGET_URL/health" 2>/dev/null && [ -s "$TMP/root.status" ]; then
  pass "target answered an HTTP request at $TARGET_URL"
else
  printf '  %sFAIL%s  target is unreachable\n' "$RED" "$OFF"
  printf '        %scould not connect to %s%s\n' "$DIM" "$TARGET_URL" "$OFF"
  printf '        %s%s\n' "$DIM" "$(head -1 "$TMP/root.err" 2>/dev/null)" "$OFF"
  printf '\n%s=== unreachable: 1 check could not run ===%s\n' "$RED" "$OFF"
  exit 2
fi

# --- 1. Liveness -----------------------------------------------------------
# /health and NOT /api/v1/health, and NOT /health/db or /health/redis: the last
# two probe a dependency, so they fail the deploy on a transient database blip
# instead of telling you the process is alive.
section "1. Liveness"
st="$(status_of root)"
if [ "$st" = "200" ]; then
  pass "GET /health -> 200"
else
  fail "GET /health -> $st (expected 200)" "the process is up but unhealthy, or the health path moved"
fi

if grep -qi '"status"' "$TMP/root.body" 2>/dev/null; then
  pass 'GET /health returns a JSON body carrying "status"'
else
  fail "GET /health body is not the expected JSON" "$(head -c 160 "$TMP/root.body" 2>/dev/null)"
fi

# --- 2. Request ID ---------------------------------------------------------
# Added by the request-context middleware, deliberately mounted BEFORE the rate
# limiter so even a 429 carries one: a request id that vanishes on the error
# you most need to trace is worse than none.
section "2. Request ID"
rid="$(header_of root X-Request-Id)"
if [ -n "$rid" ]; then
  pass "X-Request-Id present ($rid)"
else
  fail "X-Request-Id missing" "a support report cannot be traced to a log line"
fi

# --- 3. Rate limiter mounted -----------------------------------------------
# NOT checked on /health: that path is in RATE_LIMIT_EXEMPT_PATHS on purpose,
# because Render probes it every few seconds and a probe that consumes a
# user's quota makes the app look broken while nothing is. /api/v1/search is an
# ordinary authenticated route, so it IS limited, and it answers 401 without a
# token - enough to prove the limiter ran, because the limiter sets its
# headers on the way out regardless of status.
section "3. Rate limiter"
RATE_PATH="/api/v1/search?q=smoke"
fetch rate "$TARGET_URL$RATE_PATH"
rlim="$(header_of rate X-RateLimit-Limit)"
rrem="$(header_of rate X-RateLimit-Remaining)"
if [ -n "$rlim" ] && [ -n "$rrem" ]; then
  pass "X-RateLimit-Limit=$rlim and X-RateLimit-Remaining=$rrem on $RATE_PATH"
else
  fail "X-RateLimit-* headers missing on $RATE_PATH" \
       "the limiter is not mounted, or RATE_LIMIT_ENABLED=false on this target"
fi

# The limiter must ALSO have left /health alone.
hlim="$(header_of root X-RateLimit-Limit)"
if [ -z "$hlim" ]; then
  pass "/health is exempt from rate limiting (as configured)"
else
  fail "/health is being rate limited (X-RateLimit-Limit=$hlim)" \
       "Render's health probe will burn a user's quota every few seconds"
fi

# --- 4. Webhook rejects a forged signature ---------------------------------
# The check that protects revenue. A webhook that ACCEPTS a forged payload lets
# anyone mark their own order paid. A webhook that rejects everything is a
# silent outage: payments simply never activate, with no error anywhere. So
# this asserts the REJECT path, using a body well-formed enough to reach
# signature verification rather than failing earlier at JSON parsing.
section "4. Payment webhook (fail-closed)"
fetch hook -X POST "$TARGET_URL/api/v1/webhooks/razorpay" \
  -H 'Content-Type: application/json' \
  -H 'X-Razorpay-Signature: 0000000000000000000000000000000000000000000000000000000000000000' \
  -H 'X-Razorpay-Event-Id: evt_smoke_forged' \
  --data '{"entity":"event","account_id":"acc_smoke","event":"payment.captured","contains":[]}'
hst="$(status_of hook)"
case "$hst" in
  400|401|403)
    pass "POST /api/v1/webhooks/razorpay with a forged signature -> $hst (refused)" ;;
  404)
    fail "POST /api/v1/webhooks/razorpay -> 404" "the route is not mounted; payments can never activate" ;;
  503)
    fail "POST /api/v1/webhooks/razorpay -> 503" \
         "expected: this path is fail-closed on a Redis outage, so Redis is probably down" ;;
  *)
    fail "POST /api/v1/webhooks/razorpay -> $hst (expected 400/401/403)" \
         "a 2xx here means a forged signature was ACCEPTED - treat this as an incident" ;;
esac

# --- 5. CORS expose-headers ------------------------------------------------
# The frontend reads X-Request-Id and X-RateLimit-* to show a request id and a
# remaining-quota count. Browsers hide any response header not named in
# Access-Control-Expose-Headers, so a missing entry is a silent `undefined` in
# the UI that no server-side test can see.
section "5. CORS"
fetch cors "$TARGET_URL$RATE_PATH" -H "Origin: $CORS_ORIGIN"
acao="$(header_of cors Access-Control-Allow-Origin)"
expo="$(header_of cors Access-Control-Expose-Headers)"

if [ -z "$acao" ]; then
  fail "no Access-Control-Allow-Origin for Origin: $CORS_ORIGIN" \
       "either CORS_ORIGINS does not list '$CORS_ORIGIN' on this target, or the middleware is off"
else
  pass "Access-Control-Allow-Origin: $acao"
fi

if [ -z "$expo" ]; then
  fail "Access-Control-Expose-Headers missing" \
       "the browser cannot read X-Request-Id or X-RateLimit-*; the UI will show undefined"
else
  for h in X-Request-Id X-RateLimit-Limit; do
    if printf '%s' "$expo" | grep -qi "$h"; then
      pass "Access-Control-Expose-Headers advertises $h"
    else
      fail "Access-Control-Expose-Headers does not advertise $h" "got: $expo"
    fi
  done
fi

# --- Summary ---------------------------------------------------------------
printf '\n%s=== %d passed, %d failed ===%s\n' "$BOLD" "$PASS" "$FAIL" "$OFF"
[ "$FAIL" -ne 0 ] && exit 1
exit 0
