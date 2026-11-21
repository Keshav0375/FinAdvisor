#!/usr/bin/env bash
# Acceptance checks for backend/Dockerfile (E0-T2, E0-T9). Needs a running Docker daemon and network.
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
actual="$(docker run --rm --entrypoint "" "$RUNTIME_TAG" python -c "import fastapi,anthropic,langfuse,importlib.metadata as m; print(m.version('fastapi'), m.version('anthropic'), m.version('langfuse'))")"
[ "$actual" = "$expected" ] || fail "versions '$actual' != lock '$expected'"
echo "ok: versions match lock ($actual)"

# Dev has pytest, runtime does not.
docker run --rm --entrypoint "" "$DEV_TAG" pytest --version >/dev/null || fail "pytest missing in dev"

# The full suite runs in the dev image with no --ignore; a collection error fails the check.
# test_settings reads the repo-root .env.example, which is outside the build context; it is
# mounted read-only at the path the test resolves from /app (the filesystem root).
REPO_ROOT="$(cd "$BACKEND_DIR/.." && pwd)"
if ! out="$(docker run --rm --entrypoint "" -v "$REPO_ROOT/.env.example:/.env.example:ro" \
  "$DEV_TAG" pytest tests/ -p no:cacheprovider 2>&1)"; then
  echo "$out" >&2
  if echo "$out" | grep -qE 'error(s)? during collection|ERROR collecting'; then
    fail "pytest collection error in dev image"
  fi
  fail "pytest tests/ failed in dev image"
fi
echo "$out" | tail -n 1
echo "ok: full test suite passes in dev image"
if docker run --rm --entrypoint "" "$RUNTIME_TAG" pytest --version >/dev/null 2>&1; then fail "pytest present in runtime"; fi
echo "ok: pytest in dev only"

# Non-root in both images.
for tag in "$RUNTIME_TAG" "$DEV_TAG"; do
  uid="$(docker run --rm --entrypoint "" "$tag" id -u)"
  [ "$uid" != "0" ] || fail "$tag runs as root"
done
echo "ok: non-root"

# A dependency added to pyproject.toml without re-locking fails `poetry check --lock`.
# Runs in a throwaway container, so the edit is discarded with it.
if out="$(docker run --rm --entrypoint "" "$BUILDER_TAG" sh -c \
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
        found = subprocess.run(["docker", "run", "--rm", "--entrypoint", "", tag, *find_excluded],
                               check=True, capture_output=True, text=True).stdout.strip()
        if found:
            leaked = True
            print(f"{target} image contains files .dockerignore should exclude:\n{found}", file=sys.stderr)
sys.exit(1 if leaked else 0)
PY_EOF
echo "ok: nested .env*, *.pem, *.key, *.pyc excluded from runtime and dev images"

# Entrypoints (E0-T9). Commands below run without --entrypoint "" so the image ENTRYPOINT applies.
rc=0
docker run --rm "$RUNTIME_TAG" bogus >/dev/null 2>&1 || rc=$?
[ "$rc" = "2" ] || fail "'bogus' exited $rc, expected 2"
echo "ok: unknown entrypoint command exits 2"

# `jobs migrate` on an empty database in a throwaway pinned Postgres (published on a free port,
# reached through the host gateway). The readiness probe goes over TCP: during first-start
# initialisation Postgres only listens on its socket, so pg_isready would report ready too early.
# --rm removes the container once it is stopped.
PG_IMAGE="${PG_IMAGE:-pgvector/pgvector:0.8.5-pg16}"
PG_CONTAINER="fa-check-pg-$$"
trap 'docker stop "$PG_CONTAINER" >/dev/null 2>&1 || true' EXIT
docker run -d --rm --name "$PG_CONTAINER" -e POSTGRES_PASSWORD=check-pass -p 127.0.0.1::5432 "$PG_IMAGE" >/dev/null
for _ in $(seq 1 60); do
  docker exec "$PG_CONTAINER" psql -h 127.0.0.1 -U postgres -Atc 'SELECT 1' >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$PG_CONTAINER" psql -h 127.0.0.1 -U postgres -Atc 'SELECT 1' >/dev/null 2>&1 || fail "throwaway Postgres did not start"
docker exec "$PG_CONTAINER" psql -U postgres -c 'CREATE DATABASE finadvisor_check' >/dev/null
pg_port="$(docker port "$PG_CONTAINER" 5432/tcp | head -n 1 | sed 's/.*://')"
migrate_env=(-e DB_HOST=host.docker.internal -e "DB_PORT=$pg_port" -e DB_NAME=finadvisor_check
  -e DB_USER=postgres -e DB_PASSWORD=check-pass -e DB_POOL_SIZE=5 -e DB_STATEMENT_TIMEOUT_MS=30000
  -e ANTHROPIC_API_KEY=check-anthropic-key -e VOYAGE_API_KEY=check-voyage-key
  -e JWT_SECRET=check-jwt-secret-0123456789-abcdefghij)
for attempt in 1 2; do
  if ! out="$(docker run --rm --add-host host.docker.internal:host-gateway "${migrate_env[@]}" \
    "$RUNTIME_TAG" jobs migrate 2>&1)"; then
    echo "$out" >&2
    fail "jobs migrate attempt $attempt failed"
  fi
done
version="$(docker exec "$PG_CONTAINER" psql -U postgres -d finadvisor_check -Atc 'SELECT version_num FROM alembic_version')"
[ "$version" = "0001" ] || fail "alembic_version is '$version', expected 0001"
echo "ok: jobs migrate exits 0 on an empty database, twice, at revision 0001"
