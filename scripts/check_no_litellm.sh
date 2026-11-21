#!/usr/bin/env bash
# Fails when LiteLLM is referenced by a tracked file outside the allowlist, or when the backend
# dependency files mention it (ADR-005: the backend calls Anthropic directly).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Historical records, this script and its test keep the word on purpose.
# .claude/settings.local.json is a local tool-permission file, not project code.
ALLOWLIST=(
  'Story-Reports/'
  'TODO.md'
  'mvp.md'
  'architecture.md'
  'docs/threat_model.md'
  'scripts/check_no_litellm.sh'
  'backend/tests/test_check_no_litellm.py'
  '.claude/settings.local.json'
)

excludes=()
for entry in "${ALLOWLIST[@]}"; do
  excludes+=(":(exclude)${entry}")
done

status=0

# git grep exits 0 on a match, 1 on none, and above 1 on an error, which must not pass silently.
grep_status=0
matches=$(git grep -il litellm -- . "${excludes[@]}") || grep_status=$?
if [ "${grep_status}" -eq 0 ]; then
  echo "check_no_litellm: LiteLLM referenced outside the allowlist:" >&2
  echo "${matches}" >&2
  status=1
elif [ "${grep_status}" -ne 1 ]; then
  echo "check_no_litellm: git grep failed (exit ${grep_status})" >&2
  exit 2
fi

for dependency_file in backend/poetry.lock backend/pyproject.toml; do
  if [ -f "${dependency_file}" ] && grep -qi litellm "${dependency_file}"; then
    echo "check_no_litellm: ${dependency_file} mentions litellm" >&2
    status=1
  fi
done

exit "${status}"
