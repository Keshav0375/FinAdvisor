"""Race harness: run two sequences of async steps in seeded interleavings against Postgres.

`run_interleavings(step_a, step_b, n, seed, ...)` runs `n` interleavings. Interleaving `i` has the
seed `seed + i`: that seed alone decides in which order the steps of A and B run (each actor keeps
its own order), so `run_interleavings(..., n=1, seed=result.seed)` replays a reported interleaving
exactly. Steps run one at a time, so a run is deterministic; the interleaving is at step
granularity (a step is atomic), which is how E12 splits "erase" and "index" into their database
transactions. A step that waits for a lock held across steps of the other actor cannot finish and
fails with its seed after `RACE_STEP_TIMEOUT_S`.

Per interleaving: `setup(ctx)`, the steps in the scheduled order, `invariant(ctx)` (False is a
violation), then `cleanup(ctx)` always. A step that raises propagates (after cleanup), tagged with
the seed: a test whose steps expect a conflict catches it inside the step.

Knob: RACE_STEP_TIMEOUT_S (default 30), seconds one step may take.
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

STEP_TIMEOUT_S = float(os.environ.get("RACE_STEP_TIMEOUT_S", "30"))


@dataclass
class RaceContext:
    """What the steps of one interleaving share: the database and a scratch dict."""

    engine: AsyncEngine
    seed: int
    state: dict[str, Any] = field(default_factory=dict)


Step = Callable[[RaceContext], Awaitable[None]]
Hook = Callable[[RaceContext], Awaitable[None]]
Invariant = Callable[[RaceContext], Awaitable[bool]]


@dataclass(frozen=True)
class InterleavingResult:
    index: int
    seed: int
    schedule: str
    ok: bool


class RaceHarnessError(Exception):
    """The harness was misused, or a step failed or timed out (the message names the seed)."""


class RaceViolationError(AssertionError):
    """The invariant was false for at least one interleaving; the message lists the seeds."""

    def __init__(self, results: Sequence[InterleavingResult]) -> None:
        self.results = list(results)
        bad = [r for r in self.results if not r.ok]
        listing = ", ".join(f"seed={r.seed} schedule={r.schedule}" for r in bad[:10])
        more = f" (+{len(bad) - 10} more)" if len(bad) > 10 else ""
        total = len(self.results)
        super().__init__(
            f"invariant violated in {len(bad)} of {total} interleavings: {listing}{more}"
        )


def build_schedule(len_a: int, len_b: int, seed: int) -> str:
    """The order of the steps as a string of 'A' and 'B', one letter per step, fixed by the seed."""
    letters = ["A"] * len_a + ["B"] * len_b
    random.Random(seed).shuffle(letters)
    return "".join(letters)


async def _run_step(step: Step, ctx: RaceContext, label: str) -> None:
    try:
        await asyncio.wait_for(step(ctx), STEP_TIMEOUT_S)
    except TimeoutError:
        raise RaceHarnessError(
            f"seed={ctx.seed}: step {label} did not finish in {STEP_TIMEOUT_S}s "
            "(a lock held across steps of the other actor?)"
        ) from None
    except Exception as exc:
        raise RaceHarnessError(f"seed={ctx.seed}: step {label} raised {exc!r}") from exc


async def run_interleavings(
    step_a: Sequence[Step],
    step_b: Sequence[Step],
    n: int,
    seed: int,
    *,
    engine: AsyncEngine,
    invariant: Invariant,
    setup: Hook | None = None,
    cleanup: Hook | None = None,
    raise_on_violation: bool = True,
) -> list[InterleavingResult]:
    """One result per interleaving; raises RaceViolationError (with the seeds) when the invariant
    is false for any of them, unless `raise_on_violation` is False."""
    if n < 1:
        raise RaceHarnessError(f"n must be at least 1, got {n}")
    if not step_a or not step_b:
        raise RaceHarnessError("both step sequences need at least one step")
    results: list[InterleavingResult] = []
    for index in range(n):
        run_seed = seed + index
        schedule = build_schedule(len(step_a), len(step_b), run_seed)
        ctx = RaceContext(engine=engine, seed=run_seed)
        try:
            if setup is not None:
                await setup(ctx)
            next_a, next_b = iter(step_a), iter(step_b)
            for position, actor in enumerate(schedule):
                step = next(next_a if actor == "A" else next_b)
                await _run_step(step, ctx, f"{actor}#{position}")
            ok = await invariant(ctx)
        finally:
            if cleanup is not None:
                await cleanup(ctx)
        results.append(InterleavingResult(index, run_seed, schedule, ok))
    if raise_on_violation and any(not r.ok for r in results):
        raise RaceViolationError(results)
    return results
