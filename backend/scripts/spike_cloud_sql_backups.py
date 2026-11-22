"""E12-T1 spike: do Cloud SQL automated backups expire while the instance is stopped?

Run by hand, never in the PR gate, as a module from backend/ (not `python scripts/...`):

    cd backend && python -m scripts.spike_cloud_sql_backups

Demo mode (the default) reads the recorded fixture in tests/fixtures/cloud_sql, which has the
documented shape of backupRuns.list but illustrative values, and prints a verdict labelled
UNVERIFIED-pending-K1. Nothing is measured. Live mode needs a throwaway GCP project (K1):

    --mode live --project P --instance fa-spike-X --region R             print the gcloud commands
    --mode live --project P --instance fa-spike-X --action snapshot \\
        --label before_stop --snapshots-dir DIR                           record one listing
    --mode live --project P --instance fa-spike-X --action evaluate \\
        --snapshots-dir DIR --stopped-days N                              verdict from the listings

The findings and the reasoning are in docs/adr/cloud-sql-backup-expiry.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

log = structlog.get_logger()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_FIXTURES_DIR = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "cloud_sql"

# Everything below is spike-local on purpose: the script is run by hand, once, against a throwaway
# instance. None of it is a product knob, so none of it goes into Settings.
GCLOUD = "gcloud"
GCLOUD_TIMEOUT_S = 120.0
# The live probe refuses any instance that does not carry this prefix, so it cannot touch prod.
SPIKE_INSTANCE_PREFIX = "fa-spike-"
DB_VERSION = "POSTGRES_16"
EDITION = "ENTERPRISE"
TIER = "db-f1-micro"
BACKUP_START_TIME = "03:00"
# Listings taken in order: the seed backup is read from the first one.
SNAPSHOT_LABELS = ("before_stop", "before_start", "after_start", "24h_after_start")
DEFAULT_RETAINED_BACKUPS_COUNT = 1
AUTOMATED = "AUTOMATED"

EXPIRES = "expires while stopped"
DOES_NOT_EXPIRE = "does not expire while stopped"
INCONCLUSIVE = "inconclusive"
UNVERIFIED = "UNVERIFIED-pending-K1"


class SpikeError(Exception):
    """A probe step failed in a way the operator must see (missing file, gcloud failure)."""


class BackupRun(BaseModel):
    """The fields of the Admin API BackupRun that the verdict needs; the rest is ignored."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    status: str | None = None
    type: str | None = None
    instance: str | None = None
    enqueued_time: str | None = Field(default=None, alias="enqueuedTime")


@dataclass(frozen=True)
class Snapshots:
    before_stop: list[BackupRun]
    before_start: list[BackupRun]
    after_start: list[BackupRun]
    later: list[BackupRun]

    def by_label(self) -> dict[str, list[BackupRun]]:
        return dict(
            zip(
                SNAPSHOT_LABELS,
                (self.before_stop, self.before_start, self.after_start, self.later),
                strict=True,
            )
        )


@dataclass(frozen=True)
class Verdict:
    verdict: str
    detail: str
    verified: bool

    def line(self) -> str:
        text = f"VERDICT: {self.verdict} ({self.detail})"
        if not self.verified:
            text += f" [{UNVERIFIED}; DEMO FIXTURE, not a measurement]"
        return text


def parse_backup_runs(payload: Any) -> list[BackupRun]:
    """Parse a backupRuns.list body ({kind, items, nextPageToken}) or gcloud's bare JSON list."""
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        if payload.get("nextPageToken"):
            raise SpikeError("the listing has a nextPageToken; list all pages before parsing")
        items = payload.get("items", [])
    else:
        raise SpikeError("not a backupRuns.list body or a list of backup runs")
    try:
        return [BackupRun.model_validate(item) for item in items]
    except ValidationError as exc:
        raise SpikeError(f"invalid BackupRun: {exc.errors()[0]['msg']}") from exc


