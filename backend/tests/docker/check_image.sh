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

# .dockerignore keeps secret-like and bytecode files out at any depth, not just the context root.
# The build runs from a copy of backend/ in a temporary directory with dummy nested files added;
# Python's TemporaryDirectory removes the copy, so the host tree is never touched. A local
# directory is required: BuildKit does not apply .dockerignore to a tar context streamed on stdin.
# .venv and tool caches are left out of the copy for size only; .dockerignore drops them anyway.
IGNORE_TAG_PREFIX="${IGNORE_TAG_PREFIX:-fa-backend:test-ignore}"
python3 -I - "$BACKEND_DIR" "$IGNORE_TAG_PREFIX" <<'PY_EOF' || fail ".dockerignore lets nested excluded files into an image"
import pathlib, shutil, subprocess, sys, tempfile
backend, tag_prefix = pathlib.Path(sys.argv[1]), sys.argv[2]
planted = [
    "src/.env", "src/.env.local", "src/nested/deep.pem", "src/nested/deep.key",
    "scripts/.env.prod", "db/fixtures/x.pem", "tests/fixtures/x.pem", "tests/fixtures/x.key",
    "tests/fixtures/.env.test", "src/__pycache__/stale.cpython-312.pyc", "tests/stale.pyc",
]
find_excluded = ["find", "/app", "(", "-name", ".env", "-o", "-name", ".env.*", "-o", "-name", "*.pem",
                 "-o", "-name", "*.key", "-o", "-name", "*.pyc", "-o", "-name", "__pycache__", ")", "-print"]
leaked = False
with tempfile.TemporaryDirectory(prefix="fa-ignore-ctx-") as tmp:
    ctx = pathlib.Path(tmp) / "backend"
    shutil.copytree(backend, ctx, ignore=shutil.ignore_patterns(".venv", ".mypy_cache", ".ruff_cache", ".pytest_cache"))
    for name in planted:
        path = ctx / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("dummy-not-a-secret\n")
    for target in ("runtime", "dev"):
        tag = f"{tag_prefix}-{target}"
        subprocess.run(["docker", "build", "--quiet", "--target", target, "-t", tag, str(ctx)],
                       check=True, stdout=subprocess.DEVNULL)
        found = subprocess.run(["docker", "run", "--rm", tag, *find_excluded],
                               check=True, capture_output=True, text=True).stdout.strip()
        if found:
            leaked = True
            print(f"{target} image contains files .dockerignore should exclude:\n{found}", file=sys.stderr)
sys.exit(1 if leaked else 0)
PY_EOF
echo "ok: nested .env*, *.pem, *.key, *.pyc excluded from runtime and dev images"
