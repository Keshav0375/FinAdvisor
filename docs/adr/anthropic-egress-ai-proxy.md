# ADR: Kong egress to the Anthropic Messages API, ai-proxy preserve or plain proxy (spike E2-T2)

Status: PROVISIONAL. Measured against a local mock of the Anthropic API (demo mode, no key). Verdict:
plain proxy PASS, `ai-proxy` `preserve` FAIL for this use. Items marked "mock, confirm with K6" must be
re-measured with a real key before E2-T20 builds the production route.
Source: `bash spikes/e2/egress/run.sh | tee spikes/e2/egress/out.txt` (committed output: 29 PASS, 0 FAIL).
Environment (demo mode, all local): `kong:3.9.4` DB-less, `python:3.12-slim` running
`spikes/e2/egress/mock_anthropic.py` as upstream, demo credentials only. Every number below is "MOCK
upstream, not api.anthropic.com". The researcher copies this ADR into the arch decision log after merge.

## Decision

The egress route `/egress/anthropic` is a plain proxy service to `https://api.anthropic.com`, not
`ai-proxy`. Plugins on the route: `key-auth` (consumer `chat-api`, `hide_credentials: true`),
`ip-restriction`, `pre-function`, `request-transformer`; global `rate-limiting` and a log plugin on the
service. The config is `spikes/e2/egress/kong.yml`. What the arch text does not say and E2-T20 must apply:

1. `request-transformer` cannot read an environment variable (its fields are not vault-referenceable on
   3.9.4). The key is read by a `pre-function` with `kong.vault.get("{vault://env/anthropic-api-key}")`
   (env var `ANTHROPIC_API_KEY` in the Kong container; works in the default sandbox, no
   `KONG_UNTRUSTED_LUA_SANDBOX_REQUIRES` needed), passed in `kong.ctx.shared`, and added by
   `request-transformer` as `x-api-key:$(shared.anthropic_key)` after `remove: headers: [x-api-key,
   authorization]`, so a client-sent `x-api-key` or `authorization` never reaches Anthropic.
2. Any Kong log plugin serializes the request headers as they are after the transformer, so the
   Anthropic key lands in the log. Control run (redaction removed): `grep -c` of the key in the file-log
   output = 2. Fix: `kong.log.set_serialize_value("request.headers.x-api-key", "REDACTED")` in the same
   `pre-function`; with it the count is 0. The egress key is not logged (`hide_credentials: true`).
   The `ai-proxy` variant leaks the same way and needs the same line.
3. `log_statistics` token counts do not exist for this route (see `ai-proxy` below). A `pre-function`
   (`body_filter` keeps the first and last 2 KB of the response, `log` parses the native `usage` fields
   and writes `egress_usage status=200 input_tokens=11 output_tokens=7` with `kong.log.notice`) gives
   the counts in Kong's log. The counts are optional: `chat-api` sees the same `usage` fields itself.
4. `rate-limiting` for the LLM calls: `limit_by: service`, `policy: local`, `hide_client_headers: false`
   (one gateway node). Three requests against a 2/min limit gave `200 200 429`.

## Measurements (mock, confirm with K6)

### AC1 stream:true, native events
- `POST /egress/anthropic/v1/messages` with `stream: true` returned 200, `text/event-stream`, events
  `message_start`, `content_block_start`, 5x `content_block_delta`, `content_block_stop`, `message_delta`,
  `message_stop`, in that order. The real API also sends `ping`; not measured here.
- TTFB, Kong minus direct, median of 20 interleaved runs (curl `time_starttransfer`): Kong 2.3 ms, direct
  1.0 ms, delta +1.3 ms. Total time: Kong 219.7 ms, direct 224.9 ms, delta -5.2 ms (noise: the mock
  paces its events at 20 ms). On one host this is proxy overhead only, not network latency to Anthropic.
  Live value: pending K6 (`out-live.txt`).

### AC2 tools
- A request with a `tools` array and forced `tool_choice` returned a `tool_use` block with
  `stop_reason: tool_use`; the follow-up carrying the matching `tool_result` returned 200 through the
  same route; the streamed form carries `input_json_delta`. The mock does not check the model's choice or
  the tool schema, so real tool behaviour is pending K6.

### AC3 authentication and source address (two named tests in `run.sh`)
- `test_no_apikey_returns_401`: 401. A wrong key: 401. An allowed source: 200.
- `test_ip_outside_allow_list_returns_403`: a second Kong whose `ip-restriction` allows only
  `203.0.113.0/24`, valid egress key: 403 `IP address not allowed: 192.168.215.1` (the Docker gateway is
  the client address here). The main Kong allows the RFC 1918 ranges and 127.0.0.0/8; the real list is the
  VPC range (E2).
