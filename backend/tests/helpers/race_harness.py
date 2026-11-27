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

A cleanup that raises never hides the failure of the interleaving: it is added to that failure as a
note (and raised, naming the seed, only when nothing else failed).

Knob: RACE_STEP_TIMEOUT_S (default 30), seconds one step may take; read when a run starts, and a bad
value fails with a message naming the variable.
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers.env import positive_number

STEP_TIMEOUT_ENV = "RACE_STEP_TIMEOUT_S"
DEFAULT_STEP_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class RaceConfig:
    step_timeout_s: float

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> RaceConfig:
        return cls(positive_number(environ, STEP_TIMEOUT_ENV, DEFAULT_STEP_TIMEOUT_S, float))


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


async def _run_step(step: Step, ctx: RaceContext, label: str, timeout_s: float) -> None:
    try:
        await asyncio.wait_for(step(ctx), timeout_s)
    except TimeoutError:
        raise RaceHarnessError(
            f"seed={ctx.seed}: step {label} did not finish in {timeout_s}s "
            "(a lock held across steps of the other actor?)"
        ) from None
    except Exception as exc:
        raise RaceHarnessError(f"seed={ctx.seed}: step {label} raised {exc!r}") from exc


async def _run_cleanup(cleanup: Hook, ctx: RaceContext, failure: BaseException | None) -> None:
    """Run `cleanup`. When the interleaving already failed, a cleanup error is attached to that
    failure as a note and never replaces it; otherwise it is raised, naming the seed."""
    try:
        await cleanup(ctx)
    except Exception as exc:
        if failure is None:
            raise RaceHarnessError(f"seed={ctx.seed}: cleanup raised {exc!r}") from exc
        failure.add_note(f"seed={ctx.seed}: cleanup also raised {exc!r}")


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
    timeout_s = RaceConfig.from_env(os.environ).step_timeout_s
    results: list[InterleavingResult] = []
    for index in range(n):
        run_seed = seed + index
        schedule = build_schedule(len(step_a), len(step_b), run_seed)
        ctx = RaceContext(engine=engine, seed=run_seed)
        failure: BaseException | None = None
        try:
            if setup is not None:
                await setup(ctx)
            next_a, next_b = iter(step_a), iter(step_b)
            for position, actor in enumerate(schedule):
                step = next(next_a if actor == "A" else next_b)
                await _run_step(step, ctx, f"{actor}#{position}", timeout_s)
            ok = await invariant(ctx)
        except BaseException as exc:
            failure = exc
            raise
        finally:
            if cleanup is not None:
                await _run_cleanup(cleanup, ctx, failure)
        results.append(InterleavingResult(index, run_seed, schedule, ok))
    if raise_on_violation and any(not r.ok for r in results):
        raise RaceViolationError(results)
    return results
