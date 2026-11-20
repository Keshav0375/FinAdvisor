#!/usr/bin/env bash
# Acceptance checks for backend/Dockerfile (E0-T2). Needs a running Docker daemon and network.
# Usage: backend/tests/docker/check_image.sh   (from any directory)
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
RUNTIME_TAG="${RUNTIME_TAG:-fa-backend:test}"
DEV_TAG="${DEV_TAG:-fa-backend:test-dev}"
BUILDER_TAG="${BUILDER_TAG:-fa-backend:test-builder}"

fail() { echo "FAIL: $*" >&2; exit 1; }

# Every FROM is pinned by digest.
if grep -E '^FROM' "$BACKEND_DIR/Dockerfile" | grep -v '@sha256:'; then
  fail "FROM line without @sha256 digest"
fi
echo "ok: all FROM lines pinned by digest"

docker build --target runtime -t "$RUNTIME_TAG" "$BACKEND_DIR"
docker build --target dev -t "$DEV_TAG" "$BACKEND_DIR"
docker build --target builder -t "$BUILDER_TAG" "$BACKEND_DIR"

# Installed versions equal the lock versions.
lock_version() {
  awk -v n="$1" '$0=="name = \"" n "\""{f=1;next} f&&/^version = /{gsub(/"/,"",$3);print $3;exit}' "$BACKEND_DIR/poetry.lock"
}
expected="$(lock_version fastapi) $(lock_version anthropic) $(lock_version langfuse)"
actual="$(docker run --rm "$RUNTIME_TAG" python -c "import fastapi,anthropic,langfuse,importlib.metadata as m; print(m.version('fastapi'), m.version('anthropic'), m.version('langfuse'))")"
[ "$actual" = "$expected" ] || fail "versions '$actual' != lock '$expected'"
echo "ok: versions match lock ($actual)"

# Dev has pytest, runtime does not.
docker run --rm "$DEV_TAG" pytest --version >/dev/null || fail "pytest missing in dev"

# The full suite runs in the dev image with no --ignore; a collection error fails the check.
if ! out="$(docker run --rm "$DEV_TAG" pytest tests/ -p no:cacheprovider 2>&1)"; then
  echo "$out" >&2
  if echo "$out" | grep -qE 'error(s)? during collection|ERROR collecting'; then
    fail "pytest collection error in dev image"
  fi
  fail "pytest tests/ failed in dev image"
fi
echo "$out" | tail -n 1
echo "ok: full test suite passes in dev image"
if docker run --rm "$RUNTIME_TAG" pytest --version >/dev/null 2>&1; then fail "pytest present in runtime"; fi
echo "ok: pytest in dev only"

# Non-root in both images.
for tag in "$RUNTIME_TAG" "$DEV_TAG"; do
  uid="$(docker run --rm "$tag" id -u)"
  [ "$uid" != "0" ] || fail "$tag runs as root"
done
echo "ok: non-root"

# A dependency added to pyproject.toml without re-locking fails `poetry check --lock`.
# Runs in a throwaway container, so the edit is discarded with it.
if out="$(docker run --rm "$BUILDER_TAG" sh -c \
  "sed -i 's/^pydantic-settings = /rich = \">=13.0.0\"\npydantic-settings = /' pyproject.toml && poetry check --lock" 2>&1)"; then
  fail "poetry check --lock passed with a stale lock"
fi
echo "$out" | grep -qi "lock" || fail "stale-lock check failed for another reason: $out"
echo "ok: stale lock fails poetry check --lock"
