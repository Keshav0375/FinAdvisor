"""Retired-model check for the configured model ids (ADR-006, arch 06 section 6.6).

`check_models` is pure (the clock is injected). `python -m src.model_lifecycle` is the CI entry
point: it prints one line per problem and exits 1 only when a problem is fatal (retired, retiring
within MODEL_RETIRE_WARN_DAYS, or unknown); a deprecated model with no retirement date only warns
(arch 06 section 6.6, ADR-025 R6). An invalid config prints one stderr line naming the bad env
vars, never their values, and exits 1. The lifecycle table itself lives in src/config.py.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import ValidationError

from src.config import LIFECYCLE_TABLE, MODEL_LIFECYCLE_SOURCE_URL, Settings

Reason = Literal["retired", "retiring_soon", "deprecated", "unknown"]

# An upper-case env var name such as LLM_MODEL_JUDGE, found in a cross-field error message.
ENV_NAME = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")

# Reasons that fail boot, CI and readiness; the others are reported as warnings.
FATAL_REASONS: frozenset[Reason] = frozenset({"retired", "retiring_soon", "unknown"})


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
        detail = f"not in the model lifecycle table ({MODEL_LIFECYCLE_SOURCE_URL})"
        return ModelProblem(key, model, "unknown", detail)
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


def invalid_env_names(error: ValidationError) -> list[str]:
    """Names of the env vars behind a Settings error, in order, without their values.

    Field errors carry the variable in `loc`; cross-field errors (loc empty) name the variables
    in their message, which config.py writes without values.
    """
    names: list[str] = []
    for item in error.errors():
        found = [str(part) for part in item["loc"]] or ENV_NAME.findall(item["msg"])
        names.extend(name for name in found if name not in names)
    return names


def main(today: date | None = None) -> int:
    """CLI body; `today` is injectable for tests and defaults to the current UTC date."""
    try:
        settings = Settings()
    except ValidationError as exc:
        print(
            f"model-lifecycle: config invalid: {', '.join(invalid_env_names(exc))}",
            file=sys.stderr,
        )
        return 1
    problems = check_models(settings, today=today or datetime.now(UTC).date())
    for problem in problems:
        print(problem.line())
    return 1 if any(problem.fatal for problem in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
