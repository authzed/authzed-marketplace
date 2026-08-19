#!/usr/bin/env bash
# Runs one fixture's full end-to-end cycle (scripts/run-fixture.sh) N times in a row
# and tallies how many runs came back PASS vs. FAIL, to prove (or disprove) that the
# comparison's result doesn't depend on incidental timing -- added after fix round 1
# found `minLatency()` on a write-then-check pair flaked ~1 run in 5 against a real
# `spicedb serve-testing` instance (revision quantization, not a bug in the server).
#
# Usage: scripts/determinism-check.sh <fixture> <iterations>
set -euo pipefail

FIXTURE="${1:?usage: determinism-check.sh <fixture> <iterations>}"
ITERATIONS="${2:?usage: determinism-check.sh <fixture> <iterations>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PASS_COUNT=0
FAIL_COUNT=0

for i in $(seq 1 "$ITERATIONS"); do
  echo "--- [$FIXTURE] determinism run $i/$ITERATIONS ---"
  if OUTPUT=$("$SCRIPT_DIR/run-fixture.sh" "$FIXTURE" 2>&1); then
    RESULT_LINE=$(echo "$OUTPUT" | grep '^COMPARISON:' || echo "COMPARISON: (no result line found)")
    echo "$RESULT_LINE"
    if echo "$RESULT_LINE" | grep -q '^COMPARISON: PASS'; then
      PASS_COUNT=$((PASS_COUNT + 1))
    else
      FAIL_COUNT=$((FAIL_COUNT + 1))
      echo "$OUTPUT" | grep -A2 'MISMATCH' || true
    fi
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "run $i exited non-zero (script error or compare.mjs FAIL exit code)"
    echo "$OUTPUT" | tail -20
  fi
done

echo "=== [$FIXTURE] determinism summary: $PASS_COUNT/$ITERATIONS PASS, $FAIL_COUNT/$ITERATIONS FAIL ==="
[ "$FAIL_COUNT" -eq 0 ]
