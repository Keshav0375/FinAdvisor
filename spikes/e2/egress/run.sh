#!/usr/bin/env bash
# E2-T2 spike harness: Kong 3.9.4 (DB-less) as the LLM egress to the Anthropic Messages API.
# Scratch only, never shipped. Demo mode (default) needs no key: the upstream is a local mock,
# and every result is "MOCK upstream, not api.anthropic.com".
#
#   bash spikes/e2/egress/run.sh | tee spikes/e2/egress/out.txt
#   ANTHROPIC_API_KEY=... EGRESS_UPSTREAM=https://api.anthropic.com EGRESS_MODEL=<model id> \
#     bash spikes/e2/egress/run.sh --live | tee spikes/e2/egress/out-live.txt
#
# One container (python, the mock upstream) owns a network namespace and publishes four ephemeral
# host ports. Every Kong shares that namespace (--network container:), so the harness creates no
# network object and every address is 127.0.0.1:
#   8000 plain-proxy Kong (the candidate)      8001 ai-proxy preserve Kong (experiment)
#   8002 Kong whose ip-restriction excludes the client (403 test)
#   8003 Kong with log redaction removed and a 2/min limit (negative control, 429 test)
# Every container is started with -d --rm and stopped with docker stop in the EXIT trap.
# The Anthropic key is only ever read from the environment and passed with `docker run -e NAME`
# (no value on any command line); the harness writes it to no file.
#
# Knobs (environment, defaults in brackets):
#   EGRESS_KONG_IMAGE [kong:3.9.4]  EGRESS_MOCK_IMAGE [python:3.12-slim]
#   EGRESS_UPSTREAM [http://127.0.0.1:9100]  EGRESS_MOCK_PORT [9100]  EGRESS_MODEL [mock-model]
#   EGRESS_KEY [demo-egress-key-chat-api]  EGRESS_ALLOW [127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16]
#   EGRESS_DENY_ALLOW [203.0.113.0/24]  EGRESS_RATE_PER_MIN [6000]  EGRESS_TTFB_RUNS [20]
#   EGRESS_CONNECT_TIMEOUT_MS [2000]  EGRESS_READ_TIMEOUT_MS [60000]  EGRESS_CURL_MAX_S [60]
#   EGRESS_WAIT_S [60] readiness wait  EGRESS_WINDOW_GUARD_S [50] see align_window
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KONG_IMAGE="${EGRESS_KONG_IMAGE:-kong:3.9.4}"
MOCK_IMAGE="${EGRESS_MOCK_IMAGE:-python:3.12-slim}"
UPSTREAM="${EGRESS_UPSTREAM:-http://127.0.0.1:${EGRESS_MOCK_PORT:-9100}}"
MOCK_PORT="${EGRESS_MOCK_PORT:-9100}"
MODEL="${EGRESS_MODEL:-mock-model}"
EGRESS_KEY="${EGRESS_KEY:-demo-egress-key-chat-api}"
ALLOW="${EGRESS_ALLOW:-127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}"
DENY_ALLOW="${EGRESS_DENY_ALLOW:-203.0.113.0/24}"
RATE_PER_MIN="${EGRESS_RATE_PER_MIN:-6000}"
TTFB_RUNS="${EGRESS_TTFB_RUNS:-20}"
CONNECT_TIMEOUT_MS="${EGRESS_CONNECT_TIMEOUT_MS:-2000}"
READ_TIMEOUT_MS="${EGRESS_READ_TIMEOUT_MS:-60000}"
CURL_MAX_S="${EGRESS_CURL_MAX_S:-60}"
WAIT_S="${EGRESS_WAIT_S:-60}"
WINDOW_GUARD_S="${EGRESS_WINDOW_GUARD_S:-50}"

LIVE=0
if [ "${1:-}" = "--live" ]; then LIVE=1; fi

RUN_ID="fa-e2t2-$$"
BASE="$RUN_ID-base"
KONGS=()
WORK="$(mktemp -d)"
PASS=0
FAIL=0
PORT_MAIN=""
PORT_AI=""
PORT_DENY=""
PORT_CTRL=""
PORT_MOCK=""
PROMPT_SENTINEL="PROMPT-SENTINEL-$RUN_ID"

