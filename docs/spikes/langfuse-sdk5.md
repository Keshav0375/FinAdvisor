# Spike E0-T1: Langfuse Python SDK 5.x with masking

Verdict: pass (demo: local OTLP capture); confirm on Langfuse Cloud when K2 keys exist

The demo run needs no account. The real 5.1.0 SDK exports to an in-process OTLP/HTTP receiver on
127.0.0.1 with the stand-in keys `demo-public-key` / `demo-secret-key`. The masking function ran before
export, and no sensitive value reached the exported spans (checked on the decoded spans and on every
raw payload byte). What the demo does not prove: that Langfuse Cloud accepts the SDK 5.x export and
serves the trace back over its API. That needs real Cloud keys (K2) and has not been run.

Commands (from `backend/`):

- Demo (no account, runs in the unit tests too):
  `poetry run python scripts/spike_langfuse_v5.py --demo` -> `PASS (demo: local OTLP capture)`, exit 0.
  With `--no-mask` it must fail: exit 1 and `SPIKE-SSN found ...` lines.
- Real run, once `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` hold the Cloud values:
  `poetry run python scripts/spike_langfuse_v5.py --live` -> exit 0 confirms the Cloud verdict. Also
  run it with `--no-mask` (must exit 1) and once with a wrong secret (must exit 2).
  Same check as a test: `LIVE_API_TESTS=1 poetry run pytest tests/test_spike_langfuse_v5.py -m live`.

E0-T8 (the wrapper) may build on the demo verdict; the Cloud run confirms it.

## Versions and host

- Installed SDK: `langfuse` 5.1.0 (exact pin in `backend/pyproject.toml`; 5.2.0 also exists on PyPI,
  ADR-018 names 5.1.0). Poetry 2.1.2, Python 3.12.16.
- Host: demo mode uses a loopback receiver (`http://127.0.0.1:<free port>`). The live run uses
  `LANGFUSE_HOST` as set. Region: not applicable yet (no Cloud project reached). Cloud region
  (US or EU) is decided per cell in ADR-031.
- The 5.x bump also moves the OpenTelemetry packages (1.41.1 to 1.45.2, new
  `opentelemetry-exporter-otlp-common` and `opentelemetry-exporter-http-transport` 0.66b2).
  `importlib-metadata` and `zipp` leave the lock. No other direct dependency changed.

## Exact parameter names (read from the installed package, `site-packages/langfuse/`)

| Purpose | Name | Source |
|---|---|---|
| Export-stage masking hook | `Langfuse(mask_otel_spans=fn)` | `_client/client.py:318` (docs `:217`) |
| Hook signature | `fn(*, params: MaskOtelSpansParams) -> Optional[MaskOtelSpansResult]` | `types.py:233-240` |
| Input to the hook | `MaskOtelSpansParams.spans: Mapping[OtelSpanIdentifier, OtelSpanData]` | `types.py:131-170` |
| Span snapshot | `OtelSpanData.attributes`, `.resource_attributes` (read-only) | `types.py:90-128` |
| Patch | `OtelSpanPatch(set_attributes=..., delete_attributes=...)`, delete runs first | `types.py:175-207` |
| Result | `MaskOtelSpansResult(span_patches={identifier: patch})` | `types.py:210-231` |
| Imports | `from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult, OtelSpanPatch` | `types.py`, re-exported `__init__.py:91-96` |
| Input attribute key | `langfuse.observation.input` | `_client/attributes.py:42` |
| Output attribute key | `langfuse.observation.output` | `_client/attributes.py:43` |
| Capture off | `@observe(capture_input=False, capture_output=False)` | `_client/observe.py:93-94`, `:104-105`, defaults `:208-216` |
| Global capture default | env `LANGFUSE_OBSERVE_DECORATOR_IO_CAPTURE_ENABLED` (True if unset) | `_client/observe.py:208-210` |
| API base | `Langfuse(base_url=...)` (`host=` is deprecated) | `_client/client.py:197`, `:305` |
| Trace id / flush | `client.get_current_trace_id()`, `client.flush()` | `_client/client.py:2261`, `:2204` |

Both `capture_input` and `capture_output` are passed to `@observe` (the decorator form); the masking
hook is a client constructor argument, not a decorator argument.

## What was verified without Cloud

- Demo mode (`--demo`, also run by the unit tests): the real 5.1.0 SDK, the real masking hook and
  the real OTLP exporter send to `OtlpSink`, an in-process receiver on 127.0.0.1. The hook is wrapped
  to record that it ran. The SDK gets its own `TracerProvider`, because the SDK otherwise attaches
  every client's exporter to the process-wide provider and a second run in one process would also
  export to the first run's closed receiver. Checks, all in `evaluate_capture`:
  - the masking function ran at least once before the spans arrived;
  - `spike-normal` input and output carry `[REDACTED]` and no `SPIKE-SSN` text;
  - `spike-sensitive` and `spike-root` carry no `langfuse.observation.input` or `.output` attribute;
  - no `SPIKE-SSN` byte appears anywhere in any received payload (names, events, resource attributes
    included).
  With `--no-mask` the same checks fail with exit 1 (`SPIKE-SSN` in clear), which shows the check
  can fail.
- 36 tests in `backend/tests/test_spike_langfuse_v5.py`: the demo path above, mask function, trace
  evaluation (marker leak, non-empty sensitive span, missing span), polling, HTTP error mapping,
  exit codes including the `--no-mask` leak giving exit 1 and an auth error giving exit 2. One test
  is marked `live` (skipped unless `LIVE_API_TESTS=1`): the real Cloud run.

## Limitations and notes

- `mask_otel_spans` runs on the BatchSpanProcessor thread, once per export batch (not per trace).
  If it raises or returns a non-`MaskOtelSpansResult`, the whole batch is dropped, never sent
  unmasked (`_client/span_exporter.py:121-125`, `:353-376`; `types.py:218-221`). The production
  wrapper must therefore never raise from the hook.
- The hook cannot change span names, ids, events, links or resource attributes (`types.py:98-100`),
  so sensitive text must stay out of those; `capture_*=False` plus the hook only cover input/output
  (and metadata attributes, which are JSON-encoded strings in v5).
- Legacy `Langfuse(mask=...)` is applied in `_client/span.py:723-760` when a span object's
  input/output/metadata is set. The docs at `_client/client.py:217` position `mask_otel_spans` for
  spans created by third-party OpenTelemetry instrumentations. This matches ADR-018 (legacy `mask=`
  does not cover raw OTel spans); it was not exercised live here.
- Trace-level input and output have no separate attribute in 5.1.0 (`_client/attributes.py` has no
  `TRACE_INPUT`); they come from the root observation, so the root span in the spike also uses
  capture off.
- `flush()` logs and retries network errors and never raises, so an auth or transport failure only
  shows in the API poll. The script maps HTTP 401/403 to exit 2 and exhausted retries to exit 2.
- SDK 5.x needs Langfuse server v4 (ADR-018). Cloud compatibility stays unconfirmed until the live
  run passes.
- Not changed: `backend/src/observability/tracing.py` and `tests/test_tracing.py` still use the v2
  API (G-15); E0-T8 replaces them.
