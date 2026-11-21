# stub-chat-api

Minimal FastAPI stub that stands in for `chat-api` in gateway tests and spikes (E2). It echoes
the headers it receives, emits timestamped SSE, and mocks the GCE metadata identity endpoint.

**Test tooling only. Never deploy it outside spike and test environments. It is not a substitute
for chat-api (E4).**

## Run

```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python -m uvicorn app:app --port 8080

docker build -t fa-stub-chat-api tools/stub-chat-api
docker run --rm -p 8080:8080 fa-stub-chat-api
bash tools/stub-chat-api/tests/check_image.sh
```

`tests/check_image.sh` is the acceptance step for the image: it checks every `FROM` is pinned by
digest, builds the image, starts it with `docker run -d --rm --name fa-stub-<pid>` on a free host
port, curls `/echo`, `/sse` and the identity endpoint, checks the container is non-root, then
stops it with `docker stop`. It is not wired into CI yet; the CI rewrite ticket adds a job.

## Endpoints

### `GET|POST /echo`

Returns JSON:

```
{
  "method": "GET",
  "path": "/echo",
  "headers": { "<lowercase name>": "<value>", ... },
  "serverless_auth": null | {"aud", "email", "exp"} | {"error": "<reason>"}
}
```

- Headers are echoed as received under their lowercase names (`x-rl-key`, `x-request-id`,
  `traceparent`, `x-user-id`, `x-tenant-id` show what the gateway stripped or added).
- `authorization` is replaced by `{"sha256": <hex of the full header value>, "length": <bytes>}`.
  The raw value is never returned. Request bodies are never echoed.
- `x-serverless-authorization` and `proxy-authorization` get the same `{sha256, length}`
  treatment (ASSUMPTION: a real gateway test carries a real Google ID token in the first one).
- `serverless_auth` is the payload of `X-Serverless-Authorization` decoded WITHOUT verifying the
  signature (`Bearer ` prefix stripped): `aud`, `email`, `exp`. It is `null` when the header is
  absent and `{"error": ...}` when the header is not a decodable JWT (never a 500).

### `GET /sse?events=N&interval_ms=M`

`text/event-stream`, one flush per event, with `Cache-Control: no-cache` and
`X-Accel-Buffering: no`. Defaults: `events=10`, `interval_ms=100`.

```
id: 1
event: tick
data: {"seq":1,"emit_ts_ns":1763700000000000000}

```

- Event name `tick` (knob `STUB_SSE_EVENT_NAME`). `id` equals `seq`. `seq` starts at 1.
- `emit_ts_ns` is server wall-clock `time.time_ns()`, forced strictly increasing within a stream.
- Out-of-range values return 422 (see the limits below).

### `GET /metadata-mock/computeMetadata/v1/instance/service-accounts/default/identity?audience=...`

Returns a plain-text unsigned JWT (`alg: none`, empty signature) like the GCE metadata server.
Claims: `aud` (the `audience` parameter), `azp` and `email` (knob `STUB_METADATA_EMAIL`),
`email_verified`, `iss` (`https://accounts.google.com`), `iat`, `exp` (`iat` plus
`STUB_METADATA_TOKEN_TTL_S`). The response carries `Metadata-Flavor: Google`.
ASSUMPTION: the request does not need the `Metadata-Flavor: Google` header. A missing, empty or
over-long (2048) `audience` returns 422.

## Settings

All knobs are environment variables with defaults, read once at startup. A bad value stops the
process with a `ConfigError` naming the variable.

| Variable | Default | Meaning |
| --- | --- | --- |
| `STUB_HOST` | `0.0.0.0` | Bind address |
| `STUB_PORT` | `8080` | Listen port (image default `8080`) |
| `STUB_SSE_EVENT_NAME` | `tick` | SSE `event:` name |
| `STUB_SSE_MAX_EVENTS` | `1000` | Largest accepted `events` |
| `STUB_SSE_MAX_INTERVAL_MS` | `5000` | Largest accepted `interval_ms` |
| `STUB_SSE_MAX_DURATION_S` | `60` | Largest accepted `events * interval_ms`, so every stream ends |
| `STUB_METADATA_EMAIL` | `stub@fa-gateway.iam.gserviceaccount.com` | `email` claim of the mock token |
| `STUB_METADATA_TOKEN_TTL_S` | `3600` | Mock token lifetime |
| `STUB_KEEPALIVE_TIMEOUT_S` | `5` | uvicorn keep-alive timeout |

## Choices the ticket and arch leave open

- SSE event name `tick`, data `{"seq", "emit_ts_ns"}`, `id` equals `seq` (follows the arch 6.2
  shape: integer id, event name, JSON data).
- `/echo` JSON shape as above.
- Mock token claims as above.
- Only the endpoints above exist: no `/v1/chat`, no `/readyz`. The image `HEALTHCHECK` probes
  `/echo` with a 2 s timeout.

## Dependencies

`requirements.txt` is fully pinned (fastapi and uvicorn at the versions in `backend/poetry.lock`,
plus their transitive packages from `pip freeze`). `requirements-dev.txt` adds pytest,
pytest-cov, httpx and ruff, pinned to the backend lock versions. The stub has its own
requirements so `backend/pyproject.toml` and `poetry.lock` stay untouched. The base image is
`python:3.12-slim` by the same digest as `backend/Dockerfile`.
