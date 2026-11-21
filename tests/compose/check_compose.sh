#!/usr/bin/env bash
# Acceptance checks for docker-compose.yml (E0-T11).
# Static checks (AC3, AC4) need only the docker CLI. Runtime checks (AC1, AC2, AC5, AC6) need a
# running Docker daemon and network, and run when RUNTIME=1 (default). Set RUNTIME=0 to skip them.
# Usage: tests/compose/check_compose.sh   (from any directory)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
RUNTIME="${RUNTIME:-1}"
PROJECT="${COMPOSE_PROJECT_NAME:-fa-compose-check}"
WORK="$(mktemp -d)"
ENV_FILE="$WORK/check.env"

fail() { echo "FAIL: $*" >&2; exit 1; }

cleanup() {
  if [ "$RUNTIME" = "1" ]; then
    docker compose -p "$PROJECT" --env-file "$ENV_FILE" down -v >/dev/null 2>&1 || true
    docker compose -p "${PROJECT}-other" --env-file "$ENV_FILE" down -v >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

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
images="$(docker compose --env-file "$ENV_FILE" config | grep -E 'image:')"
echo "$images" | grep -q 'pgvector/pgvector:0.8.5-pg16' || fail "pgvector pin missing"
echo "$images" | grep -q 'apache/kafka:4.4.0' || fail "kafka pin missing"
if echo "$images" | grep -Eqi 'redis|langfuse|litellm|kong'; then fail "removed image present: $images"; fi
echo "ok: images pinned, no redis/langfuse/litellm/kong"

# Host ports bind to 127.0.0.1 only.
if docker compose --env-file "$ENV_FILE" config | grep -E 'host_ip:' | grep -v '127.0.0.1'; then
  fail "a host port is not bound to 127.0.0.1"
fi
echo "ok: host ports bound to 127.0.0.1"

[ "$RUNTIME" = "1" ] || { echo "skipped runtime checks (RUNTIME=0)"; exit 0; }

# AC1, AC5: everything healthy, migrate exited 0, schema at 0001, probes answer.
docker compose -p "$PROJECT" --env-file "$ENV_FILE" up -d --wait || fail "up --wait failed"
ps="$(docker compose -p "$PROJECT" --env-file "$ENV_FILE" ps -a --format '{{.Service}} {{.State}} {{.Health}} {{.ExitCode}}')"
for svc in postgres kafka api frontend; do
  echo "$ps" | grep -q "^$svc running healthy" || fail "$svc not healthy: $ps"
done
echo "$ps" | grep -q "^migrate exited  0" || fail "migrate did not exit 0: $ps"
echo "ok: postgres, kafka, api, frontend healthy; migrate exited 0"

version="$(docker compose -p "$PROJECT" --env-file "$ENV_FILE" exec -T postgres \
  psql -U fa_check -d finadvisor -tAc "select version_num from alembic_version")"
[ "$version" = "0001" ] || fail "alembic_version is '$version'"
curl -fsS "http://127.0.0.1:38000/healthz" >/dev/null || fail "/healthz"
curl -fsS "http://127.0.0.1:38000/readyz" >/dev/null || fail "/readyz"
echo "ok: alembic 0001, /healthz and /readyz 200"

# AC2: a second project on other ports runs side by side.
sed -e 's/^POSTGRES_HOST_PORT=.*/POSTGRES_HOST_PORT=45432/' \
    -e 's/^KAFKA_HOST_PORT=.*/KAFKA_HOST_PORT=49092/' \
    -e 's/^API_HOST_PORT=.*/API_HOST_PORT=48000/' \
    -e 's/^FRONTEND_HOST_PORT=.*/FRONTEND_HOST_PORT=43000/' \
    "$ENV_FILE" > "$WORK/other.env"
docker compose -p "${PROJECT}-other" --env-file "$WORK/other.env" up -d --wait \
  || fail "second project failed to start"
curl -fsS "http://127.0.0.1:48000/healthz" >/dev/null || fail "second project /healthz"
docker compose -p "${PROJECT}-other" --env-file "$WORK/other.env" down -v >/dev/null
echo "ok: second project ran side by side"

# AC6: down -v leaves nothing.
docker compose -p "$PROJECT" --env-file "$ENV_FILE" down -v >/dev/null
left="$(docker compose -p "$PROJECT" --env-file "$ENV_FILE" ps -a -q)"
[ -z "$left" ] || fail "containers left after down -v: $left"
vols="$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")"
[ -z "$vols" ] || fail "volumes left after down -v: $vols"
echo "ok: down -v left no containers or volumes"