- The mock's last-request report shows the upstream received `x-api-key` exactly once with the injected
  value, no `apikey` and no `authorization`, also when the client sent its own `x-api-key` and
  `authorization`.

### AC4 logs (captured: access log, error log, file-log JSON of the plain-proxy Kong)
- `grep -c` over the log: Anthropic key 0, egress key 0, prompt text 0 (the prompt is a unique sentinel).
  The mock's own log holds 0 prompt text too.
- Token counts: 26 lines `egress_usage status=200 input_tokens=11 output_tokens=7` (the mock's fixed
  usage), 3 lines for the 401 requests with `n/a`.

### `ai-proxy` 3.9.4, `provider: anthropic`, `route_type: preserve`
- `llm_format: anthropic` is refused at startup: `in 'llm_format': expected one of: openai, bedrock,
  gemini`. This confirms ADR-011 (Anthropic native format is 3.10+, and Kong OSS ends at 3.9.4).
- `route_type: preserve` is accepted and passes native Messages requests through: stream events and
  `tool_use` came back unchanged (200). But:
  - It forwards the client's full path (`/egress/anthropic/v1/messages`) upstream despite `strip_path`.
    Setting `model.options.upstream_path: /v1/messages` fixes that and then pins every request to that
    one path: `/v1/messages/count_tokens` reached `/v1/messages` (200 on the mock). The plain proxy
    forwarded the path as sent.
  - With `model.name` set, a request with another model gets 400 `cannot use own model - must be:
    <name>`. It must be left unset, or there is one ai-proxy per model (answer, aux, judge, fallback).
  - `log_statistics: true` logs a `usage` object, but 0 of 5 requests had non-zero counts (all fields 0,
    JSON and stream). The cause is in the code, not the mock: preserve mode returns early in
    `normalize-json-response.lua`, `parse-sse-chunk.lua` and `normalize-sse-chunk.lua`. So the arch's
    `log_statistics` benefit does not exist with `preserve`.
  - It sets `anthropic-version` itself from `options.anthropic_version`, replacing the client's value.

## Verdict

Provisional PASS for the plain proxy: native Messages format with streaming and tool use, `key-auth` 401,
`ip-restriction` 403, key injected from the environment and absent from logs, global rate limit. FAIL for
`ai-proxy` `preserve` as the egress: no usable `log_statistics`, path pinned, model pinned or one plugin
per model, and it adds nothing the plain proxy lacks. The arch text "`ai-proxy` `preserve` or plain
proxy" resolves to plain proxy, with the `pre-function` and `request-transformer` settings above. E2-T20
builds the plain-proxy route; the fallback below is not applied.

## Pending the live run (K6)

Not satisfiable without a key: the real event sequence (with `ping`), a real `tool_use`, TTFB Kong vs
direct against `api.anthropic.com` (network), real token counts, and the TLS upstream (`https`, service
`protocol`). Command (reads the key from the environment only, about 50 small calls):

```
ANTHROPIC_API_KEY=... EGRESS_UPSTREAM=https://api.anthropic.com EGRESS_MODEL=<cheap model id> \
  bash spikes/e2/egress/run.sh --live | tee spikes/e2/egress/out-live.txt
```

Use a cheap model from the repo config (`LLM_MODEL_AUX` in `.env.example` is `claude-haiku-5-5`). If the
live run fails a check, update this ADR; a failure of AC1 or AC2 against the real API triggers the
fallback.

## Named fallback (not applied)

Overview section 10 item 4: `LLMClient` calls Anthropic directly, the egress route is dropped, and
E2-T20 closes as not applicable.

## Consequences for E2

- E2-T20: plain-proxy service to `https://api.anthropic.com` with the plugins above; Kong container env
  `ANTHROPIC_API_KEY` from Secret Manager; no `ai-proxy`, so no `llm_format` or `log_statistics` in the
  production kong.yml.
- 06 section 6.5 llm-egress row: replace "`ai-proxy` `preserve` or plain proxy" by "plain proxy", and
  `log_statistics` by "token counts from a `pre-function` log line (optional)". ADR-011 stays valid.
- Every Kong log plugin must be paired with the `set_serialize_value` redaction of `x-api-key`.
- Chat-api owns the retry and fallback to `claude-sonnet-4-6` (`LLM_MODEL_FALLBACK`); the route sets
  `retries: 0` and bounded connect, read and write timeouts so a stalled upstream cannot hold a worker.
