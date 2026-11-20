from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_no_litellm.sh"

pytestmark = pytest.mark.skipif(
    not SCRIPT.exists() or shutil.which("git") is None or shutil.which("bash") is None,
    reason="needs the repo checkout with git and bash",
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t.invalid", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "scripts" / "check_no_litellm.sh")
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _run(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/check_no_litellm.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def test_real_repo_is_clean() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT)], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_litellm_in_source_fails(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"backend/src/main.py": "# LiteLLM\n"})
    result = _run(repo)
    assert result.returncode == 1
    assert "backend/src/main.py" in result.stderr


def test_allowlisted_paths_pass(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        {
            "TODO.md": "litellm history\n",
            "Story-Reports/5.1.md": "LiteLLM\n",
            "docs/threat_model.md": "LiteLLM\n",
            "terraform/main.tf": "litellm\n",
            "backend/src/main.py": "print('ok')\n",
        },
    )
    assert _run(repo).returncode == 0


@pytest.mark.parametrize("dependency_file", ["backend/poetry.lock", "backend/pyproject.toml"])
def test_litellm_in_dependency_files_fails(tmp_path: Path, dependency_file: str) -> None:
    repo = _make_repo(tmp_path, {dependency_file: 'litellm = "1.0"\n'})
    result = _run(repo)
    assert result.returncode == 1
    assert dependency_file in result.stderr