def compute_verdict(
    snapshots: Snapshots, retained_backups_count: int, stopped_days: int, verified: bool
) -> Verdict:
    """Compare the seed backups (AUTOMATED, listed before the stop) across the later listings.

    The retention is a count of backups in the docs. One automated backup a day makes the count
    equal to days, so "stopped longer than the retention" is checked as stopped_days greater than
    the count. A stopped instance takes no new automated backup, so that is the only way an old
    one can be outside the window.
    """
    seed = {run.id for run in snapshots.before_stop if run.type == AUTOMATED}
    if not seed:
        return Verdict(
            INCONCLUSIVE, "no AUTOMATED backup in the before-stop listing to follow", verified
        )
    if stopped_days <= retained_backups_count:
        return Verdict(
            INCONCLUSIVE,
            f"stopped {stopped_days} d, not longer than the retention {retained_backups_count}",
            verified,
        )

    def present(runs: list[BackupRun]) -> bool:
        return any(run.id in seed for run in runs)

    if not present(snapshots.before_start):
        return Verdict(EXPIRES, "the seed backup is absent from the before-start listing", verified)
    if not present(snapshots.after_start):
        return Verdict(
            DOES_NOT_EXPIRE,
            "the seed backup was listed while stopped and pruned between 'before start' "
            "and 'after start'",
            verified,
        )
    if not present(snapshots.later):
        return Verdict(
            DOES_NOT_EXPIRE,
            "the seed backup was listed while stopped and right after start, and "
            "pruned between 'after start' and 24 h after start",
            verified,
        )
    return Verdict(
        DOES_NOT_EXPIRE,
        "the seed backup is still listed 24 h after start, beyond the retention",
        verified,
    )


def live_commands(
    project: str, instance: str, region: str, retained_backups_count: int
) -> list[str]:
    """The exact gcloud commands of the live run, in order. Comment lines start with '#'."""
    flags = f"--project={project}"
    listing = f"{GCLOUD} sql backups list --instance={instance} {flags} --format=json"
    return [
        f"{GCLOUD} sql instances create {instance} {flags} --region={region} "
        f"--database-version={DB_VERSION} --edition={EDITION} --tier={TIER} "
        f"--backup-start-time={BACKUP_START_TIME} "
        f"--retained-backups-count={retained_backups_count}",
        "# wait for the first AUTOMATED backup (the window after creation), then record it",
        listing,
        "# snapshot label: before_stop",
        f"{GCLOUD} sql instances patch {instance} {flags} --activation-policy=NEVER",
        "# stay stopped longer than the retention; note the days as --stopped-days",
        listing,
        "# snapshot label: before_start",
        f"{GCLOUD} sql instances patch {instance} {flags} --activation-policy=ALWAYS",
        listing,
        "# snapshot label: after_start",
        "# 24 h later",
        listing,
        "# snapshot label: 24h_after_start",
        f"{GCLOUD} sql instances delete {instance} {flags} --quiet",
        f"{GCLOUD} sql instances list {flags}",
    ]


def check_spike_instance(instance: str) -> None:
    if not instance.startswith(SPIKE_INSTANCE_PREFIX):
        raise SpikeError(
            f"instance {instance!r} does not start with {SPIKE_INSTANCE_PREFIX!r}; "
            "the probe only touches throwaway spike instances"
        )


