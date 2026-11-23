# E2-T2 spike: Kong egress to the Anthropic Messages API

Scratch harness, not shipped. Result and verdict: `docs/adr/anthropic-egress-ai-proxy.md`.

```
bash spikes/e2/egress/run.sh | tee spikes/e2/egress/out.txt
```

Demo mode (default) needs Docker and curl, and no Anthropic key. The upstream is `mock_anthropic.py`
(a local stand-in for `POST /v1/messages`: JSON, native SSE, tool_use, tool_result follow-up), so every
result is "MOCK upstream, not api.anthropic.com" and provisional. The script exits 1 if a check fails;
`out.txt` is the committed demo run. `FINDING` lines are measured facts that are not pass/fail.

Live run (needs the key, K6; about 50 small calls, use a cheap model):

```
ANTHROPIC_API_KEY=... EGRESS_UPSTREAM=https://api.anthropic.com EGRESS_MODEL=<model id> \
  bash spikes/e2/egress/run.sh --live | tee spikes/e2/egress/out-live.txt
```

The key is read from the environment, handed to Kong with `docker run -e ANTHROPIC_API_KEY` and read
there by the `env` vault; it is on no command line and in no file.

- `kong.yml`: the candidate. Plain proxy (`/egress/anthropic` strips to the upstream), `key-auth`
  (consumer `chat-api`), `ip-restriction`, `request-transformer` (adds `x-api-key`), global
  `rate-limiting`, `file-log`, and a `pre-function` that reads the key, redacts it from serialized logs
  and logs token counts.
- `kong-ai.yml`: the experiment. OSS `ai-proxy` with `provider: anthropic`, `route_type: preserve`.
- Containers: one python container (the mock) owns the network namespace and publishes four ephemeral
  host ports; every Kong joins it with `--network container:`, so no network object and no fixed port.
  All run with `-d --rm` and are stopped with `docker stop` on exit.
- Knobs: `EGRESS_KONG_IMAGE`, `EGRESS_MOCK_IMAGE`, `EGRESS_UPSTREAM`, `EGRESS_MOCK_PORT`, `EGRESS_MODEL`,
  `EGRESS_KEY`, `EGRESS_ALLOW`, `EGRESS_DENY_ALLOW`, `EGRESS_RATE_PER_MIN`, `EGRESS_TTFB_RUNS`,
  `EGRESS_CONNECT_TIMEOUT_MS`, `EGRESS_READ_TIMEOUT_MS`, `EGRESS_CURL_MAX_S`, `EGRESS_WAIT_S`,
  `EGRESS_WINDOW_GUARD_S`.
