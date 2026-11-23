#!/usr/bin/env bash
# Asserts scripts/check_no_nat.sh: the repo's terraform/ passes, and every negative fixture fails
# with the offending resource type named. Run from CI (terraform-edge job) and locally.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

CHECK=scripts/check_no_nat.sh
FIXTURES=tests/terraform/fixtures
failures=0

fail() {
  echo "FAIL: $*" >&2
  failures=$((failures + 1))
}

# The repo itself must pass.
if ! "${CHECK}" terraform >/dev/null 2>&1; then
  fail "check_no_nat rejects terraform/ (it must pass on the repo)"
fi

# Each fixture must fail (exit 1) and name its resource type.
expect_fail() {
  local dir=$1 type=$2 out rc=0
  out=$("${CHECK}" "${FIXTURES}/${dir}" 2>&1) || rc=$?
  if [ "${rc}" -ne 1 ]; then
    fail "${dir}: expected exit 1, got ${rc}"
  elif ! grep -q "${type}" <<<"${out}"; then
    fail "${dir}: output does not name ${type}"
  fi
}

expect_fail bad_nat google_compute_router_nat
expect_fail bad_connector google_vpc_access_connector

# A commented-out resource must not trip the check.
if ! "${CHECK}" "${FIXTURES}/ok_commented" >/dev/null 2>&1; then
  fail "ok_commented: a commented resource must not fail"
fi

# A missing directory is an error (exit 2), never a silent pass.
rc=0
"${CHECK}" "${FIXTURES}/does_not_exist" >/dev/null 2>&1 || rc=$?
if [ "${rc}" -ne 2 ]; then
  fail "missing directory: expected exit 2, got ${rc}"
fi

if [ "${failures}" -ne 0 ]; then
  echo "check_no_nat_test: ${failures} failure(s)" >&2
  exit 1
fi
echo "check_no_nat_test: ok"
