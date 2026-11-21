#!/usr/bin/env bash
# Acceptance checks for docker-compose.yml (E0-T11).
# Static checks (AC3, AC4) need only the docker CLI. Runtime checks (AC1, AC2, AC5, AC6) need a
# running Docker daemon, network and python3, and run when RUNTIME=1 (default). RUNTIME=0 skips them.
# Knobs: COMPOSE_PROJECT_NAME (default fa-compose-check), COMPOSE_WAIT_TIMEOUT_S (default 300).
# Usage: tests/compose/check_compose.sh   (from any directory)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
RUNTIME="${RUNTIME:-1}"
PROJECT="${COMPOSE_PROJECT_NAME:-fa-compose-check}"
WAIT_TIMEOUT_S="${COMPOSE_WAIT_TIMEOUT_S:-300}"
WORK="$(mktemp -d)"
ENV_FILE="$WORK/check.env"
STRAY_PIDS=()

fail() { echo "FAIL: $*" >&2; exit 1; }

cleanup() {
  if [ "${#STRAY_PIDS[@]}" -gt 0 ]; then
    for pid in "${STRAY_PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  fi
  if [ "$RUNTIME" = "1" ]; then
    docker compose -p "$PROJECT" --env-file "$ENV_FILE" down -v >/dev/null 2>&1 || true
    docker compose -p "${PROJECT}-other" --env-file "$ENV_FILE" down -v >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

compose() { docker compose -p "$PROJECT" --env-file "$ENV_FILE" "$@"; }

# Throwaway, non-secret values built from the committed example.
sed -e 's/^DB_USER=.*/DB_USER=fa_check/' \
    -e 's/^DB_PASSWORD=.*/DB_PASSWORD=check-unused-db-pass/' \
    -e 's/^JWT_SECRET=.*/JWT_SECRET=check-unused-jwt-secret-0123456789-abcdefghij/' \
    -e 's/=REPLACE_ME/=check-unused-value/' \
    -e 's/^POSTGRES_HOST_PORT=.*/POSTGRES_HOST_PORT=35432/' \
    -e 's/^KAFKA_HOST_PORT=.*/KAFKA_HOST_PORT=39092/' \
    -e 's/^API_HOST_PORT=.*/API_HOST_PORT=38000/' \
    -e 's/^FRONTEND_HOST_PORT=.*/FRONTEND_HOST_PORT=33000/' \
    .env.example > "$ENV_FILE"

# AC3: DB_PASSWORD unset fails with the message, no secret default.
grep -v '^DB_PASSWORD=' "$ENV_FILE" > "$WORK/nopass.env"
if out="$(env -u DB_PASSWORD docker compose --env-file "$WORK/nopass.env" config 2>&1)"; then
  fail "compose config succeeded without DB_PASSWORD"
fi
echo "$out" | grep -q "set DB_PASSWORD" || fail "no 'set DB_PASSWORD' message: $out"
echo "ok: missing DB_PASSWORD fails with a message"

# AC4: pinned images, none of the removed ones.
images="$(compose config | grep -E 'image:')"
echo "$images" | grep -q 'pgvector/pgvector:0.8.5-pg16' || fail "pgvector pin missing"
echo "$images" | grep -q 'apache/kafka:4.4.0' || fail "kafka pin missing"
if echo "$images" | grep -Eqi 'redis|langfuse|litellm|kong'; then fail "removed image present: $images"; fi
echo "ok: images pinned, no redis/langfuse/litellm/kong"

# Host ports bind to 127.0.0.1 only.
if compose config | grep -E 'host_ip:' | grep -v '127.0.0.1'; then
  fail "a host port is not bound to 127.0.0.1"
fi
echo "ok: host ports bound to 127.0.0.1"

[ "$RUNTIME" = "1" ] || { echo "skipped runtime checks (RUNTIME=0)"; exit 0; }

# Reads `compose ps -a --format json` (one object per line, or one array) on stdin and checks that
# the named services are running and healthy and that migrate exited with ExitCode 0.
check_states() {
  python3 -c '
import json, sys
text = sys.stdin.read().strip()
rows = json.loads(text) if text.startswith("[") else [json.loads(l) for l in text.splitlines() if l]
by = {r["Service"]: r for r in rows}
errors = []
for svc in ("postgres", "kafka", "api", "frontend"):
    r = by.get(svc, {})
    if r.get("State") != "running" or r.get("Health") != "healthy":
        errors.append("%s: State=%s Health=%s" % (svc, r.get("State"), r.get("Health")))
m = by.get("migrate", {})
if m.get("State") != "exited" or m.get("ExitCode") != 0:
    errors.append("migrate: State=%s ExitCode=%s" % (m.get("State"), m.get("ExitCode")))
if errors:
    print("; ".join(errors)); sys.exit(1)
'
}

# AC2: stray listeners on the default host ports. The stack must come up on the configured
# *_HOST_PORT values instead and leave the strays untouched.
for port in 5432 8000 3000 9092; do
  python3 -m http.server "$port" --bind 127.0.0.1 --directory "$WORK" >/dev/null 2>&1 &
  STRAY_PIDS+=("$!")
  up=0
  for _ in $(seq 1 50); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then up=1; break; fi
    sleep 0.1
  done
  [ "$up" = "1" ] || fail "could not open a stray listener on $port (port already in use?)"
done
echo "ok: stray listeners on 5432, 8000, 3000, 9092"

# AC1, AC5: everything healthy, migrate exited 0, schema at 0001, probes answer.
compose up -d --wait --wait-timeout "$WAIT_TIMEOUT_S" || fail "up --wait failed with stray listeners"
states="$(compose ps -a --format json)"
printf '%s\n' "$states" | check_states || fail "service states wrong: $(printf '%s\n' "$states" | check_states 2>&1 || true)"
echo "ok: postgres, kafka, api, frontend healthy; migrate exited 0 (stack up beside stray listeners)"
for pid in "${STRAY_PIDS[@]}"; do kill -0 "$pid" 2>/dev/null || fail "stray listener $pid died"; done
echo "ok: stray listeners untouched"

version="$(compose exec -T postgres psql -U fa_check -d finadvisor -tAc "select version_num from alembic_version")"
[ "$version" = "0001" ] || fail "alembic_version is '$version'"
curl -fsS "http://127.0.0.1:38000/healthz" >/dev/null || fail "/healthz"
curl -fsS "http://127.0.0.1:38000/readyz" >/dev/null || fail "/readyz"
echo "ok: alembic 0001, /healthz and /readyz 200"

# A second project on other ports runs side by side.
sed -e 's/^POSTGRES_HOST_PORT=.*/POSTGRES_HOST_PORT=45432/' \
    -e 's/^KAFKA_HOST_PORT=.*/KAFKA_HOST_PORT=49092/' \
    -e 's/^API_HOST_PORT=.*/API_HOST_PORT=48000/' \
    -e 's/^FRONTEND_HOST_PORT=.*/FRONTEND_HOST_PORT=43000/' \
    "$ENV_FILE" > "$WORK/other.env"
docker compose -p "${PROJECT}-other" --env-file "$WORK/other.env" up -d --wait \
  --wait-timeout "$WAIT_TIMEOUT_S" || fail "second project failed to start"
curl -fsS "http://127.0.0.1:48000/healthz" >/dev/null || fail "second project /healthz"
docker compose -p "${PROJECT}-other" --env-file "$WORK/other.env" down -v >/dev/null
echo "ok: second project ran side by side"

# AC6: down -v leaves nothing.
compose down -v >/dev/null
left="$(compose ps -a -q)"
[ -z "$left" ] || fail "containers left after down -v: $left"
vols="$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")"
[ -z "$vols" ] || fail "volumes left after down -v: $vols"
echo "ok: down -v left no containers or volumes"
