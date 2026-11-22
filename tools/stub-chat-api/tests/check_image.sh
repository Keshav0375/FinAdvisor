#!/usr/bin/env bash
# Acceptance check for tools/stub-chat-api/Dockerfile (E2-T3). Needs a Docker daemon, network and curl.
# Usage: tools/stub-chat-api/tests/check_image.sh   (from any directory)
# Env: STUB_IMAGE_TAG (default fa-stub-chat-api:test), STUB_CONTAINER_PORT (default 8080),
#      STUB_READY_TIMEOUT_S (default 30).
set -euo pipefail

STUB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${STUB_IMAGE_TAG:-fa-stub-chat-api:test}"
PORT="${STUB_CONTAINER_PORT:-8080}"
READY_TIMEOUT_S="${STUB_READY_TIMEOUT_S:-30}"
NAME="fa-stub-$$"
CURL=(curl -sS --max-time 5)

fail() { echo "FAIL: $*" >&2; exit 1; }

# Every FROM is pinned by digest.
if grep -E '^FROM' "$STUB_DIR/Dockerfile" | grep -v '@sha256:'; then
  fail "FROM line without @sha256 digest"
fi
echo "ok: all FROM lines pinned by digest"

docker build -t "$TAG" "$STUB_DIR"

# --rm removes the container once it is stopped; the trap stops it on any exit.
trap 'docker stop "$NAME" >/dev/null 2>&1 || true' EXIT
docker run -d --rm --name "$NAME" -e "STUB_PORT=$PORT" -p "127.0.0.1:0:$PORT" "$TAG" >/dev/null
host_port="$(docker port "$NAME" "$PORT/tcp" | head -n 1 | sed 's/.*://')"
base="http://127.0.0.1:$host_port"

ready=0
for _ in $(seq 1 "$READY_TIMEOUT_S"); do
  if [ "$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$base/echo" 2>/dev/null || true)" = "200" ]; then
    ready=1
    break
  fi
  sleep 1
done
[ "$ready" = "1" ] || { docker logs "$NAME" >&2 || true; fail "/echo did not return 200 within ${READY_TIMEOUT_S}s"; }
echo "ok: docker run serves /echo with HTTP 200"

# /echo never returns the raw Authorization value.
body="$("${CURL[@]}" -H 'Authorization: Bearer SENTINEL_AAA' "$base/echo")"
echo "$body" | grep -q '"sha256"' || fail "/echo has no sha256 for Authorization: $body"
if echo "$body" | grep -q 'SENTINEL_AAA'; then fail "/echo leaked the raw Authorization value"; fi
echo "ok: /echo hides the raw Authorization value"

# /sse streams the requested number of events.
sse="$("${CURL[@]}" "$base/sse?events=3&interval_ms=10")"
count="$(echo "$sse" | grep -c '^id: ')"
[ "$count" = "3" ] || fail "/sse sent $count events, expected 3: $sse"
echo "ok: /sse streams 3 events"

# The identity mock returns an unsigned JWT that /echo decodes back to the audience.
token="$("${CURL[@]}" "$base/metadata-mock/computeMetadata/v1/instance/service-accounts/default/identity?audience=https://x")"
[ "$(echo "$token" | tr -cd '.' | wc -c | tr -d ' ')" = "2" ] || fail "identity token is not a JWT: $token"
echo "$("${CURL[@]}" -H "X-Serverless-Authorization: Bearer $token" "$base/echo")" | grep -q '"aud":"https://x"' \
  || fail "/echo did not decode aud from the identity token"
echo "ok: identity mock token decodes to aud https://x"

# Non-root.
uid="$(docker exec "$NAME" id -u)"
[ "$uid" != "0" ] || fail "container runs as root"
echo "ok: non-root"
