# ADR: Langfuse trace delete behaviour (spike E12-T3)

Status: **UNVERIFIED-pending-K2.** Demo mode (no Langfuse Cloud keys, Keshav's order 2026-11-21).
Nothing in this document is a measurement of Langfuse Cloud. The request and response shapes below
are quoted from the Langfuse source and API reference; the time until a deleted trace stops being
returned is not documented anywhere I could fetch, so it is a hypothesis with a risk attached. The
live check is written out so it can be run in one sitting once the keys exist (K2).

Sources read on 2026-11-23. The probe is `backend/scripts/spike_langfuse_delete.py`, the stub is
`backend/tests/stubs/langfuse_stub.py`, and the offline tests are `backend/tests/test_langfuse_stub.py`
and `backend/tests/test_spike_langfuse_delete.py`. No new dependency (httpx is already a dev dependency).

## Verdict (one line)

**Delete is accepted with a 2xx and a `{message}` body, and the trace stays readable for an
unmeasured time: UNVERIFIED-pending-K2.** The erasure step must not treat the delete response as
proof of erasure. It must poll `GET /api/public/traces/{id}` until 404 and keep the receipt in phase 1
until then (ADR-030).

## What the docs and source say (quoted, fetched 2026-11-23)

From the Langfuse repository, `fern/apis/server/definition/trace.yml` (the API definition the
reference is generated from), https://github.com/langfuse/langfuse (main branch):

- `delete`: "Delete a specific trace", `DELETE /traces/{traceId}` under base path `/api/public`;
  path parameter `traceId` is "The unique langfuse identifier of the trace to delete".
- `deleteMultiple`: "Delete multiple traces", `DELETE /traces`, request body `DeleteTracesRequest`
  with one property, `traceIds: list<string>` ("List of trace IDs to delete").
- Both return `DeleteTraceResponse`, one property `message: string`.

From the generated `web/public/generated/api/openapi.yml` (operations `trace_delete` and
`trace_deleteMultiple`): the documented responses are **200** (`DeleteTraceResponse`, `message`
required), **400**, **401**, **403**, **404** and **405**. There is no 202 in the reference.

From the handlers, `web/src/pages/api/public/traces/[traceId].ts` and `.../traces/index.ts`:

- single: `return { message: "Trace deleted successfully" };`
- batch: `return { message: "Traces deleted successfully" };`, body schema `DeleteTracesV1Body`.
- The handler sets no status code of its own, and the single delete is rate limited under the
  resource name `trace-delete` (so a batch erasure may meet a 429 that the reference does not list).
- The handler awaits a `traceDeletionProcessor(...)` call before it returns. What that processor does
  (queue, or delete in the stores at once) is not visible from the files fetched.

From https://langfuse.com/docs/api-and-data-platform/features/public-api: the page says nothing about
deleting traces. A data-deletion page I tried (`.../features/data-deletion`) answered 404 on this date.

Basic auth is public key : secret key, as in the E0-T1 spike (`docs/spikes/langfuse-sdk5.md`).

From the arch: ADR-030 "Langfuse deletion 'usually within 15 minutes', no confirmation"
(evidence-r4.md 2.3). I did not find that sentence on a page I could fetch; treat it as the arch's
claim, not as a confirmed provider statement.

## A gap between the ticket and the docs: 200, not 202

The ticket, ADR-030's proof row and deep-dive/07 test #24 say the delete answers **202**. The API
reference says **200**. Both cannot be right for Cloud. Consequences, all handled without a decision:

- The stub's success code is a knob (`LANGFUSE_STUB_ACCEPT_STATUS`), default **202** because the
  acceptance criteria are written against 202. Tests also run it at 200.
- The erasure client must accept **any 2xx** as "delete accepted" and must never key on 202.
- The live run records the real code; replace the default in the stub if it is 200.

## Hypotheses to settle on the live run

- **H1 (eventual deletion).** The delete call returns before the trace is gone; a GET keeps returning
  200 for some seconds to minutes, then 404. This is what ADR-030 assumes ("usually within 15
  minutes"). The stub's `honour` mode models it with a delay (default 0, 90 s in the demo, illustrative).
- **H2 (accepted, never applied).** A 2xx with the success message and the trace is still returned
  after the polling ends. That is the case test #24 needs ("stub returns 202 without deleting").
  The stub's `ignore` mode models it. Whether Cloud ever does this is unknown.
- **H3 (missing ids).** A single delete of an id that does not exist answers 404 (the reference lists
  404). A batch containing unknown ids is assumed to answer 2xx and ignore them: not documented, the
  stub does that. The live run repeats the delete after the 404 to record it.
- **H4 (transient failure).** A 5xx or a 429 on delete is retryable and the call is idempotent. The
  stub's `error` mode answers 500 for the first `error_count` delete calls (default 1), then 2xx.

## Why the answer matters: the deletion promise

ADR-030: the promise says "Langfuse provider best effort ('usually within 15 minutes'), stated as
provider-bound", and the receipt says "executed" (phase 1) before "windows closed" (phase 2), never
"erased everywhere" before phase 2. ADR-029: batch delete by trace id, retry, alert at 24 h.
Provider retention is outside the erasure promise.

**Promise status: unconfirmed, needs live check.** If H2 is real for Cloud, a 2xx proves nothing and
only the alert at 24 h catches it, which is what test #24 asserts. If H1 is real and the delay is
longer than 24 h, the alert fires on a healthy provider. The measured seconds decide the alert
threshold; until then 24 h stays.

## What the stub gives test #24

`LangfuseStub(mode, ...)` is a context manager on `127.0.0.1:<free port>`:

| mode | delete answer | GET after delete |
|------|---------------|------------------|
| `honour` | accept status (202 default) | 200 until the delete delay has passed, then 404 |
| `ignore` | accept status | 200 forever |
| `error` | 500 for the first `error_count` calls, then as `honour` | unchanged by the 500s |

Knobs (argument, then environment, then default): accept status `LANGFUSE_STUB_ACCEPT_STATUS`
(202), delete delay `LANGFUSE_STUB_DELETE_DELAY_S` (0), error count `LANGFUSE_STUB_ERROR_COUNT` (1).
The clock is injected, so tests need no sleeps. It answers 401 on bad basic auth, 400 on a bad batch
body, 404 on a missing trace, and takes `POST /api/public/ingestion` trace-create events so the live
procedure can be run against it. The probe's own knobs are `LANGFUSE_DELETE_TIMEOUT_S` (10 s per
request), `LANGFUSE_DELETE_POLL_INTERVAL_S` (15 s) and `LANGFUSE_DELETE_POLL_TIMEOUT_S` (1800 s).

## Demo mode: what was run

`cd backend && python -m scripts.spike_langfuse_delete` runs the probe against the stub in the three
modes on a virtual clock and prints every verdict labelled `DEMO STUB, not a measurement`. It
exercises the client, the polling and the verdict logic. It does not answer the questions above.

## Confirm on the real project (K2)

Use a Langfuse Cloud project that holds no real data (the E0 project is fine; the probe only touches
traces it creates, named `fa-spike-single-<RUN>` and `fa-spike-batch-<RUN>`). Export the keys, then:

    cd backend
    export LANGFUSE_HOST=<https://cloud.langfuse.com or the cell's region host>
    export LANGFUSE_PUBLIC_KEY=<pk-lf-...> LANGFUSE_SECRET_KEY=<sk-lf-...>
    python -m scripts.spike_langfuse_delete --mode live --action run --run-id <RUN> --out /tmp/fa-spike-langfuse.json

The probe creates both traces through `POST /api/public/ingestion` (a `trace-create` event; this
request shape is written from memory, not fetched, so if Langfuse refuses it, create the traces with
the SDK as in `backend/scripts/spike_langfuse_v5.py`), waits until each answers 200, deletes the
first with the single call and the second with the batch call, reads the status code and message,
reads GET straight after, then polls GET every 15 s until 404 and records the seconds. It then
repeats the single delete on the gone id. The same steps by hand:

    python -m scripts.spike_langfuse_delete --mode live --action commands --run-id <RUN>

which prints the curl commands, in this order for each of `single` and `batch`: ingest the trace,
GET (must be 200), the delete (`DELETE $LANGFUSE_HOST/api/public/traces/<id>`, or `DELETE
$LANGFUSE_HOST/api/public/traces` with the body `{"traceIds": ["<id>"]}`), GET straight after, GET
every 15 s until 404 (note the seconds), then the delete again.

Paste the result file, the status codes and the seconds into this document, replace the verdict line
and the Status line, correct the stub default if the code is 200 not 202, and write the measured
seconds against the 24 h alert threshold. If the poll ends without a 404 (the 1800 s default), repeat
once with `LANGFUSE_DELETE_POLL_TIMEOUT_S=90000` (25 h) before concluding H2.

## Proposed handling for the erasure step (not implemented, out of scope here)

- Accept any 2xx. Retry 5xx and 429 with backoff and jitter (the call is idempotent), per ADR-029.
- Do not mark Langfuse "windows closed" on the delete response. Poll GET until 404, or let the next
  erasure job run check it. Keep the receipt in phase 1 until then.
- Alert at 24 h of a pending Langfuse step, the number to be revisited with the measured seconds.
