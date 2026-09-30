#!/usr/bin/env bash
# ============================================================================
# mypy ratchet
# ============================================================================
#
# WHAT THIS IS. A gate that fails only when the mypy error count is GREATER
# THAN a recorded baseline. It is not `mypy --strict` and it is not a plan to
# reach zero; it is a tripwire on REGRESSION, so a change that adds type errors
# is caught in review instead of surfacing three months later as a wall of red
# nobody reads.
#
# WHY A COUNT AND NOT `mypy --exit-zero`. Because the codebase is not clean yet
# and is not going to be this week. A gate that says "no errors at all" against
# a baseline of 36 would be red on every single push, and a permanently red gate
# is a gate nobody runs. The count is the honest formulation: 36 is allowed, 37
# is not.
#
# HOW TO USE IT WHEN YOU FIX SOMETHING. If you fix a type error, the count goes
# DOWN. Lower THRESHOLD in the same commit. That is the whole point of a
# ratchet - it only ever tightens, so the codebase cannot drift back up. Leaving
# the threshold above the real count after a fix is how a ratchet quietly stops
# being a ratchet.
#
# THE EXIT-CODE-2 GUARD. mypy exits 0 (clean), 1 (errors found) and 2 (fatal -
# unreadable config, bad flag, crash). This script treats 2 as a hard failure
# rather than "0 errors, below threshold, pass", because that is precisely the
# failure that a naive `count > threshold` check waves through: a broken mypy
# invocation reports nothing, nothing is 0, and 0 is never greater than the
# threshold. A gate that cannot tell the difference between "clean" and "did not
# run" is worse than no gate, because it is green.
# ============================================================================
set -uo pipefail

# The baseline. Lower it whenever the real count drops - see the note above.
#
# History: 37 at the start of Phase 2, 36 after the dead require_role() was
# deleted (its own missing-return-annotation error went with it), and 35 after
# the Phase 4 SupabaseAuthAdmin(settings) fix removed the call-arg error. It only
# ever goes down, which is the point.
THRESHOLD="${MYPY_ERROR_THRESHOLD:-35}"

# What to check. Defaults to the API package, which is the only Python tree here.
TARGET="${1:-app/}"

echo "::group::mypy ${TARGET}"
# Not `set -e`: mypy exits 1 whenever it finds anything, which is the normal
# operating state of this repository. Letting that abort the script before the
# count is computed would defeat the entire purpose.
mypy_output="$(mypy "${TARGET}" 2>&1)"
mypy_status=$?
echo "${mypy_output}"
echo "::endgroup::"

# Count real per-diagnostic lines, matching mypy's "path:line: error: ..." shape.
#
# The trailing "Found N errors in M files" summary is deliberately NOT parsed.
# When mypy dies early it prints no summary, and a grep for "Found ... errors"
# would return 0 - the same silent-pass hole as above, in a different place.
count="$(printf '%s\n' "${mypy_output}" | grep -cE '^[^[:space:]]+\.py:[0-9]+: error:')"
count="${count:-0}"

echo "mypy error count:     ${count}"
echo "mypy ratchet ceiling: ${THRESHOLD}"

if [ "${mypy_status}" -eq 2 ]; then
    echo "::error::mypy exited 2 (fatal - bad config, bad flag, or a crash)."
    echo "::error::This is NOT a clean run. Fix the invocation before trusting the count above."
    exit 2
fi

if [ "${count}" -gt "${THRESHOLD}" ]; then
    echo "::error::mypy ratchet tripped: ${count} errors > ${THRESHOLD} allowed."
    echo "::error::A change added type errors. Either fix them, or - if the new code is"
    echo "::error::genuinely unavoidable - raise the threshold deliberately and say why"
    echo "::error::in the commit message. Do not raise it to make the gate go green."
    exit 1
fi

if [ "${count}" -lt "${THRESHOLD}" ]; then
    echo "mypy is ${THRESHOLD} - ${count} BELOW the ceiling. Lower MYPY_ERROR_THRESHOLD"
    echo "(default in this script) to ${count} so the ratchet tightens."
fi

echo "mypy ratchet OK: ${count} <= ${THRESHOLD}."
