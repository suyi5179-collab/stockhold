#!/usr/bin/env bash
# Prove the correctness tests can fail: run them with each protection on, then switched off.
# Needs the stack (docker-compose -p stockhold up -d --wait && ./scripts/deploy.sh). Writes docs/results/failure_controls.raw.txt.
set -uo pipefail
cd "$(dirname "$0")/.."
OUT=docs/results/failure_controls.raw.txt
TESTS="tests/test_reserve.py::test_concurrent_checkouts_never_oversell tests/test_idempotency.py::test_duplicate_payment_failures_release_once"
run() {
  echo "### STOCKHOLD_SABOTAGE=${1:-(none)}"
  STOCKHOLD_SABOTAGE="$1" .venv/bin/pytest $TESTS -p no:cacheprovider -rf 2>&1 | grep -E "^(E  |FAILED|[0-9]+ (passed|failed))|passed|failed" | grep -v "^$"
  echo
}
{
  echo "# $(date -u +%Y-%m-%dT%H:%M:%SZ)  $(git rev-parse --short HEAD)"
  run ""
  run no_condition
  run no_release_guard
  run ""
} | tee "$OUT"
