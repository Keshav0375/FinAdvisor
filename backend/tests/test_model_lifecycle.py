from __future__ import annotations

import os
import runpy
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from src.config import LIFECYCLE_TABLE, Settings
from src.model_lifecycle import ModelProblem, check_models, main

BACKEND_DIR = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 1)

RETIRED_ID = "claude-sonnet-4-20250514"
RETIRING_ID = "claude-sonnet-4-5"
DEPRECATED_ID = "claude-haiku-4-5"


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _by_key(problems: list[ModelProblem]) -> dict[str, ModelProblem]:
    return {problem.key: problem for problem in problems}


def _run_cli(**env: str) -> subprocess.CompletedProcess[str]:
    # os.environ already carries the dummy required keys set by the autouse fixture.
    return subprocess.run(
        [sys.executable, "-m", "src.model_lifecycle"],
        cwd=BACKEND_DIR,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestCheckModels:
    def test_retired_answer_model_is_a_fatal_problem(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(monkeypatch, LLM_MODEL_ANSWER=RETIRED_ID)
        problems = check_models(settings, today=TODAY)
        problem = _by_key(problems)["LLM_MODEL_ANSWER"]
        assert problem.reason == "retired"
        assert problem.model == RETIRED_ID
        assert problem.fatal

    def test_retired_on_the_retirement_day(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(monkeypatch, LLM_MODEL_AUX=RETIRING_ID)
        problems = check_models(settings, today=date(2026, 11, 30))
        assert _by_key(problems)["LLM_MODEL_AUX"].reason == "retired"

    def test_retiring_soon_is_fatal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(monkeypatch, LLM_MODEL_AUX=RETIRING_ID)
        problems = check_models(settings, today=TODAY)
        problem = _by_key(problems)["LLM_MODEL_AUX"]
        assert problem.reason == "retiring_soon"
        assert problem.fatal

    def test_retiring_model_outside_the_window_is_clean(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = _settings(monkeypatch, LLM_MODEL_AUX=RETIRING_ID)
        assert check_models(settings, today=date(2026, 9, 1)) == []

    def test_warn_window_is_the_settings_knob(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 2026-10-01 is 60 days before 2026-11-30: outside 30, inside 90.
        narrow = _settings(monkeypatch, LLM_MODEL_AUX=RETIRING_ID)
        assert check_models(narrow, today=date(2026, 10, 1)) == []
        wide = _settings(monkeypatch, LLM_MODEL_AUX=RETIRING_ID, MODEL_RETIRE_WARN_DAYS="90")
        problems = check_models(wide, today=date(2026, 10, 1))
        assert _by_key(problems)["LLM_MODEL_AUX"].reason == "retiring_soon"

    def test_warn_window_boundary_is_inclusive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(monkeypatch, LLM_MODEL_AUX=RETIRING_ID)
        assert check_models(settings, today=date(2026, 10, 31))  # 30 days left
        assert check_models(settings, today=date(2026, 10, 30)) == []  # 31 days left

    def test_default_models_are_clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(monkeypatch)
        assert check_models(settings, today=TODAY) == []

    def test_disabled_fallback_is_still_checked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(
            monkeypatch, LLM_MODEL_FALLBACK=RETIRED_ID, LLM_FALLBACK_ENABLED="false"
        )
        problems = check_models(settings, today=TODAY)
        assert _by_key(problems)["LLM_MODEL_FALLBACK"].reason == "retired"

    def test_judge_model_is_checked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(monkeypatch, LLM_MODEL_JUDGE=RETIRED_ID)
        assert _by_key(check_models(settings, today=TODAY))["LLM_MODEL_JUDGE"].fatal

    def test_unknown_model_id_is_fatal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(monkeypatch, LLM_MODEL_AUX="claude-haiku-9-9")
        problem = _by_key(check_models(settings, today=TODAY))["LLM_MODEL_AUX"]
        assert problem.reason == "unknown"
        assert problem.fatal

    def test_deprecated_without_retirement_date_warns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = _settings(monkeypatch, LLM_MODEL_AUX=DEPRECATED_ID)
        problem = _by_key(check_models(settings, today=TODAY))["LLM_MODEL_AUX"]
        assert problem.reason == "deprecated"
        assert not problem.fatal

    def test_each_bad_key_is_reported_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(
            monkeypatch, LLM_MODEL_ANSWER=RETIRED_ID, LLM_MODEL_FALLBACK=RETIRED_ID
        )
        keys = [problem.key for problem in check_models(settings, today=TODAY)]
        assert sorted(keys) == ["LLM_MODEL_ANSWER", "LLM_MODEL_FALLBACK"]

    def test_negative_warn_days_rejected_at_boot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValueError, match="MODEL_RETIRE_WARN_DAYS"):
            _settings(monkeypatch, MODEL_RETIRE_WARN_DAYS="-1")


class TestCli:
    def test_retired_model_exits_1_and_names_the_key(self) -> None:
        # Real process and real clock: claude-sonnet-4-20250514 retired 2026-06-15, so the
        # result cannot change with the date.
        result = _run_cli(LLM_MODEL_ANSWER=RETIRED_ID)
        assert result.returncode == 1
        assert "LLM_MODEL_ANSWER" in result.stdout

    def test_default_models_exit_0(self) -> None:
        # Default ids carry no dates in the table, so the real clock cannot change the result.
        result = _run_cli()
        assert result.returncode == 0, result.stdout + result.stderr

    def test_retiring_soon_exits_1(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("LLM_MODEL_AUX", RETIRING_ID)
        assert main(today=TODAY) == 1
        assert "LLM_MODEL_AUX" in capsys.readouterr().out

    def test_retiring_model_outside_window_exits_0(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("LLM_MODEL_AUX", RETIRING_ID)
        assert main(today=date(2026, 9, 1)) == 0
        assert capsys.readouterr().out == ""

    def test_deprecated_without_date_exits_0_but_prints_the_key(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("LLM_MODEL_AUX", DEPRECATED_ID)
        assert main(today=TODAY) == 0
        assert "LLM_MODEL_AUX" in capsys.readouterr().out


class TestCliConfigErrors:
    def test_missing_env_prints_one_line_with_names_and_exits_1(self) -> None:
        # Real process: a missing required key must not end in a traceback.
        env = {key: value for key, value in os.environ.items() if key != "ANTHROPIC_API_KEY"}
        result = subprocess.run(
            [sys.executable, "-m", "src.model_lifecycle"],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == "model-lifecycle: config invalid: ANTHROPIC_API_KEY\n"

    def test_invalid_values_are_named_never_printed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("JWT_SECRET", "short-secret-value")
        monkeypatch.setenv("MODEL_RETIRE_WARN_DAYS", "-7")
        assert main(today=TODAY) == 1
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err.count("\n") == 1
        assert out.err.startswith("model-lifecycle: config invalid: ")
        assert "JWT_SECRET" in out.err
        assert "MODEL_RETIRE_WARN_DAYS" in out.err
        assert "short-secret-value" not in out.err
        assert "-7" not in out.err

    def test_cross_field_error_names_the_variables(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("LLM_MODEL_JUDGE", "claude-sonnet-5-5")
        assert main(today=TODAY) == 1
        err = capsys.readouterr().err
        assert err == "model-lifecycle: config invalid: LLM_MODEL_JUDGE, LLM_MODEL_ANSWER\n"
        assert "claude-sonnet-5-5" not in err


class TestModuleEntryPoint:
    def test_running_as_main_exits_with_the_check_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # In-process run of the `__main__` guard, so coverage sees the entry line.
        monkeypatch.setenv("LLM_MODEL_ANSWER", RETIRED_ID)
        monkeypatch.delitem(sys.modules, "src.model_lifecycle", raising=False)
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("src.model_lifecycle", run_name="__main__")
        assert exc.value.code == 1


class TestLifecycleTableInConfig:
    def test_table_lives_in_config_and_covers_the_default_models(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = _settings(monkeypatch)
        for model in (
            settings.llm_model_answer,
            settings.llm_model_aux,
            settings.llm_model_judge,
            settings.llm_model_fallback,
        ):
            assert LIFECYCLE_TABLE[model].model == model
