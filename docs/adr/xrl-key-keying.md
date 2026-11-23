# ADR: X-RL-Key keying and Valkey keys on Kong 3.9.4 (spike E2-T1)

Status: measured, verdict PASS with two required settings that the arch text does not state.
Source: `bash spikes/e2/xrlkey/run.sh | tee spikes/e2/xrlkey/out.txt` (committed output: 15 PASS, 0 FAIL).
Environment (demo mode, all local): `kong:3.9.4` DB-less, `valkey/valkey:8.0` (valkey_version 8.0.13),
the E2-T3 `stub-chat-api` as upstream, demo tokens only. The researcher copies this ADR into the arch
decision log after merge (R1).

## Decision

A `pre-function` (access phase) sets `X-RL-Key` to the hex sha256 of the full `Authorization` value.
`rate-limiting` keys on it (`limit_by: header`, `header_name: X-RL-Key`, `policy: redis`). Raw bearer
tokens never reach Valkey. The config is `spikes/e2/xrlkey/kong.yml`. Two settings differ from the arch
text and must go into the production kong.yml and the Kong deployment (E2):

1. `rate-limiting.config.sync_rate: -1`, not `0`. Kong 3.9.4 refuses to start with `sync_rate: 0`:
   `sync_rate must be greater than 0.02, or -1 to disable`. `-1` is the synchronous mode (each request
   increments in Valkey), which is what "sync_rate 0" in ADR-011 and arch 6.5 meant.
2. Kong must run with `KONG_UNTRUSTED_LUA_SANDBOX_REQUIRES=resty.sha256,resty.string`.

## Measurements

### (a) Plugin order and how it is guaranteed
- `pre-function/handler.lua` is `return require("kong.plugins.pre-function._handler")(1000000)`;
  `rate-limiting/handler.lua` has `PRIORITY = 910`. The access phase runs plugins by descending
  priority, so pre-function runs first. The order is a constant in the plugin code of the pinned image,
  not a config value; it holds for every route that carries both plugins, whatever the order in
  kong.yml. It can change only by changing the Kong version (re-run this spike on any Kong bump).
- Empirical proof: `rate-limiting` read the header the pre-function had just set. The upstream echo
  shows `x-rl-key` equal to the sha256 of the token and the Valkey key holds that sha256 (AC1, AC2).
  The pre-function used `kong.service.request.set_header`; `rate-limiting` reads it through
  `kong.request.get_header`, so this setter is visible to it.

### (b) sha256 inside the pre-function sandbox
- Default settings (`untrusted_lua = sandbox`): `require 'resty.sha256' not allowed within sandbox`;
  every request returns 500.
- With `KONG_UNTRUSTED_LUA_SANDBOX_REQUIRES=resty.sha256,resty.string` the code works
  (`resty.sha256` for the digest, `resty.string.to_hex` for hex). `untrusted_lua` stays `sandbox`.
- No require-free route exists: `kong.tools` is nil in the sandbox; `ngx` offers only `sha1_bin` and
  `md5`, not sha256.
- The setting is global to the Kong node (it also applies to any other sandboxed Lua), so it widens the
  sandbox by exactly those two modules.

### (c) X-RL-Key absent (request has no Authorization)
- The pre-function removes any client X-RL-Key when there is no Authorization. `rate-limiting` then
  falls back to the client IP as the identifier: the Valkey key was
  `ratelimit:<route-id>:<service-id>:192.168.215.1:<window>:minute` (the Docker gateway address in this run).
- Five requests without Authorization: `200 200 200 429 429`. All unauthenticated clients behind one IP
  share one 3/min bucket. Behind the real edge (Cloud Run or a load balancer) the identifier is the
  forwarded client IP Kong resolves (`real_ip_header`); that was not measured here. E2 must test it
  with the IP limiter (`limit_by: ip`) in the production config.

### (d) Client-sent X-RL-Key is overwritten
- Four tokens each sending `X-RL-Key: fixed`, round-robin: rounds 1 to 3 all 200, round 4 all 429. Each
  token kept its own bucket. The upstream saw the sha256 of each token, never `fixed`. Valkey held 4
  keys, none built from `fixed` or from sha256("fixed").
- With no Authorization and `X-RL-Key: forged`: status 200 and no `x-rl-key` reached the upstream.

### Valkey content (AC1, AC2)
- Same token: `200 200 200 429`, 429 carries `Retry-After`. A different token: 200.
- Key format: `ratelimit:<route-id>:<service-id>:<identifier>:<window-start-ms>:minute`, type string, value
  the count, TTL 60. The identifier is exactly the header value: the 64-hex sha256, no second hash.
- `valkey-cli --scan` plus TYPE/GET/TTL dump (in `out.txt`): the raw `Bearer ...` string and the bare
  secret occur 0 times for both tokens; each token's sha256 occurs in exactly one key.

## Verdict
PASS. The arch design works on Kong 3.9.4 with the two settings above. Not measured here (out of
scope): Valkey outage and fail-open (`fault_tolerant`), the request-transformer strip, the IP limiter,
real client-IP resolution at the edge.

## Named fallback (not applied)
R24: if the pre-function could not run before rate-limiting, limit by IP only and rely on the chat-api
per-tenant budget. It is not needed, since the order is fixed by priority and measured.

## Consequences for E2
- Production kong.yml: `sync_rate: -1`; Kong container env `KONG_UNTRUSTED_LUA_SANDBOX_REQUIRES`.
- The 06 s6.5 and ADR-011 text "sync_rate 0" becomes "sync_rate -1 (synchronous)".
- A hash of the token is visible in Valkey keys and in the `X-RL-Key` header sent upstream; it is not
  reversible but stays a session identifier, so keep it out of logs and metrics labels.
