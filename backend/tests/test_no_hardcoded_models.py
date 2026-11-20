from __future__ import annotations

import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
HARD_CODED_MODEL = re.compile(
    r"claude-(sonnet|haiku|opus)-[\w.-]+|DEFAULT_MODEL = \"claude|JUDGE_MODEL = "
)


def test_no_hard_coded_claude_model_ids_outside_config() -> None:
    """Model ids come from Settings only (ADR-006); src/config.py holds the defaults."""
    offenders: list[str] = []
    for folder in ("src", "scripts"):
        for path in (BACKEND_DIR / folder).rglob("*.py"):
            if path.name == "config.py" and folder == "src":
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if HARD_CODED_MODEL.search(line):
                    offenders.append(f"{path.relative_to(BACKEND_DIR)}:{number}")
    assert offenders == []
