#!/usr/bin/env bash
# E2-T1 spike harness: X-RL-Key = sha256(Authorization) set by pre-function, rate-limiting keyed on
# it against Valkey, on Kong 3.9.4 DB-less. Scratch only, never shipped. Everything is local.
#
#   bash spikes/e2/xrlkey/run.sh | tee spikes/e2/xrlkey/out.txt
#
# Containers: valkey, the E2-T3 stub-chat-api (upstream) and kong share the valkey container's
# network namespace (--network container:), so every address is 127.0.0.1 and the harness creates
# no network object. Only valkey publishes a port (Kong's proxy 8000, on an ephemeral host port).
# Every container is started with -d --rm and stopped with docker stop in the EXIT trap.
#
# Knobs (environment, defaults in brackets):
#   XRL_KONG_IMAGE [kong:3.9.4]  XRL_VALKEY_IMAGE [valkey/valkey:8.0]
#   XRL_STUB_IMAGE [fa-stub-chat-api:e2t1, built from tools/stub-chat-api]
#   XRL_HOST_PORT [empty = ephemeral]  XRL_WAIT_S [60] readiness wait per container
#   XRL_WINDOW_GUARD_S [50] a block that starts later than this second of the minute waits for
#   the next minute, so a 3/min bucket cannot be split by a minute boundary.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
KONG_IMAGE="${XRL_KONG_IMAGE:-kong:3.9.4}"
VALKEY_IMAGE="${XRL_VALKEY_IMAGE:-valkey/valkey:8.0}"
STUB_IMAGE="${XRL_STUB_IMAGE:-fa-stub-chat-api:e2t1}"
HOST_PORT="${XRL_HOST_PORT:-}"
WAIT_S="${XRL_WAIT_S:-60}"
WINDOW_GUARD_S="${XRL_WINDOW_GUARD_S:-50}"

RUN_ID="fa-e2t1-$$"
VALKEY="$RUN_ID-valkey"
STUB="$RUN_ID-stub"
KONGS=()
WORK="$(mktemp -d)"
PORT=""
PASS=0
FAIL=0

# Demo credentials only. Never real tokens.
TOKEN_A="Bearer demo-token-alpha"
TOKEN_B="Bearer demo-token-bravo"
TOKEN_C="Bearer demo-token-charlie"
TOKEN_D="Bearer demo-token-delta"
TOKEN_READY="Bearer demo-token-ready"

cleanup() {
  local c
  for c in ${KONGS[@]+"${KONGS[@]}"}; do docker stop "$c" >/dev/null 2>&1 || true; done
  docker stop "$STUB" >/dev/null 2>&1 || true
  docker stop "$VALKEY" >/dev/null 2>&1 || true
}
trap cleanup EXIT

say() { printf '%s\n' "$*"; }
section() { printf '\n== %s ==\n' "$*"; }
check() { # check <description> <0|1 condition result>
  if [ "$2" -eq 0 ]; then
    PASS=$((PASS + 1)); say "PASS $1"
  else
    FAIL=$((FAIL + 1)); say "FAIL $1"
  fi
}

sha256_of() { printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1; }
vcli() { docker exec "$VALKEY" valkey-cli "$@"; }

wait_for() { # wait_for <description> <command...>: poll once a second up to WAIT_S
  local what="$1" i
  shift
  for ((i = 0; i < WAIT_S; i++)); do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  say "TIMEOUT waiting for $what"
  return 1
}

stub_healthy() { [ "$(docker inspect -f '{{.State.Health.Status}}' "$STUB")" = "healthy" ]; }
kong_answers() { # any HTTP answer (not a refused connection) on the proxy port
  [ "$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: $TOKEN_READY" "http://127.0.0.1:$PORT/chat")" != "000" ]
}

