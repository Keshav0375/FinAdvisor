# ADR: Cloud SQL backup expiry while the instance is stopped (spike E12-T1)

Status: **UNVERIFIED-pending-K1.** Demo mode (no GCP project, Keshav's order 2026-11-21). Nothing in
this document is a measurement of a real instance. The official docs do not answer the question, so
the verdict below is a hypothesis with a risk attached, and the live check is written out so it can
be run in one sitting once a throwaway project exists (K1).

Docs read on 2026-11-22. The probe is `backend/scripts/spike_cloud_sql_backups.py` with the offline
tests in `backend/tests/test_spike_cloud_sql_backups.py`.

## Verdict (one line)

**Does not expire while stopped: UNVERIFIED-pending-K1.** Hypothesis H1: an automated backup taken
before the stop stays listed for the whole stop, however long, because retention is a count of
backups and a stopped instance takes no new automated backup. Docs are silent on stopped instances.

## What the official docs say (quoted, fetched 2026-11-22)

From https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/backups :

- "Automated backup retention depends on the configured retention policy in the chosen backup
  option for your instance."
- "Automated backups get pruned regularly based on the retention value configured."
- Retention is configured as the number of automated backups to keep, 1 to 365. It is a count, not a
  number of days. The page states no default.
- "Automated backups are deleted on a rolling basis, one backup per day, after the instance is
  deleted." The rolling period follows the retention settings from before the deletion.
- On-demand backups are kept until manually deleted (or their project is deleted).
- The page does not mention stopped instances or the `NEVER` activation policy anywhere. How pruning
  behaves while an instance is stopped is not stated.

From https://docs.cloud.google.com/sql/docs/postgres/admin-api/rest/v1/backupRuns/list :

- Response body: `kind` ("This is always `sql#backupRunsList`."), `items[]` ("A list of backup runs
  in reverse chronological order of the enqueued time."), `nextPageToken`.
- The `BackupRun` fields (https://docs.cloud.google.com/sql/docs/postgres/admin-api/rest/v1/backupRuns)
  include `id`, `status`, `type` (`AUTOMATED`, `ON_DEMAND` or `FINAL`), `enqueuedTime`, `startTime`,
  `endTime`, `windowStartTime`, `instance`, `location`, `backupKind`.

PITR log retention (arch 5.14, https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/configure-pitr)
is a separate setting and is not covered by this spike.

## Why the answer matters: the deletion promise

The promise, deep-dive/05-1 5.14 (R4-D9): "backups within 7 d plus ledger replay on restore."
deep-dive/07 7.4 row 31 tests "backups expire on schedule while Cloud SQL is stopped", and ADR-029
(amended 2026-11-07) records the stopped-instance expiry as an E12 spike.

**Promise status: unconfirmed, needs live check.** Under H1 the promise as worded is wrong for
demo-on-demand: the instance is stopped between demos, no new automated backup is taken, and an old
backup that holds erased personal data could outlive the 7 d window by as long as the instance stays
stopped. The ledger replay on restore still re-applies erasures, so a restore does not resurrect
erased data, but the "within 7 d" part of the sentence would not hold for the stored copy.

The ticket points at "deep-dive/04-2 4.19" for this sentence. That pointer is stale: the sentence is
in 05-1 5.14 Promise (the arch text is unchanged by this ticket).

## Two things the docs change about the method

1. Retention is a **count** (1 to 365), the arch says "7 d". They coincide only while one automated
   backup is taken per day. On a stopped instance they do not: count-based retention never reaches
   the limit, so nothing is pruned (the reasoning behind H1).
2. `gcloud sql backups create` makes an `ON_DEMAND` backup, which the docs say is kept until deleted.
   It cannot show pruning. The seed must be the `AUTOMATED` backup of the backup window, so the live
   run takes a fourth listing (`before_stop`) to identify it.

## Demo mode: what was run

`cd backend && python -m scripts.spike_cloud_sql_backups` reads `backend/tests/fixtures/cloud_sql/`:
four listings in the documented shape (`before_stop`, `before_start`, `after_start`,
`24h_after_start`) with illustrative values, and `meta.json` (retention count 1, stopped 4 days).
The output is labelled `DEMO FIXTURE, not a measurement`. The scenario in the fixture is H1: the seed
backup is listed while stopped and right after start, and goes only after the next automated backup
exists. It exists to exercise the parser and the verdict logic, not to answer the question.

## Confirm on the real project (K1)

Use a throwaway non-prod project and an instance named `fa-spike-*` (the probe refuses any other
name). Print the commands with your values:

    cd backend && python -m scripts.spike_cloud_sql_backups --mode live \
      --project <K1-PROJECT> --instance fa-spike-backup --region <REGION>

That prints, in order (retention count 1, the minimum the docs allow; record what the console
actually accepts):

    gcloud sql instances create fa-spike-backup --project=<K1> --region=<R> --database-version=POSTGRES_16 --edition=ENTERPRISE --tier=db-f1-micro --backup-start-time=03:00 --retained-backups-count=1
    # wait for the first AUTOMATED backup, then:
    gcloud sql backups list --instance=fa-spike-backup --project=<K1> --format=json     # before_stop
    gcloud sql instances patch fa-spike-backup --project=<K1> --activation-policy=NEVER
    # stay stopped longer than the retention (more than 1 day), note the days
    gcloud sql backups list --instance=fa-spike-backup --project=<K1> --format=json     # before_start
    gcloud sql instances patch fa-spike-backup --project=<K1> --activation-policy=ALWAYS
    gcloud sql backups list --instance=fa-spike-backup --project=<K1> --format=json     # after_start
    # 24 h later
    gcloud sql backups list --instance=fa-spike-backup --project=<K1> --format=json     # 24h_after_start
    gcloud sql instances delete fa-spike-backup --project=<K1> --quiet
    gcloud sql instances list --project=<K1>                                            # must show none

Record each listing with the probe (it stores gcloud's output verbatim):

    python -m scripts.spike_cloud_sql_backups --mode live --project <K1> --instance fa-spike-backup \
      --action snapshot --label before_stop --snapshots-dir /tmp/fa-spike

Then the verdict, checkable against the stored listings:

    python -m scripts.spike_cloud_sql_backups --mode live --project <K1> --instance fa-spike-backup \
      --action evaluate --snapshots-dir /tmp/fa-spike --stopped-days <N>

Paste the four raw listings, the retention value and the verdict line into this document and change
the Status line. Also check that `gcloud sql backups list` works on a stopped instance at all; if it
refuses, the "before start" listing is not available and the verdict falls back to the listing right
after start.

## Proposed fix for Keshav to decide (not implemented, arch unchanged)

If the live check shows backups do NOT expire while stopped:

- `demo-up` (before the erasure job in the start sequence) lists automated backups and deletes the
  ones older than the retention explicitly (`gcloud sql backups delete`), then the canary and the
  7 d promise hold at every start.
- or change the promise wording to "backups within 7 d of the system running", the same wording the
  live stores already use.

If they DO expire while stopped, the 5.14 promise is confirmed and the UNVERIFIED note in the
5.14 Cloud SQL backups row and ADR-029 can be removed.