def run_gcloud(cmd: list[str]) -> str:
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=GCLOUD_TIMEOUT_S
        )
    except FileNotFoundError as exc:
        raise SpikeError(f"{cmd[0]} is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise SpikeError(f"{' '.join(cmd)} timed out after {GCLOUD_TIMEOUT_S} s") from exc
    except subprocess.CalledProcessError as exc:
        raise SpikeError(f"gcloud failed: {(exc.stderr or '').strip()}") from exc
    return done.stdout


def gcloud_backups_list(
    project: str, instance: str, runner: Callable[[list[str]], str] = run_gcloud
) -> str:
    return runner(
        [
            GCLOUD,
            "sql",
            "backups",
            "list",
            f"--instance={instance}",
            f"--project={project}",
            "--format=json",
        ]
    )


def snapshot_path(directory: Path, label: str) -> Path:
    return directory / f"backups_{label}.json"


def write_snapshot(directory: Path, label: str, raw: str) -> Path:
    """Store gcloud's output verbatim, after checking that it parses as backup runs."""
    try:
        parse_backup_runs(json.loads(raw))
    except json.JSONDecodeError as exc:
        raise SpikeError("gcloud output is not valid JSON") from exc
    directory.mkdir(parents=True, exist_ok=True)
    target = snapshot_path(directory, label)
    target.write_text(raw.rstrip("\n") + "\n", encoding="utf-8")
    return target


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SpikeError(f"cannot read {path.name}: {exc.strerror}") from exc
    except json.JSONDecodeError as exc:
        raise SpikeError(f"{path.name} is not valid JSON") from exc


def load_snapshots(directory: Path) -> tuple[Snapshots, dict[str, Any]]:
    runs = [parse_backup_runs(read_json(snapshot_path(directory, x))) for x in SNAPSHOT_LABELS]
    meta_path = directory / "meta.json"
    meta: dict[str, Any] = read_json(meta_path) if meta_path.exists() else {}
    return Snapshots(*runs), meta


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cloud SQL backup expiry probe (E12-T1 spike)")
    parser.add_argument("--mode", choices=("demo", "live"), default="demo")
    parser.add_argument(
        "--action", choices=("commands", "snapshot", "evaluate"), default="commands"
    )
    parser.add_argument("--project", help="live: the throwaway GCP project (K1)")
    parser.add_argument("--instance", help=f"live: a throwaway instance, {SPIKE_INSTANCE_PREFIX}*")
    parser.add_argument("--region", help="live, action commands: Cloud SQL region")
    parser.add_argument("--label", choices=SNAPSHOT_LABELS, help="live, action snapshot")
    parser.add_argument("--snapshots-dir", type=Path, default=None)
    parser.add_argument(
        "--retained-backups-count", type=int, default=DEFAULT_RETAINED_BACKUPS_COUNT
    )
    parser.add_argument("--stopped-days", type=int, default=None)
    return parser


def report_for(
    snapshots: Snapshots, meta: dict[str, Any], retained: int, stopped: int, demo: bool
) -> dict[str, Any]:
    return {
        "demo": demo,
        "note": meta.get("note"),
        "instance": meta.get("instance"),
        "retained_backups_count": retained,
        "stopped_days": stopped,
        "listings": {k: [r.id for r in v] for k, v in snapshots.by_label().items()},
    }


def evaluate(directory: Path, args: argparse.Namespace, demo: bool) -> int:
    snapshots, meta = load_snapshots(directory)
    retained = int(meta.get("retained_backups_count", args.retained_backups_count))
    stopped = args.stopped_days if args.stopped_days is not None else meta.get("stopped_days")
    if stopped is None:
        raise SpikeError("the stopped days are unknown: pass --stopped-days")
    result = compute_verdict(snapshots, retained, int(stopped), verified=not demo)
    report = report_for(snapshots, meta, retained, int(stopped), demo)
    sys.stdout.write(json.dumps(report, indent=2) + "\n" + result.line() + "\n")
    return 0


def run_live(
    parser: argparse.ArgumentParser, args: argparse.Namespace, runner: Callable[[list[str]], str]
) -> int:
    if not args.project or not args.instance:
        parser.error("--mode live needs --project and --instance")
    if args.action == "commands":
        if not args.region:
            parser.error("--action commands needs --region")
        check_spike_instance(args.instance)
        cmds = live_commands(args.project, args.instance, args.region, args.retained_backups_count)
        sys.stdout.write("\n".join(cmds) + "\n")
        return 0
    if args.action == "snapshot":
        if not args.label:
            parser.error("--action snapshot needs --label")
        if args.snapshots_dir is None:
            parser.error("--action snapshot needs --snapshots-dir")
        check_spike_instance(args.instance)
        raw = gcloud_backups_list(args.project, args.instance, runner)
        target = write_snapshot(args.snapshots_dir, args.label, raw)
        sys.stdout.write(f"wrote {target}\n")
        return 0
    if args.stopped_days is None:
        parser.error("--action evaluate needs --stopped-days")
    if args.snapshots_dir is None:
        parser.error("--action evaluate needs --snapshots-dir")
    check_spike_instance(args.instance)
    return evaluate(args.snapshots_dir, args, demo=False)


def main(argv: list[str] | None = None, runner: Callable[[list[str]], str] = run_gcloud) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.mode == "demo":
            return evaluate(args.snapshots_dir or DEFAULT_FIXTURES_DIR, args, demo=True)
        return run_live(parser, args, runner)
    except SpikeError as exc:
        log.error("spike_failed", error=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