start_kong() { # start_kong <container> <config file> [extra docker args...]
  local name="$1" config="$2"
  shift 2
  KONGS+=("$name")
  docker run -d --rm --name "$name" --network "container:$VALKEY" \
    -v "$config:/k/kong.yml:ro" \
    -e KONG_DATABASE=off -e KONG_DECLARATIVE_CONFIG=/k/kong.yml \
    -e KONG_PROXY_LISTEN=0.0.0.0:8000 -e KONG_ADMIN_LISTEN=off \
    "$@" "$KONG_IMAGE" >/dev/null
}

stop_kong() { docker stop "$1" >/dev/null; }

align_window() { # do not start a 3/min block in the last seconds of a minute
  local s
  s=$((10#$(date +%S)))
  if [ "$s" -ge "$WINDOW_GUARD_S" ]; then
    say "(waiting $((61 - s))s for the next minute window)"
    sleep $((61 - s))
  fi
}

# send <token|-> [extra curl args...]: prints "<status>" and keeps headers/body in $WORK
send() {
  local token="$1"
  shift
  local auth=()
  if [ "$token" != "-" ]; then auth=(-H "Authorization: $token"); fi
  curl -s -o "$WORK/body" -D "$WORK/headers" -w '%{http_code}' \
    ${auth[@]+"${auth[@]}"} "$@" "http://127.0.0.1:$PORT/chat"
}

echoed_key() { # the x-rl-key the upstream saw, empty when absent
  grep -o '"x-rl-key":"[^"]*"' "$WORK/body" | head -1 | cut -d'"' -f4 || true
}

dump_valkey() { # dump_valkey <file>: every key with TYPE, GET and TTL
  local key
  : >"$1"
  while IFS= read -r key; do
    [ -n "$key" ] || continue
    printf 'KEY %s\n  TYPE %s\n  GET %s\n  TTL %s\n' "$key" "$(vcli TYPE "$key")" "$(vcli GET "$key")" "$(vcli TTL "$key")" >>"$1"
  done < <(vcli --scan)
}

count_in() { # count_in <literal> <file>: grep -c, 0 when absent (grep exits 1 then)
  grep -c -F -- "$1" "$2" || true
}

# ---------------------------------------------------------------------------------------------
section "setup"
say "kong=$KONG_IMAGE valkey=$VALKEY_IMAGE stub=$STUB_IMAGE"
docker build -q -t "$STUB_IMAGE" "$REPO_ROOT/tools/stub-chat-api" >/dev/null
docker run -d --rm --name "$VALKEY" -p "127.0.0.1:${HOST_PORT}:8000" "$VALKEY_IMAGE" >/dev/null
docker run -d --rm --name "$STUB" --network "container:$VALKEY" "$STUB_IMAGE" >/dev/null
wait_for "valkey" vcli ping
wait_for "stub-chat-api healthy" stub_healthy
PORT="$(docker port "$VALKEY" 8000/tcp | head -1)"
PORT="${PORT##*:}"
say "valkey: $(vcli INFO server | grep -E '^(valkey_version|redis_version)' | tr -d '\r' | tr '\n' ' ')"

# ---------------------------------------------------------------------------------------------
section "negative control 1: sync_rate 0 (the arch value) against Kong 3.9.4"
sed 's/sync_rate: -1/sync_rate: 0/' "$HERE/kong.yml" >"$WORK/kong-sync0.yml"
# Foreground docker run in the background shell, so its output survives --rm; Kong must exit on its own.
docker run --rm --name "$RUN_ID-kong-sync0" --network "container:$VALKEY" \
  -v "$WORK/kong-sync0.yml:/k/kong.yml:ro" \
  -e KONG_DATABASE=off -e KONG_DECLARATIVE_CONFIG=/k/kong.yml \
  -e KONG_PROXY_LISTEN=0.0.0.0:8000 -e KONG_ADMIN_LISTEN=off \
  "$KONG_IMAGE" >"$WORK/sync0.log" 2>&1 &
sync0_pid=$!
KONGS+=("$RUN_ID-kong-sync0")
for ((i = 0; i < WAIT_S; i++)); do
  if ! kill -0 "$sync0_pid" 2>/dev/null; then break; fi
  sleep 1
done
grep -m1 'sync_rate must be' "$WORK/sync0.log" || say "(no sync_rate message captured)"
rc=1
if grep -q 'sync_rate must be greater than 0.02, or -1 to disable' "$WORK/sync0.log"; then rc=0; fi
check "Kong rejects sync_rate: 0 at startup; synchronous Valkey writes need sync_rate: -1" "$rc"
docker stop "$RUN_ID-kong-sync0" >/dev/null 2>&1 || true
wait "$sync0_pid" 2>/dev/null || true

# ---------------------------------------------------------------------------------------------
section "(b) pre-function sandbox, default settings: require resty.sha256"
start_kong "$RUN_ID-kong-default" "$HERE/kong.yml"
wait_for "kong (default sandbox)" kong_answers
code="$(send "$TOKEN_A")"
say "status with the default sandbox: $code"
docker logs "$RUN_ID-kong-default" 2>&1 | grep -m1 "not allowed within sandbox" | sed 's/, client:.*//' || true
rc=1
if [ "$code" = "500" ] && docker logs "$RUN_ID-kong-default" 2>&1 | grep -q "require 'resty.sha256' not allowed within sandbox"; then rc=0; fi
check "default sandbox refuses require 'resty.sha256' (request fails with 500)" "$rc"
stop_kong "$RUN_ID-kong-default"

section "(b) pre-function sandbox: KONG_UNTRUSTED_LUA_SANDBOX_REQUIRES=resty.sha256,resty.string"
start_kong "$RUN_ID-kong" "$HERE/kong.yml" -e KONG_UNTRUSTED_LUA_SANDBOX_REQUIRES=resty.sha256,resty.string
wait_for "kong (sandbox requires)" kong_answers
KONG="$RUN_ID-kong"
code="$(send "$TOKEN_READY")"
rc=1
if [ "$code" != "500" ]; then rc=0; fi
check "with the two requires allowed the pre-function runs (status $code, not 500)" "$rc"

# ---------------------------------------------------------------------------------------------
section "(a) plugin order"
docker exec "$KONG" grep -nE 'PRIORITY|\(1000000\)' /usr/local/share/lua/5.1/kong/plugins/pre-function/handler.lua \
  /usr/local/share/lua/5.1/kong/plugins/rate-limiting/handler.lua | sed 's#/usr/local/share/lua/5.1/kong/plugins/##'
docker exec "$KONG" grep -n 'pre-function\|rate-limiting' /usr/local/share/lua/5.1/kong/constants.lua | head -3 || true
say "Access phase runs plugins by descending PRIORITY, a constant in each plugin's handler.lua."

# ---------------------------------------------------------------------------------------------
section "AC1: 3/min per Authorization"
vcli FLUSHALL >/dev/null
align_window
statuses=""
for _ in 1 2 3 4; do statuses="$statuses $(send "$TOKEN_A")"; done
say "same token, 4 requests:$statuses"
retry_after="$(grep -i '^retry-after:' "$WORK/headers" | tr -d '\r' || true)"
say "4th response header: ${retry_after:-<none>}"
rc=1
if [ "$statuses" = " 200 200 200 429" ]; then rc=0; fi
check "4th same-Authorization request is 429, first three are 200" "$rc"
rc=1
if [ -n "$retry_after" ]; then rc=0; fi
check "429 carries Retry-After" "$rc"
code="$(send "$TOKEN_B")"
rc=1
if [ "$code" = "200" ]; then rc=0; fi
check "different Authorization returns 200 (got $code)" "$rc"

# ---------------------------------------------------------------------------------------------
section "AC2: Valkey holds no raw bearer, keys hold the sha256"
vcli FLUSHALL >/dev/null
align_window
for _ in 1 2; do send "$TOKEN_A" >/dev/null; send "$TOKEN_B" >/dev/null; done
dump_valkey "$WORK/valkey-dump.txt"
cat "$WORK/valkey-dump.txt"
say "keys: $(vcli DBSIZE)"
for token in "$TOKEN_A" "$TOKEN_B"; do
  hex="$(sha256_of "$token")"
  raw_full="$(count_in "$token" "$WORK/valkey-dump.txt")"
  raw_bare="$(count_in "${token#Bearer }" "$WORK/valkey-dump.txt")"
  in_key="$(count_in "$hex" "$WORK/valkey-dump.txt")"
  say "token ${token#Bearer }: grep -c raw 'Bearer ...'=$raw_full, raw secret=$raw_bare, sha256 $hex in dump=$in_key"
  rc=1
  if [ "$raw_full" = "0" ] && [ "$raw_bare" = "0" ]; then rc=0; fi
  check "raw bearer for ${token#Bearer } occurs 0 times in the Valkey dump" "$rc"
  rc=1
  if [ "$in_key" -ge 1 ]; then rc=0; fi
  check "a Valkey key contains the 64-hex sha256 of ${token#Bearer }" "$rc"
done

# ---------------------------------------------------------------------------------------------
section "AC3: client-sent X-RL-Key is overwritten"
vcli FLUSHALL >/dev/null
align_window
seen=()
bad_echo=0
for round in 1 2 3 4; do
  line="round $round:"
  for token in "$TOKEN_A" "$TOKEN_B" "$TOKEN_C" "$TOKEN_D"; do
    code="$(send "$token" -H 'X-RL-Key: fixed')"
    line="$line $code"
    seen[round]="${seen[round]:-} $code"
    if [ "$code" = "200" ] && [ "$(echoed_key)" != "$(sha256_of "$token")" ]; then bad_echo=1; fi
  done
  say "$line"
done
rc=1
if [ "${seen[1]}" = " 200 200 200 200" ] && [ "${seen[2]}" = " 200 200 200 200" ] && [ "${seen[3]}" = " 200 200 200 200" ] && [ "${seen[4]}" = " 429 429 429 429" ]; then rc=0; fi
check "four tokens sending X-RL-Key: fixed keep their own bucket (200 x3, then 429 each)" "$rc"
check "the upstream sees X-RL-Key = sha256 of the token, never 'fixed'" "$bad_echo"
dump_valkey "$WORK/valkey-dump3.txt"
say "keys: $(vcli DBSIZE) (expected 4)"
rc=1
if [ "$(vcli DBSIZE)" = "4" ] && [ "$(count_in "$(sha256_of fixed)" "$WORK/valkey-dump3.txt")" = "0" ] && [ "$(count_in ":fixed:" "$WORK/valkey-dump3.txt")" = "0" ]; then rc=0; fi
check "Valkey has 4 keys and none was built from the client value" "$rc"

# ---------------------------------------------------------------------------------------------
section "AC4: no Authorization header (measured)"
vcli FLUSHALL >/dev/null
align_window
code="$(send - -H 'X-RL-Key: forged')"
echoed="$(echoed_key)"
say "no Authorization plus client X-RL-Key: forged -> status $code, upstream x-rl-key: '${echoed:-<absent>}'"
rc=1
if [ "$code" = "200" ] && [ -z "$echoed" ]; then rc=0; fi
check "a forged X-RL-Key without Authorization is removed before the upstream" "$rc"
vcli FLUSHALL >/dev/null
statuses=""
for _ in 1 2 3 4 5; do statuses="$statuses $(send -)"; done
say "no Authorization, 5 requests:$statuses"
dump_valkey "$WORK/valkey-dump4.txt"
grep '^KEY ' "$WORK/valkey-dump4.txt" || true
say "keys: $(vcli DBSIZE)"
rc=1
if [ -n "$statuses" ] && [ "$(vcli DBSIZE)" -ge 1 ]; then rc=0; fi
check "no-Authorization behaviour measured (statuses and key recorded above)" "$rc"

# ---------------------------------------------------------------------------------------------
section "summary"
say "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -ne 0 ]; then exit 1; fi
