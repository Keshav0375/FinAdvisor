# ADR: BigQuery erasure probe, the observable time-travel window (spike E12-T2)

Status: **UNVERIFIED-pending-K1.** Demo mode (no GCP project, Keshav's order 2026-11-21). Nothing in
this document is a measurement of a real dataset. What the docs state is quoted and marked
`documented`; what they do not state is marked `UNVERIFIED-pending-K1` and has a command in the live
proof plan below.

Docs read on 2026-11-23. The probe is `backend/scripts/spike_bigquery_time_travel.py` with the
offline tests in `backend/tests/test_spike_bigquery_time_travel.py` and the live tests (skipped
without K1) in `backend/tests/test_bigquery_time_travel_live.py`.

## Verdict (one line)

**Only the time-travel window is observable; the 7 d fail-safe is not queryable.** A probe can show
"open" while a deleted row is still readable through `FOR SYSTEM_TIME AS OF`, and "closed" once
BigQuery refuses the snapshot time. The fail-safe window after it is stated as a documented window,
never measured (ADR-030).

## What the official docs say (quoted, fetched 2026-11-23)

From https://docs.cloud.google.com/bigquery/docs/time-travel (page "Last updated 2026-11-18 UTC"):

- "You can set the duration of the time travel window, from a minimum of two days to a maximum of
  seven days."
- "During the fail-safe period, deleted data is automatically retained for an additional seven days
  after the time travel window ... The fail-safe period isn't configurable."
- "You can't query data in fail-safe storage directly, and you can't copy a table from fail-safe
  storage until Cloud Customer Care grants temporary fail-safe recovery access on the dataset."
- Not supported for time travel: external tables, temporary cached results, temporary session
  tables, tables in external datasets.

From https://docs.cloud.google.com/bigquery/docs/default-configuration (page "Last updated
2026-11-20 UTC"):

- "default_max_time_travel_hours: The default time travel window in hours for new datasets. This
  duration must be within the range of 48 to 168, inclusive, and must be divisible by 24. Changing
  the default max time travel hours does not affect existing datasets."

## Wording: "not queryable", not "unrecoverable"

ADR-030 says the fail-safe is "not queryable" and that is still right. The page above adds that Cloud
Customer Care can grant temporary recovery access to fail-safe storage and that a table can then be
copied out of it. So the fail-safe data is recoverable by Google on request, which is a reason to
keep calling the window a **documented window**, and not "unrecoverable" (this ADR, the receipt text
and the privacy notice must not use that word). No ADR text is edited by this ticket; the researcher
decides whether ADR-030 and deep-dive 05-1 5.14 get a sentence about Customer Care access.

## Verdict table

| Statement | Status | Source |
|---|---|---|
| The time-travel window minimum is 48 h (2 d) | `documented` | time-travel page |
| The default for new datasets is set per project and must be 48 to 168 h in steps of 24 | `documented` | default-configuration page |
| Changing the default does not change existing datasets | `documented` | default-configuration page |
| The fail-safe is 7 d, fixed, not configurable | `documented` | time-travel page |
| The fail-safe cannot be queried | `documented` | time-travel page |
| A `FOR SYSTEM_TIME AS OF` query older than the window fails | UNVERIFIED-pending-K1 | docs are silent on the behaviour |
| The error text of that failure | UNVERIFIED-pending-K1 | docs are silent; the probe matches `Invalid snapshot time` plus `time travel window` |
| `ALTER PROJECT ... SET OPTIONS (` ``region-R.default_max_time_travel_hours`` `= 48)` syntax is accepted per region | UNVERIFIED-pending-K1 | the docs name the option; the exact statement shape is to be run |
| A dataset created with no options after the ALTER reads `max_time_travel_hours` 48 | UNVERIFIED-pending-K1 | the docs imply it (default "for new datasets") |
| Deleted rows are readable inside the window | UNVERIFIED-pending-K1 | the docs describe the feature, no run here |

## Receipt vocabulary (ADR-030)

| Window | Receipt phase 2 term | How |
|---|---|---|
| BigQuery time travel, 48 h | observable: `erasure-verify` runs `bq_time_travel_probe` after the window timer | probe, result `closed` |
| BigQuery 7 d fail-safe | `documented` | stated from the docs above, **not measured**, no query can read it |

The receipt must never print "verified closed" for the fail-safe. "Trigger to revisit" (ADR-030): an
observable fail-safe probe appears.

## The probe function

`bq_time_travel_probe(table, ts, *, run_query) -> "open" | "closed"`:

- Runs ``SELECT COUNT(*) AS n FROM `table` FOR SYSTEM_TIME AS OF TIMESTAMP 'ts'`` (ts as UTC).
- Rows counted: **open**. No rows at ts: **closed**. BigQuery refuses the snapshot time as outside
  the window: **closed**.
- Anything else raises (permission, quota, a missing table, an error text it does not know). It never
  maps an unknown error to `closed`: a false `closed` would sign a receipt falsely. The price is that
  a changed BigQuery message makes the probe raise until the text is updated, which is visible.
- The message variant "Cannot read before ..." is not accepted: it may also mean "before the table
  existed", which is not "outside the window". Open question for K1: record the exact text of both
  cases.
- `run_query` is injected. Live mode uses `bq query` through `subprocess.run` with an argv list, no
  shell, and a timeout; no new dependency (`google-cloud-bigquery` is not in the stack). Table, project
  and region names are matched against a pattern before they enter SQL.
- **Limit:** the count is over the whole table. The scratch table holds only the marker row. For
  erasure-verify, a row-scoped variant is needed (a later ticket; the erasure DML is out of scope).
- A probe at a timestamp after the delete but inside the window returns `closed` because the row is
  absent. erasure-verify must probe at a timestamp just before the delete (the live plan records
  `t_before_delete` for that).

## Demo mode: what was run

`cd backend && python -m scripts.spike_bigquery_time_travel` reads `backend/tests/fixtures/bigquery/`:
a getQueryResults body with one row (inside the window), a REST 400 error body (older than the
window), and a SCHEMATA_OPTIONS result with `48`. The probe and `read_time_travel_hours` run on them
through the same code as live mode. The output is labelled `DEMO FIXTURE, not a measurement`. The
error text in the fixture is from memory and is the thing K1 must confirm.

## Live proof plan (needs K1 and a real wait of more than 48 h)

48 h is the documented minimum window, so there is no shorter dataset to shorten the run: the proof
that a timestamp older than the window is refused needs a real wait of more than 48 h (plus a
margin) after the delete. ADR-030 says "dataset configured with the minimum 48 h"; that is this
dataset. Use a throwaway project and datasets named `fa_spike_*` (the probe refuses other names).

    cd backend && python -m scripts.spike_bigquery_time_travel --mode live \
      --project <K1-PROJECT> --dataset fa_spike_tt --region <REGION>

That prints, in order (each line quoted with shlex, run them one by one):

1. `ALTER PROJECT` default 48 for the region.
2. `bq mk --dataset --max_time_travel_hours=48 <P>:fa_spike_tt`.
3. `bq mk --dataset <P>:fa_spike_tt_inherit` (no options).
4. `CREATE TABLE`, `INSERT` of the marker row.
5. `SELECT FORMAT_TIMESTAMP(...) AS t`: this is `t_before_delete`, from BigQuery's clock. Then `DELETE`.
6. `--action probe --ts <t_before_delete>`, expect `-> open` (acceptance criterion 1, inside).
7. Wait more than 48 h from step 5.
8. The same probe, expect `-> closed` (criterion 1, older than the window). Also keep the raw error
   text `bq` prints with a direct `bq query` of the same SQL, and paste it here.
9. `--dataset fa_spike_tt_inherit --action schemata`, expect `max_time_travel_hours = 48` (criterion 2).
10. `bq rm -r -f -d` on both datasets.

Then run the live tests with the values from the run (they skip without `LIVE_API_TESTS=1`):

    BQ_SPIKE_PROJECT=... BQ_SPIKE_REGION=... BQ_SPIKE_TABLE=<P>.fa_spike_tt.marker \
    BQ_SPIKE_TS_INSIDE=<t> BQ_SPIKE_TS_OLD=<t> BQ_SPIKE_INHERIT_DATASET=fa_spike_tt_inherit \
    LIVE_API_TESTS=1 pytest tests/test_bigquery_time_travel_live.py

Paste the raw outputs and the error text into this document and change the Status line. If the
error text differs from `Invalid snapshot time ... time travel window`, update the two constants in
the script and the fixture in the same commit.

## Assumptions

- ASSUMPTION: the window is a dataset option (`max_time_travel_hours`), so the ticket's "create a
  table with `max_time_travel_hours = 48`" is applied at dataset level; the docs quoted here set the
  duration without saying where, and K1 must confirm that no table-level option exists.
- ASSUMPTION: no `google-cloud-bigquery` dependency; the `bq` CLI behind an injected runner, like
  E12-T1 uses `gcloud`.
- ASSUMPTION: the printed `bq mk --max_time_travel_hours` flag and the `ALTER PROJECT` statement
  shape are right (UNVERIFIED-pending-K1).