# Demo credential for the mock upstream. Never a real token.
MOCK_KEY="demo-anthropic-key-sentinel-$RUN_ID"
if [ "$LIVE" -eq 1 ]; then
  : "${ANTHROPIC_API_KEY:?live mode needs ANTHROPIC_API_KEY in the environment}"
  : "${EGRESS_UPSTREAM:?live mode needs EGRESS_UPSTREAM (https://api.anthropic.com)}"
  : "${EGRESS_MODEL:?live mode needs EGRESS_MODEL (a cheap model id)}"
else
  ANTHROPIC_API_KEY="$MOCK_KEY"
fi
export ANTHROPIC_API_KEY
export MOCK_EXPECT_KEY="$MOCK_KEY"

cleanup() {
  local c
  for c in ${KONGS[@]+"${KONGS[@]}"}; do docker stop "$c" >/dev/null 2>&1 || true; done
  docker stop "$BASE" >/dev/null 2>&1 || true
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
finding() { say "FINDING $*"; } # a measured fact that is not a pass/fail condition

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

mock_healthy() {
  docker exec "$BASE" python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:$MOCK_PORT/_health', timeout=2)"
}
kong_answers() { # kong_answers <port>: any HTTP answer (not a refused connection)
  [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$1/egress/anthropic/v1/messages")" != "000" ]
}
host_port() { local p; p="$(docker port "$BASE" "$1/tcp" | head -1)"; printf '%s' "${p##*:}"; }

render() { # render <template> <out> <allow list>
  sed -e "s#__EGRESS_KEY__#$EGRESS_KEY#" -e "s#__UPSTREAM__#$UPSTREAM#g" \
    -e "s#__CONNECT_TIMEOUT_MS__#$CONNECT_TIMEOUT_MS#" -e "s#__READ_TIMEOUT_MS__#$READ_TIMEOUT_MS#g" \
    -e "s#__ALLOW__#$3#" -e "s#__RATE_PER_MIN__#$RATE_PER_MIN#" "$1" >"$2"
}

start_kong() { # start_kong <container> <config file> <listen port> [extra docker args...]
  local name="$1" config="$2" listen="$3"
  shift 3
  KONGS+=("$name")
  docker run -d --rm --name "$name" --network "container:$BASE" \
    -v "$config:/k/kong.yml:ro" \
    -e KONG_DATABASE=off -e KONG_DECLARATIVE_CONFIG=/k/kong.yml \
    -e KONG_PROXY_LISTEN="0.0.0.0:$listen" -e KONG_ADMIN_LISTEN=off \
    -e KONG_NGINX_WORKER_PROCESSES=2 -e ANTHROPIC_API_KEY \
    "$@" "$KONG_IMAGE" >/dev/null
}

stop_kong() { # stop_kong <container> <log file>: keep its logs, then stop it
  docker logs "$1" >"$2" 2>&1 || true
  docker stop "$1" >/dev/null
}

align_window() { # do not start a per-minute block in the last seconds of a minute
  local s
  s=$((10#$(date +%S)))
  if [ "$s" -ge "$WINDOW_GUARD_S" ]; then
    say "(waiting $((61 - s))s for the next minute window)"
    sleep $((61 - s))
  fi
}

count_in() { # count_in <literal> <file>: grep -c, 0 when absent (grep exits 1 then)
  grep -c -F -- "$1" "$2" || true
}
count_secret() { # count_secret <file>: occurrences of the Anthropic key, read without argv exposure
  grep -c -F -f <(printf '%s\n' "$ANTHROPIC_API_KEY") "$1" || true
}
median_ms() { # stdin: seconds, one per line; prints the median in ms
  sort -n | awk '{a[NR] = $1} END {if (NR % 2) m = a[(NR + 1) / 2]; else m = (a[NR / 2] + a[NR / 2 + 1]) / 2; printf "%.1f", m * 1000}'
}

# --- request builders (bodies carry the prompt sentinel, never real text) -----------------------
stream_body() { printf '{"model":"%s","max_tokens":64,"stream":true,"messages":[{"role":"user","content":"%s"}]}' "$MODEL" "$PROMPT_SENTINEL"; }
plain_body() { printf '{"model":"%s","max_tokens":64,"messages":[{"role":"user","content":"%s"}]}' "$MODEL" "$PROMPT_SENTINEL"; }
TOOL_DEF='"tools":[{"name":"get_time","description":"Get the current time in a zone","input_schema":{"type":"object","properties":{"zone":{"type":"string"}},"required":["zone"]}}]'
tool_body() { printf '{"model":"%s","max_tokens":128,%s,"tool_choice":{"type":"tool","name":"get_time"},"messages":[{"role":"user","content":"%s"}]}' "$MODEL" "$TOOL_DEF" "$PROMPT_SENTINEL"; }
followup_body() { # followup_body <tool_use id>
  printf '{"model":"%s","max_tokens":128,%s,"messages":[{"role":"user","content":"%s"},{"role":"assistant","content":[{"type":"tool_use","id":"%s","name":"get_time","input":{"zone":"UTC"}}]},{"role":"user","content":[{"type":"tool_result","tool_use_id":"%s","content":"12:00"}]}]}' \
    "$MODEL" "$TOOL_DEF" "$PROMPT_SENTINEL" "$1" "$1"
}

# call <port> <path> <key|-> <body> [extra curl args...]: prints the status; body to $WORK/body, headers to $WORK/headers
call() {
  local port="$1" path="$2" key="$3" body="$4"
  shift 4
  local auth=()
  if [ "$key" != "-" ]; then auth=(-H "apikey: $key"); fi
  curl -s --max-time "$CURL_MAX_S" -o "$WORK/body" -D "$WORK/headers" -w '%{http_code}' \
    ${auth[@]+"${auth[@]}"} -H 'anthropic-version: 2023-06-01' -H 'content-type: application/json' \
    "$@" -d "$body" "http://127.0.0.1:$port$path"
}

direct_curl() { # direct_curl <curl args...>: a request straight to the upstream with the real or demo key
  if [ "$LIVE" -eq 1 ]; then
    printf 'header = "x-api-key: %s"\n' "$ANTHROPIC_API_KEY" | curl -s --max-time "$CURL_MAX_S" -K - "$@"
  else
    curl -s --max-time "$CURL_MAX_S" -H "x-api-key: $MOCK_KEY" "$@"
  fi
}
direct_url() { if [ "$LIVE" -eq 1 ]; then printf '%s/v1/messages' "$UPSTREAM"; else printf 'http://127.0.0.1:%s/v1/messages' "$PORT_MOCK"; fi; }

mock_last() { if [ "$LIVE" -eq 0 ]; then curl -s "http://127.0.0.1:$PORT_MOCK/_last"; fi; }
json_has() { # json_has <haystack> <literal>
  case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac
}

event_names() { grep '^event:' "$WORK/body" | awk '{print $2}'; }

# ---------------------------------------------------------------------------------------------
section "setup"
if [ "$LIVE" -eq 1 ]; then say "mode=LIVE upstream=$UPSTREAM model=$MODEL kong=$KONG_IMAGE"; else say "mode=DEMO (MOCK upstream, not api.anthropic.com) upstream=$UPSTREAM kong=$KONG_IMAGE"; fi
docker run -d --rm --name "$BASE" \
  -p 127.0.0.1::8000 -p 127.0.0.1::8001 -p 127.0.0.1::8002 -p 127.0.0.1::8003 -p "127.0.0.1::$MOCK_PORT" \
  -v "$HERE/mock_anthropic.py:/m/mock_anthropic.py:ro" -e MOCK_EXPECT_KEY -e "MOCK_PORT=$MOCK_PORT" -e MOCK_HOST=0.0.0.0 \
  "$MOCK_IMAGE" python /m/mock_anthropic.py >/dev/null
wait_for "mock upstream" mock_healthy
PORT_MAIN="$(host_port 8000)"; PORT_AI="$(host_port 8001)"; PORT_DENY="$(host_port 8002)"
PORT_CTRL="$(host_port 8003)"; PORT_MOCK="$(host_port "$MOCK_PORT")"

render "$HERE/kong.yml" "$WORK/kong-main.yml" "$ALLOW"
render "$HERE/kong.yml" "$WORK/kong-deny.yml" "$DENY_ALLOW"
render "$HERE/kong-ai.yml" "$WORK/kong-ai.yml" "$ALLOW"
sed -e '/set_serialize_value/d' "$WORK/kong-main.yml" | sed -e 's#minute: .*#minute: 2#' >"$WORK/kong-ctrl.yml"
start_kong "$RUN_ID-kong-main" "$WORK/kong-main.yml" 8000
start_kong "$RUN_ID-kong-deny" "$WORK/kong-deny.yml" 8002
wait_for "kong main" kong_answers "$PORT_MAIN"
wait_for "kong deny" kong_answers "$PORT_DENY"
say "kong: $(docker exec "$RUN_ID-kong-main" kong version | tr -d '\r')"

# ---------------------------------------------------------------------------------------------
section "AC1: stream:true through the plain-proxy route, native events"
code="$(call "$PORT_MAIN" /egress/anthropic/v1/messages "$EGRESS_KEY" "$(stream_body)" -N)"
names="$(event_names | tr '\n' ' ')"
say "status $code; events: $names"
rc=1
if [ "$code" = "200" ]; then rc=0; fi
check "stream_request_returns_200 (got $code)" "$rc"
first="$(event_names | head -1)"; last="$(event_names | tail -1)"
rc=1
if [ "$first" = "message_start" ] && [ "$last" = "message_stop" ]; then rc=0; fi
check "stream_first_event_message_start_last_message_stop (first=$first last=$last)" "$rc"
rc=1
if event_names | grep -q '^content_block_delta$'; then rc=0; fi
check "stream_contains_content_block_delta" "$rc"
rc=1
if grep -qi '^content-type: text/event-stream' "$WORK/headers"; then rc=0; fi
check "stream_content_type_event_stream" "$rc"
rc=1
if grep -qi '^x-ratelimit-limit-minute: ' "$WORK/headers"; then rc=0; fi
check "global_rate_limiting_headers_present ($(grep -i '^x-ratelimit-limit-minute:' "$WORK/headers" | tr -d '\r'))" "$rc"
if [ "$LIVE" -eq 0 ]; then
  last="$(mock_last)"
  rc=1
  if json_has "$last" '"x_api_key_matches": true' && json_has "$last" '"x_api_key_count": 1'; then rc=0; fi
  check "upstream_received_injected_key_exactly_once (MOCK)" "$rc"
  rc=1
  if json_has "$last" '"has_apikey_header": false' && json_has "$last" '"has_authorization_header": false'; then rc=0; fi
  check "egress_key_and_authorization_not_forwarded_upstream (MOCK)" "$rc"
  code="$(call "$PORT_MAIN" /egress/anthropic/v1/messages "$EGRESS_KEY" "$(plain_body)" -H 'x-api-key: client-forged' -H 'authorization: Bearer client-forged')"
  last="$(mock_last)"
  rc=1
  if [ "$code" = "200" ] && json_has "$last" '"x_api_key_matches": true' && json_has "$last" '"x_api_key_count": 1' && json_has "$last" '"has_authorization_header": false'; then rc=0; fi
  check "client_sent_x_api_key_and_authorization_replaced_not_forwarded (MOCK, got $code)" "$rc"
fi

section "AC1: time to first byte, Kong minus direct, median of $TTFB_RUNS (interleaved)"
if [ "$LIVE" -eq 0 ]; then say "MOCK upstream: this is proxy overhead on one host, not network latency to api.anthropic.com."; fi
: >"$WORK/ttfb-kong"; : >"$WORK/ttfb-direct"; : >"$WORK/total-kong"; : >"$WORK/total-direct"
for ((i = 0; i < TTFB_RUNS; i++)); do
  curl -s --max-time "$CURL_MAX_S" -o /dev/null -w '%{time_starttransfer} %{time_total}\n' -N \
    -H "apikey: $EGRESS_KEY" -H 'anthropic-version: 2023-06-01' -H 'content-type: application/json' \
    -d "$(stream_body)" "http://127.0.0.1:$PORT_MAIN/egress/anthropic/v1/messages" >"$WORK/m"
  awk '{print $1}' "$WORK/m" >>"$WORK/ttfb-kong"; awk '{print $2}' "$WORK/m" >>"$WORK/total-kong"
  direct_curl -o /dev/null -w '%{time_starttransfer} %{time_total}\n' -N \
    -H 'anthropic-version: 2023-06-01' -H 'content-type: application/json' \
    -d "$(stream_body)" "$(direct_url)" >"$WORK/m"
  awk '{print $1}' "$WORK/m" >>"$WORK/ttfb-direct"; awk '{print $2}' "$WORK/m" >>"$WORK/total-direct"
done
tk="$(median_ms <"$WORK/ttfb-kong")"; td="$(median_ms <"$WORK/ttfb-direct")"
ak="$(median_ms <"$WORK/total-kong")"; ad="$(median_ms <"$WORK/total-direct")"
say "TTFB median ms: kong=$tk direct=$td delta=$(awk -v a="$tk" -v b="$td" 'BEGIN {printf "%.1f", a - b}')"
say "total median ms: kong=$ak direct=$ad delta=$(awk -v a="$ak" -v b="$ad" 'BEGIN {printf "%.1f", a - b}')"
rc=1
if [ "$(wc -l <"$WORK/ttfb-kong")" -eq "$TTFB_RUNS" ] && [ "$(wc -l <"$WORK/ttfb-direct")" -eq "$TTFB_RUNS" ] && [ "$tk" != "0.0" ]; then rc=0; fi
check "ttfb_median_recorded_for_kong_and_direct ($TTFB_RUNS runs each)" "$rc"

# ---------------------------------------------------------------------------------------------
section "AC2: tools array -> tool_use, tool_result follow-up through the same route"
code="$(call "$PORT_MAIN" /egress/anthropic/v1/messages "$EGRESS_KEY" "$(tool_body)")"
tool_id="$(grep -oE '"id": ?"toolu_[A-Za-z0-9_]+"' "$WORK/body" | head -1 | cut -d'"' -f4 || true)"
say "status $code; tool_use id: ${tool_id:-<none>}"
rc=1
if [ "$code" = "200" ] && grep -q '"type": *"tool_use"' "$WORK/body" && grep -q '"stop_reason": *"tool_use"' "$WORK/body"; then rc=0; fi
check "tools_request_returns_tool_use_block" "$rc"
rc=1
if [ -n "$tool_id" ]; then
  code="$(call "$PORT_MAIN" /egress/anthropic/v1/messages "$EGRESS_KEY" "$(followup_body "$tool_id")")"
  say "follow-up with tool_result status $code"
  if [ "$code" = "200" ]; then rc=0; fi
fi
check "tool_result_followup_accepted_200_same_route" "$rc"
tool_stream_body="$(tool_body | sed 's/"max_tokens":128,/"max_tokens":128,"stream":true,/')"
code="$(call "$PORT_MAIN" /egress/anthropic/v1/messages "$EGRESS_KEY" "$tool_stream_body" -N)"
rc=1
if [ "$code" = "200" ] && grep -q 'input_json_delta' "$WORK/body"; then rc=0; fi
check "streamed_tool_use_has_input_json_delta" "$rc"

# ---------------------------------------------------------------------------------------------
section "AC3: authentication and source address"
code="$(call "$PORT_MAIN" /egress/anthropic/v1/messages - "$(plain_body)")"
rc=1
if [ "$code" = "401" ]; then rc=0; fi
check "test_no_apikey_returns_401 (got $code)" "$rc"
code="$(call "$PORT_MAIN" /egress/anthropic/v1/messages "wrong-$EGRESS_KEY" "$(plain_body)")"
rc=1
if [ "$code" = "401" ]; then rc=0; fi
check "test_wrong_apikey_returns_401 (got $code)" "$rc"
code="$(call "$PORT_MAIN" /egress/anthropic/v1/messages "$EGRESS_KEY" "$(plain_body)")"
rc=1
if [ "$code" = "200" ]; then rc=0; fi
check "test_source_inside_allow_list_returns_200 (allow $ALLOW; got $code)" "$rc"
code="$(call "$PORT_DENY" /egress/anthropic/v1/messages "$EGRESS_KEY" "$(plain_body)")"
say "second Kong, allow list $DENY_ALLOW, valid egress key: status $code, body $(tr -d '\n' <"$WORK/body")"
rc=1
if [ "$code" = "403" ]; then rc=0; fi
check "test_ip_outside_allow_list_returns_403 (got $code)" "$rc"

# ---------------------------------------------------------------------------------------------
section "AC4: Kong logs"
align_window
docker logs "$RUN_ID-kong-main" >"$WORK/kong-main.log" 2>&1
say "captured $(wc -l <"$WORK/kong-main.log" | tr -d ' ') log lines from the plain-proxy Kong (access log, error log, file-log JSON)"
for what in "Anthropic key:count_secret" "egress key:$EGRESS_KEY" "prompt text:$PROMPT_SENTINEL"; do
  label="${what%%:*}"; val="${what#*:}"
  if [ "$val" = "count_secret" ]; then n="$(count_secret "$WORK/kong-main.log")"; else n="$(count_in "$val" "$WORK/kong-main.log")"; fi
  say "grep -c $label = $n"
  rc=1
  if [ "$n" = "0" ]; then rc=0; fi
  check "kong_log_contains_no_${label// /_} (grep -c = $n)" "$rc"
done
say "log lines with token counts:"
grep -o 'egress_usage status=[0-9]* input_tokens=[^ ]* output_tokens=[0-9a-z/]*' "$WORK/kong-main.log" | sort | uniq -c
rc=1
if [ "$LIVE" -eq 0 ]; then
  if grep -q 'egress_usage status=200 input_tokens=11 output_tokens=7' "$WORK/kong-main.log"; then rc=0; fi
  check "kong_log_contains_token_counts_11_in_7_out (MOCK values, plain proxy, custom Lua)" "$rc"
else
  if grep -qE 'egress_usage status=200 input_tokens=[1-9][0-9]* output_tokens=[1-9][0-9]*' "$WORK/kong-main.log"; then rc=0; fi
  check "kong_log_contains_nonzero_token_counts (live, plain proxy, custom Lua)" "$rc"
fi
if [ "$LIVE" -eq 0 ]; then
  docker logs "$BASE" >"$WORK/mock.log" 2>&1
  n="$(count_in "$PROMPT_SENTINEL" "$WORK/mock.log")"
  rc=1
  if [ "$n" = "0" ]; then rc=0; fi
  check "mock_upstream_log_contains_no_prompt_text (grep -c = $n)" "$rc"
fi

section "AC4 negative control + global rate limit (Kong 8003: serialize redaction removed, 2/min)"
start_kong "$RUN_ID-kong-ctrl" "$WORK/kong-ctrl.yml" 8003
wait_for "kong ctrl" kong_answers "$PORT_CTRL"
align_window
statuses=""
for _ in 1 2 3; do statuses="$statuses $(call "$PORT_CTRL" /egress/anthropic/v1/messages "$EGRESS_KEY" "$(plain_body)")"; done
say "3 requests against a 2/min limit:$statuses"
rc=1
if [ "$statuses" = " 200 200 429" ]; then rc=0; fi
check "global_rate_limiting_returns_429_over_the_limit" "$rc"
stop_kong "$RUN_ID-kong-ctrl" "$WORK/kong-ctrl.log"
n="$(count_secret "$WORK/kong-ctrl.log")"
say "file-log without set_serialize_value: grep -c Anthropic key = $n"
rc=1
if [ "$n" -ge 1 ]; then rc=0; fi
check "without_redaction_the_anthropic_key_is_in_file_log (control, grep -c = $n)" "$rc"

# ---------------------------------------------------------------------------------------------
section "ai-proxy 3.9.4 experiments (provider anthropic, route_type preserve)"
say "negative control: llm_format anthropic (ADR-011 says 3.10+)"
sed -e 's#^              route_type: preserve#              route_type: preserve\n              llm_format: anthropic#' "$WORK/kong-ai.yml" >"$WORK/kong-ai-fmt.yml"
docker run --rm --name "$RUN_ID-kong-aifmt" --network "container:$BASE" \
  -v "$WORK/kong-ai-fmt.yml:/k/kong.yml:ro" \
  -e KONG_DATABASE=off -e KONG_DECLARATIVE_CONFIG=/k/kong.yml \
  -e KONG_PROXY_LISTEN=0.0.0.0:8001 -e KONG_ADMIN_LISTEN=off -e ANTHROPIC_API_KEY \
  "$KONG_IMAGE" >"$WORK/aifmt.log" 2>&1 &
aifmt_pid=$!
KONGS+=("$RUN_ID-kong-aifmt")
for ((i = 0; i < WAIT_S; i++)); do
  if ! kill -0 "$aifmt_pid" 2>/dev/null; then break; fi
  sleep 1
done
grep -m1 'llm_format' "$WORK/aifmt.log" | sed 's/^.*\(declarative config.*\)$/\1/' | cut -c1-300 || say "(no llm_format message captured)"
rc=1
if grep -q 'llm_format' "$WORK/aifmt.log" && grep -q 'expected one of: openai, bedrock, gemini' "$WORK/aifmt.log"; then rc=0; fi
check "ai_proxy_3_9_4_has_no_anthropic_llm_format (accepts openai, bedrock, gemini only)" "$rc"
docker stop "$RUN_ID-kong-aifmt" >/dev/null 2>&1 || true
wait "$aifmt_pid" 2>/dev/null || true

say "negative control: model.name set -> client model must match"
sed -e 's#^                provider: anthropic#                provider: anthropic\n                name: pinned-model#' "$WORK/kong-ai.yml" >"$WORK/kong-ai-named.yml"
start_kong "$RUN_ID-kong-ainamed" "$WORK/kong-ai-named.yml" 8001
wait_for "kong ai named" kong_answers "$PORT_AI"
code="$(call "$PORT_AI" /egress/anthropic/v1/messages "$EGRESS_KEY" "$(plain_body)")"
say "client model $MODEL, configured pinned-model: status $code, body $(tr -d '\n' <"$WORK/body")"
rc=1
if [ "$code" = "400" ] && grep -q 'cannot use own model' "$WORK/body"; then rc=0; fi
check "ai_proxy_with_model_name_rejects_other_models (one ai-proxy per model, got $code)" "$rc"
stop_kong "$RUN_ID-kong-ainamed" "$WORK/ainamed.log"

say "preserve without model.name (the usable form)"
start_kong "$RUN_ID-kong-ai" "$WORK/kong-ai.yml" 8001
wait_for "kong ai" kong_answers "$PORT_AI"
code="$(call "$PORT_AI" /egress/anthropic/v1/messages "$EGRESS_KEY" "$(stream_body)" -N)"
rc=1
if [ "$code" = "200" ] && [ "$(event_names | head -1)" = "message_start" ] && [ "$(event_names | tail -1)" = "message_stop" ]; then rc=0; fi
check "ai_proxy_preserve_streams_native_events (got $code)" "$rc"
code="$(call "$PORT_AI" /egress/anthropic/v1/messages "$EGRESS_KEY" "$(tool_body)")"
rc=1
if [ "$code" = "200" ] && grep -q '"type": *"tool_use"' "$WORK/body"; then rc=0; fi
check "ai_proxy_preserve_returns_tool_use (got $code)" "$rc"
code="$(call "$PORT_AI" /egress/anthropic/v1/messages "$EGRESS_KEY" "$(plain_body)")"
say "(ai-proxy preserve, JSON request) status $code"
code_ai_ct="$(call "$PORT_AI" /egress/anthropic/v1/messages/count_tokens "$EGRESS_KEY" "$(plain_body)")"
code_plain_ct="$(call "$PORT_MAIN" /egress/anthropic/v1/messages/count_tokens "$EGRESS_KEY" "$(plain_body)")"
finding "path handling: POST .../v1/messages/count_tokens -> ai-proxy preserve $code_ai_ct (pinned to upstream_path /v1/messages), plain proxy $code_plain_ct (path forwarded; the mock has no count_tokens, so 404 is the mock's answer, live it is a real endpoint)"
stop_kong "$RUN_ID-kong-ai" "$WORK/kong-ai.log"
say "log_statistics as logged by the ai-proxy variant (usage object of each request):"
grep -o '"usage":{[^}]*}' "$WORK/kong-ai.log" | sort | uniq -c
rc=1
if grep -q '"usage":{' "$WORK/kong-ai.log"; then rc=0; fi
check "ai_proxy_log_statistics_usage_object_is_logged" "$rc"
nonzero="$(grep -o '"usage":{[^}]*}' "$WORK/kong-ai.log" | grep -cE '"(prompt|completion|total)_tokens":[1-9]' || true)"
finding "ai-proxy preserve log_statistics requests with non-zero token counts: $nonzero of $(grep -c '"usage":{' "$WORK/kong-ai.log" || true) (the code skips response parsing in preserve mode: normalize-json-response.lua, parse-sse-chunk.lua, normalize-sse-chunk.lua)"
n="$(count_secret "$WORK/kong-ai.log")"
rc=1
if [ "$n" = "0" ] && [ "$(count_in "$PROMPT_SENTINEL" "$WORK/kong-ai.log")" = "0" ] && [ "$(count_in "$EGRESS_KEY" "$WORK/kong-ai.log")" = "0" ]; then rc=0; fi
check "ai_proxy_variant_log_contains_no_keys_nor_prompt (with set_serialize_value redaction)" "$rc"

# ---------------------------------------------------------------------------------------------
section "summary"
say "PASS=$PASS FAIL=$FAIL"
if [ "$LIVE" -eq 0 ]; then say "All results above are from the MOCK upstream, not api.anthropic.com: provisional, confirm with --live (K6)."; fi
if [ "$FAIL" -ne 0 ]; then exit 1; fi
