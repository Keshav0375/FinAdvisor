"""Retired-model check for the configured model ids (ADR-006, arch 06 section 6.6).

`check_models` is pure (the clock is injected). `python -m src.model_lifecycle` is the CI entry
point: it prints one line per problem and exits 1 only when a problem is fatal.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from src.config import Settings

SOURCE_URL = "https://platform.claude.com/docs/en/about-claude/model-deprecations"

Reason = Literal["retired", "retiring_soon", "deprecated", "unknown"]

# Reasons that fail boot, CI and readiness; the others are reported as warnings.
FATAL_REASONS: frozenset[Reason] = frozenset({"retired", "unknown"})


@dataclass(frozen=True)
class ModelLifecycle:
    model: str
    deprecated_since: date | None = None
    retires: date | None = None
    source: str = SOURCE_URL


# Allowlist of known model ids. An id absent from this table is an `unknown` problem (arch 06
# section 6.6: unknown ids fail). Dates come from ADR-006 (decision-logs.md, accepted 2026-11-01),
# not re-verified live; the table is a standing maintenance cost (ADR-006, "Harder later").
# The ADR gives no retirement date for claude-haiku-4-5, so it only warns.
LIFECYCLE_TABLE: dict[str, ModelLifecycle] = {
    row.model: row
    for row in (
        ModelLifecycle("claude-sonnet-5-5"),
        ModelLifecycle("claude-haiku-5-5"),
        ModelLifecycle("claude-sonnet-4-6"),
        ModelLifecycle("claude-sonnet-4-5", retires=date(2026, 11, 30)),
        ModelLifecycle("claude-haiku-4-5", deprecated_since=date(2026, 10, 15)),
        ModelLifecycle("claude-sonnet-4-20250514", retires=date(2026, 6, 15)),
    )
}


@dataclass(frozen=True)
class ModelProblem:
    key: str
    model: str
    reason: Reason
    detail: str

    @property
    def fatal(self) -> bool:
        return self.reason in FATAL_REASONS

    def line(self) -> str:
        level = "ERROR" if self.fatal else "WARNING"
        return f"{level} {self.key}={self.model} {self.reason}: {self.detail}"


def _check_one(key: str, model: str, warn_days: int, today: date) -> ModelProblem | None:
    row = LIFECYCLE_TABLE.get(model)
    if row is None:
        return ModelProblem(
            key, model, "unknown", f"not in the model lifecycle table ({SOURCE_URL})"
        )
    if row.retires is not None:
        if row.retires <= today:
            return ModelProblem(key, model, "retired", f"retired {row.retires} ({row.source})")
        if (row.retires - today).days <= warn_days:
            return ModelProblem(
                key, model, "retiring_soon", f"retires {row.retires} ({row.source})"
            )
    if row.deprecated_since is not None and row.deprecated_since <= today:
        return ModelProblem(
            key, model, "deprecated", f"deprecated since {row.deprecated_since} ({row.source})"
        )
    return None


def check_models(settings: Settings, *, today: date) -> list[ModelProblem]:
    """Problems for every configured model id; the fallback counts even while it is disabled."""
    configured = (
        ("LLM_MODEL_ANSWER", settings.llm_model_answer),
        ("LLM_MODEL_AUX", settings.llm_model_aux),
        ("LLM_MODEL_JUDGE", settings.llm_model_judge),
        ("LLM_MODEL_FALLBACK", settings.llm_model_fallback),
    )
    problems = (
        _check_one(key, model, settings.model_retire_warn_days, today) for key, model in configured
    )
    return [problem for problem in problems if problem is not None]


def main() -> int:
    problems = check_models(Settings(), today=datetime.now(UTC).date())
    for problem in problems:
        print(problem.line())
    return 1 if any(problem.fatal for problem in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
