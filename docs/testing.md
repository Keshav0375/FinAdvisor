# Testing

Test entry points are `make test-unit` and `make test-integration` (see the header of the
`Makefile`). This page describes the helpers that later epics import from `backend/tests/helpers/`.

## HTTP replay (recorded fixtures)

Library: **respx 0.23.1** (httpx-native; the app's clients and the SDKs it uses are httpx based),
pinned in `backend/pyproject.toml` and `backend/poetry.lock`. The architecture leaves the library
open ("pin at implementation").

Use the `replay` fixture. It is a factory: `replay("demo_quotes")` serves
`backend/tests/fixtures/cassettes/demo_quotes.json` to every httpx client (sync and async) until the
test ends.

```python
from tests.helpers.http_replay import replay  # noqa: F401  (in the conftest next to your tests)

def test_connector(replay):
    replay("demo_quotes")
    ...
```

- A request with no recording raises `NoMatchingRecordingError` (no network). If the code under test
  catches it, the test still fails at teardown. A test that provokes it on purpose calls
  `handle.expect_unmatched()`.
- A request matches on method and URL (query order is ignored; request bodies are not matched).
  Repeated requests get the recorded responses in order, then the last one again.
- A cassette is JSON: `{"version": 1, "interactions": [{"request": {"method", "url"}, "response":
  {"status", "headers", "body", "body_encoding"}}]}`.

| Variable | Default | Meaning |
|---|---|---|
| `REPLAY_STRICT` | `1` | `0` lets unmatched requests reach the network. Refused when `CI=true`. |
| `REPLAY_RECORD` | unset | `1` records the live responses into the cassette. Local only: refused when `CI=true` (the run exits non-zero). |
| `REPLAY_CASSETTE_DIR` | `backend/tests/fixtures/cassettes` | Cassette directory. |

Record locally with `REPLAY_RECORD=1 poetry run pytest <test>`. Credentials never reach a cassette:
header and query names that look like a credential (authorization, api-key, token, secret, cookie,
password, key) are dropped, and a cassette that still mentions `authorization` or `api-key` is
refused on save and on load. Check: `grep -il 'authorization\|api-key' backend/tests/fixtures/cassettes`
prints nothing. Use demo keys when recording against a sandbox; never commit a real response that
holds personal data. The weekly live-source smoke is a separate job (not part of this helper).

## Race harness

`run_interleavings(step_a, step_b, n, seed, *, engine, invariant, setup=None, cleanup=None)` in
`tests/helpers/race_harness.py` runs two sequences of async steps (for example `erase` and `index`)
in `n` interleavings against the integration Postgres (`async_engine` fixture).

- Interleaving `i` uses the seed `seed + i`; that seed alone fixes the order of the steps, so a run is
  deterministic. Replay one reported interleaving with `run_interleavings(..., n=1, seed=<its seed>)`.
- Each step is atomic: the interleaving is between steps, so put each database transaction in its own
  step. A step that waits for a lock held across steps of the other actor times out and fails with its
  seed (`RACE_STEP_TIMEOUT_S`, default 30).
- `invariant(ctx)` returning False is a violation. By default the call raises `RaceViolationError`
  listing the seeds; with `raise_on_violation=False` it returns one `InterleavingResult` per
  interleaving (`index`, `seed`, `schedule`, `ok`).
- A step that raises fails the run (`RaceHarnessError` naming the seed); catch expected conflicts
  inside the step.

## Kafka stopped

`make kafka-stop` and `make kafka-start` stop and start only the broker of the compose stack
(`start` waits for the healthcheck). In integration tests use the `kafka_stopped` fixture
(`tests/integration/conftest.py`): it stops the broker for the test and starts it, healthy, at
teardown (also when the test fails). It is the setup for erasure with Kafka stopped (E12).

The fixture drives the compose project of `make test-integration`, which exports `KAFKA_COMPOSE=1`,
`COMPOSE_PROJECT_NAME` and `COMPOSE_ENV_FILES`. Without `KAFKA_COMPOSE=1` (for example where Kafka is a
CI service container, not a compose service) the fixture skips the test. `COMPOSE_WAIT_TIMEOUT_S`
(default 300) is how long the restart waits for healthy; `KAFKA_DOCKER_TIMEOUT_S` (default 60) bounds
the other docker calls.
