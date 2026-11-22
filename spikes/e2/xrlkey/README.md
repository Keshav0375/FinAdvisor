# E2-T1 spike: X-RL-Key keying on Kong 3.9.4

Scratch harness, not shipped. Result and verdict: `docs/adr/xrl-key-keying.md`.

```
bash spikes/e2/xrlkey/run.sh | tee spikes/e2/xrlkey/out.txt
```

Needs Docker and curl. Everything is local: `kong:3.9.4` (DB-less), `valkey/valkey:8.0`, and the
`tools/stub-chat-api` image (built by the script as `fa-stub-chat-api:e2t1`) as upstream, with demo
tokens. The script exits 1 if any check fails; `out.txt` is the committed run.

- `kong.yml`: pre-function sets `X-RL-Key = sha256(Authorization)`; rate-limiting by that header, 3/min,
  policy redis, `sync_rate: -1`.
- The three containers share valkey's network namespace, so addresses are `127.0.0.1` and no network
  object is created. Only Kong's proxy is published, on an ephemeral port (set `XRL_HOST_PORT` to pin it).
- Containers run with `-d --rm` and are stopped with `docker stop` on exit.
- Knobs: `XRL_KONG_IMAGE`, `XRL_VALKEY_IMAGE`, `XRL_STUB_IMAGE`, `XRL_HOST_PORT`, `XRL_WAIT_S`,
  `XRL_WINDOW_GUARD_S`. A block that would start in the last seconds of a minute waits for the next
  window so a 3/min bucket is never split.
