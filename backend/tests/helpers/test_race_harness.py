"""The race harness: seeded, reproducible interleavings; a racy toy is caught, a locked one not."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.helpers import race_harness
from tests.helpers.race_harness import (
    RaceContext,
    RaceHarnessError,
    RaceViolationError,
    build_schedule,
    run_interleavings,
)

# An engine that never connects: the in-memory tests below use no database.
OFFLINE_ENGINE = create_async_engine("postgresql+asyncpg://unused:unused@db.invalid/unused")

ADVISORY_LOCK_KEY = 4242


# In-memory toy: two actors claim a single slot. Racy = check, then act in a separate step.


async def _check(ctx: RaceContext, who: str) -> None:
    ctx.state[f"{who}_saw_free"] = not ctx.state["claims"]


async def _act(ctx: RaceContext, who: str) -> None:
    if ctx.state[f"{who}_saw_free"]:
        ctx.state["claims"].append(who)


async def _claim_atomically(ctx: RaceContext, who: str) -> None:
    if not ctx.state["claims"]:
        ctx.state["claims"].append(who)


async def _memory_setup(ctx: RaceContext) -> None:
    ctx.state["claims"] = []


async def _at_most_one_claim(ctx: RaceContext) -> bool:
    return len(ctx.state["claims"]) <= 1


async def check_a(ctx: RaceContext) -> None:
    await _check(ctx, "a")


async def act_a(ctx: RaceContext) -> None:
    await _act(ctx, "a")


async def check_b(ctx: RaceContext) -> None:
    await _check(ctx, "b")


async def act_b(ctx: RaceContext) -> None:
    await _act(ctx, "b")


async def claim_a(ctx: RaceContext) -> None:
    await _claim_atomically(ctx, "a")


async def claim_b(ctx: RaceContext) -> None:
    await _claim_atomically(ctx, "b")


async def _run_memory_racy(n: int, seed: int, *, raise_on_violation: bool = False):  # type: ignore[no-untyped-def]
    return await run_interleavings(
        [check_a, act_a],
        [check_b, act_b],
        n,
        seed,
        engine=OFFLINE_ENGINE,
        invariant=_at_most_one_claim,
        setup=_memory_setup,
        raise_on_violation=raise_on_violation,
    )


def test_schedule_is_fixed_by_the_seed_and_keeps_each_actors_steps() -> None:
    assert build_schedule(2, 3, seed=7) == build_schedule(2, 3, seed=7)
    schedules = {build_schedule(2, 2, seed=s) for s in range(100)}
    assert schedules == {"AABB", "ABAB", "ABBA", "BAAB", "BABA", "BBAA"}
    assert all(
        sorted(s) == ["A", "A", "B", "B", "B"] for s in {build_schedule(2, 3, s) for s in range(20)}
    )


async def test_in_memory_race_is_detected_and_reproduced() -> None:
    results = await _run_memory_racy(n=200, seed=1)
    assert len(results) == 200
    violations = [r for r in results if not r.ok]
    assert violations, "the racy toy never violated its invariant in 200 interleavings"
    assert all(r.seed == 1 + r.index for r in results)

    again = await _run_memory_racy(n=200, seed=1)
    assert again == results

    first = violations[0]
    [replayed] = await _run_memory_racy(n=1, seed=first.seed)
    assert (replayed.ok, replayed.schedule, replayed.seed) == (False, first.schedule, first.seed)


async def test_atomic_pair_never_violates() -> None:
    results = await run_interleavings(
        [claim_a],
        [claim_b],
        n=50,
        seed=1,
        engine=OFFLINE_ENGINE,
        invariant=_at_most_one_claim,
        setup=_memory_setup,
    )
    assert all(r.ok for r in results)


async def test_violation_raises_with_the_seeds() -> None:
    with pytest.raises(RaceViolationError, match=r"seed=\d+ schedule=[AB]{4}") as raised:
        await _run_memory_racy(n=200, seed=1, raise_on_violation=True)
    assert "of 200 interleavings" in str(raised.value)
    assert len(raised.value.results) == 200


async def test_violation_message_is_capped_at_ten_seeds() -> None:
    results = [race_harness.InterleavingResult(i, i, "AB", ok=False) for i in range(12)]
    assert "(+2 more)" in str(RaceViolationError(results))


async def test_cleanup_runs_after_every_interleaving_even_when_a_step_fails() -> None:
    cleaned: list[int] = []

    async def cleanup(ctx: RaceContext) -> None:
        cleaned.append(ctx.seed)

    async def boom(ctx: RaceContext) -> None:
        raise ValueError("step failed")

    with pytest.raises(RaceHarnessError, match=r"seed=5: step [AB]#\d raised ValueError"):
        await run_interleavings(
            [boom],
            [claim_b],
            n=3,
            seed=5,
            engine=OFFLINE_ENGINE,
            invariant=_at_most_one_claim,
            setup=_memory_setup,
            cleanup=cleanup,
        )
    assert cleaned == [5]


async def test_a_step_that_never_finishes_fails_with_its_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(race_harness, "STEP_TIMEOUT_S", 0.05)

    async def hang(ctx: RaceContext) -> None:
        await asyncio.sleep(10)

    with pytest.raises(RaceHarnessError, match=r"seed=9: step [AB]#\d did not finish"):
        await run_interleavings(
            [hang], [claim_b], n=1, seed=9, engine=OFFLINE_ENGINE, invariant=_at_most_one_claim
        )


@pytest.mark.parametrize(
    ("n", "steps_a", "steps_b"), [(0, [claim_a], [claim_b]), (1, [], [claim_b]), (1, [claim_a], [])]
)
async def test_bad_arguments_are_refused(n: int, steps_a: list, steps_b: list) -> None:  # type: ignore[type-arg]
    with pytest.raises(RaceHarnessError):
        await run_interleavings(
            steps_a, steps_b, n, 1, engine=OFFLINE_ENGINE, invariant=_at_most_one_claim
        )


# Against the integration Postgres: check-then-act on a real table, one transaction per step.


@pytest.fixture
async def claims_table(async_engine: AsyncEngine) -> AsyncIterator[str]:
    name = f"race_claims_{uuid.uuid4().hex[:12]}"
    async with async_engine.begin() as conn:
        await conn.execute(text(f"CREATE TABLE {name} (owner text NOT NULL)"))
    yield name
    async with async_engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {name}"))


def _database_toy(table: str):  # type: ignore[no-untyped-def]
    """Steps, invariant and cleanup of the toy on `table` (a name this test created)."""

    async def check(ctx: RaceContext, who: str) -> None:
        async with ctx.engine.begin() as conn:
            count = (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
        ctx.state[f"{who}_saw_free"] = count == 0

    async def act(ctx: RaceContext, who: str) -> None:
        if ctx.state[f"{who}_saw_free"]:
            async with ctx.engine.begin() as conn:
                await conn.execute(text(f"INSERT INTO {table} (owner) VALUES (:o)"), {"o": who})

    async def claim_locked(ctx: RaceContext, who: str) -> None:
        async with ctx.engine.begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY})
            count = (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            if count == 0:
                await conn.execute(text(f"INSERT INTO {table} (owner) VALUES (:o)"), {"o": who})

    async def one_row(ctx: RaceContext) -> bool:
        async with ctx.engine.begin() as conn:
            return (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() <= 1

    async def empty(ctx: RaceContext) -> None:
        async with ctx.engine.begin() as conn:
            await conn.execute(text(f"DELETE FROM {table}"))

    racy_a = [lambda c: check(c, "a"), lambda c: act(c, "a")]
    racy_b = [lambda c: check(c, "b"), lambda c: act(c, "b")]
    return (
        racy_a,
        racy_b,
        [lambda c: claim_locked(c, "a")],
        [lambda c: claim_locked(c, "b")],
        one_row,
        empty,
    )


@pytest.mark.integration
async def test_detects_and_reproduces_race(async_engine: AsyncEngine, claims_table: str) -> None:
    racy_a, racy_b, locked_a, locked_b, invariant, cleanup = _database_toy(claims_table)

    def run(a, b, n, seed):  # type: ignore[no-untyped-def]
        return run_interleavings(
            a,
            b,
            n,
            seed,
            engine=async_engine,
            invariant=invariant,
            cleanup=cleanup,
            raise_on_violation=False,
        )

    results = await run(racy_a, racy_b, 200, 1)
    violations = [r for r in results if not r.ok]
    assert violations, "the racy pair never violated the invariant in 200 interleavings"

    assert await run(racy_a, racy_b, 200, 1) == results

    first = violations[0]
    [replayed] = await run(racy_a, racy_b, 1, first.seed)
    assert (replayed.ok, replayed.schedule) == (False, first.schedule)

    locked = await run(locked_a, locked_b, 200, 1)
    assert [r for r in locked if not r.ok] == []

    with pytest.raises(RaceViolationError, match=f"seed={first.seed}"):
        await run_interleavings(
            racy_a, racy_b, 1, first.seed, engine=async_engine, invariant=invariant, cleanup=cleanup
        )
