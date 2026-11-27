"""E12-T2 integration tests against a real scratch dataset (needs K1, a throwaway GCP project).

They run only with LIVE_API_TESTS=1 plus the scratch values below, which come from the live run in
docs/adr/bigquery-erasure-probe.md. The "older than the window" proof needs a real wait of more than
48 h after the delete, so the timestamps are recorded by hand, not produced inside the test.

    BQ_SPIKE_PROJECT, BQ_SPIKE_REGION    scratch project and the dataset location
    BQ_SPIKE_TABLE                       project.fa_spike_x.table that held the deleted marker row
    BQ_SPIKE_TS_INSIDE                   t_before_delete, probed within 48 h of the delete
    BQ_SPIKE_TS_OLD                      the same timestamp, probed more than 48 h later
    BQ_SPIKE_INHERIT_DATASET             fa_spike_x_inherit, created with no options after the ALTER
"""

from __future__ import annotations

import os
from datetime import datetime

import pytest

from scripts import spike_bigquery_time_travel as spike
from src.config import Settings
from src.lifecycle import bq_probe

pytestmark = pytest.mark.live


def need(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.skip(f"{name} is not set (needs the K1 scratch project)")
    return value


def run_query() -> bq_probe.RunQuery:
    settings = Settings()  # type: ignore[call-arg]
    return bq_probe.bq_query_runner(
        need("BQ_SPIKE_PROJECT"),
        need("BQ_SPIKE_REGION"),
        bq_bin=settings.bq_bin,
        timeout_s=settings.bq_timeout_s,
    )


def parse(name: str) -> datetime:
    return datetime.fromisoformat(need(name))


# AC1 against a scratch dataset: the deleted row is readable inside the window.
def test_live_probe_open_inside_window() -> None:
    table = need("BQ_SPIKE_TABLE")
    spike.check_spike_dataset(table.split(".")[1])
    result = bq_probe.bq_time_travel_probe(
        table, parse("BQ_SPIKE_TS_INSIDE"), run_query=run_query()
    )
    assert result == "open"


# AC1: a timestamp older than the window is rejected by BigQuery and maps to closed.
def test_live_probe_closed_outside_window() -> None:
    table = need("BQ_SPIKE_TABLE")
    spike.check_spike_dataset(table.split(".")[1])
    result = bq_probe.bq_time_travel_probe(table, parse("BQ_SPIKE_TS_OLD"), run_query=run_query())
    assert result == "closed"


# AC2: a dataset created with no options after ALTER PROJECT default 48 reads 48.
def test_live_new_dataset_inherits_48() -> None:
    dataset = need("BQ_SPIKE_INHERIT_DATASET")
    spike.check_spike_dataset(dataset)
    hours = bq_probe.read_time_travel_hours(
        need("BQ_SPIKE_PROJECT"), need("BQ_SPIKE_REGION"), dataset, run_query=run_query()
    )
    assert hours == bq_probe.EXPECTED_HOURS
