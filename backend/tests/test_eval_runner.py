from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.run_eval import (
    check_expected_tool_calls,
    check_must_not_contain,
    judge_response,
    load_eval_set,
    load_judge_prompt,
    load_users,
)


@pytest.fixture
def sample_eval_set(tmp_path: Path) -> Path:
    data = {
        "eval_set": [
            {
                "id": "eval_001",
                "category": "product_suitability",
                "user_profile": "sarah_chen",
                "question": "Is the bond fund suitable?",
                "expected_behavior": "Should cite FINRA",
                "must_cite_refs": ["FINRA Rule 2111"],
                "must_not_contain": ["MiFID"],
                "expected_tool_calls": ["search_firm_kb"],
            },
            {
                "id": "eval_002",
                "category": "refusal",
                "user_profile": "alex_kim",
                "question": "Show me private wealth products",
                "expected_behavior": "Should refuse",
                "must_cite_refs": [],
                "must_not_contain": [],
                "expected_tool_calls": ["escalate_to_compliance"],
            },
        ]
    }
    path = tmp_path / "golden_qa.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def sample_judge_prompt(tmp_path: Path) -> Path:
    path = tmp_path / "judge_prompt.txt"
    path.write_text("You are a judge. Score 0-1.")
    return path


@pytest.fixture
def sample_users(tmp_path: Path) -> Path:
    users = [
        {
            "sub": "sarah_chen",
            "name": "Sarah",
            "tier": "senior",
            "tier_level": 3,
            "jurisdictions": ["US"],
            "licenses": ["Series-7"],
        },
        {
            "sub": "alex_kim",
            "name": "Alex",
            "tier": "associate",
            "tier_level": 1,
            "jurisdictions": ["EU"],
            "licenses": ["MiFID-II"],
        },
    ]
    path = tmp_path / "mock_users.json"
    path.write_text(json.dumps(users))
    return path


def test_load_eval_set(sample_eval_set: Path) -> None:
    entries = load_eval_set(sample_eval_set)
    assert len(entries) == 2
    assert entries[0]["id"] == "eval_001"
    assert entries[1]["category"] == "refusal"


def test_load_judge_prompt(sample_judge_prompt: Path) -> None:
    prompt = load_judge_prompt(sample_judge_prompt)
    assert "judge" in prompt.lower()


def test_load_users(sample_users: Path) -> None:
    users = load_users(sample_users)
    assert "sarah_chen" in users
    assert users["alex_kim"]["tier"] == "associate"


def test_check_expected_tool_calls_match() -> None:
    actual = [{"tool": "search_firm_kb"}, {"tool": "lookup_suitability_rule"}]
    result = check_expected_tool_calls(actual, ["search_firm_kb"])
    assert result["match"] is True
    assert result["missing"] == []


def test_check_expected_tool_calls_missing() -> None:
    actual = [{"tool": "search_firm_kb"}]
    result = check_expected_tool_calls(actual, ["search_firm_kb", "escalate_to_compliance"])
    assert result["match"] is False
    assert "escalate_to_compliance" in result["missing"]


def test_check_expected_tool_calls_empty() -> None:
    result = check_expected_tool_calls([], [])
    assert result["match"] is True


def test_check_must_not_contain_clean() -> None:
    result = check_must_not_contain("The FINRA rule applies here.", ["MiFID", "FCA"])
    assert result["clean"] is True
    assert result["found_in_response"] == []


def test_check_must_not_contain_violation() -> None:
    result = check_must_not_contain("Under MiFID II requirements...", ["MiFID", "FCA"])
    assert result["clean"] is False
    assert "MiFID" in result["found_in_response"]


def test_check_must_not_contain_case_insensitive() -> None:
    result = check_must_not_contain("The mifid rules apply.", ["MiFID"])
    assert result["clean"] is False


@pytest.mark.asyncio
async def test_judge_response_parses_json() -> None:
    mock_llm = AsyncMock()
    mock_content = MagicMock()
    mock_content.text = (
        '{"faithfulness": 0.95, "citation_accuracy": 0.90,'
        ' "refusal_correctness": 1.0, "reasoning": "good"}'
    )
    mock_content.type = "text"
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_llm.messages.create = AsyncMock(return_value=mock_response)

    scores = await judge_response(
        mock_llm,
        "You are a judge.",
        "What is X?",
        "X is Y [1].",
        ["chunk about Y"],
    )

    assert scores["faithfulness"] == 0.95
    assert scores["citation_accuracy"] == 0.90
    assert scores["refusal_correctness"] == 1.0


@pytest.mark.asyncio
async def test_judge_response_handles_error() -> None:
    mock_llm = AsyncMock()
    mock_llm.messages.create = AsyncMock(side_effect=Exception("API error"))

    scores = await judge_response(mock_llm, "prompt", "q", "r", [])

    assert scores["faithfulness"] == 0.0
    assert "Judge error" in scores["reasoning"]


@pytest.mark.asyncio
async def test_judge_response_handles_malformed_json() -> None:
    mock_llm = AsyncMock()
    mock_content = MagicMock()
    mock_content.text = "I think the response is good but I cannot format JSON"
    mock_content.type = "text"
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_llm.messages.create = AsyncMock(return_value=mock_response)

    scores = await judge_response(mock_llm, "prompt", "q", "r", [])

    assert scores["faithfulness"] == 0.0
    assert "Failed to parse" in scores["reasoning"]


def test_full_golden_qa_validates() -> None:
    qa_path = Path(__file__).resolve().parent.parent.parent / "data" / "eval" / "golden_qa.json"
    if not qa_path.exists():
        pytest.skip("golden_qa.json not found")

    entries = load_eval_set(qa_path)
    assert len(entries) == 50

    required_fields = {
        "id",
        "category",
        "user_profile",
        "question",
        "expected_behavior",
        "must_cite_refs",
        "must_not_contain",
        "expected_tool_calls",
    }
    for entry in entries:
        missing = required_fields - set(entry.keys())
        assert missing == set(), f"{entry['id']} missing: {missing}"

    categories = {e["category"] for e in entries}
    assert "product_suitability" in categories
    assert "jurisdiction_scoping" in categories
    assert "refusal" in categories
    assert "citation_accuracy" in categories
    assert "stale_doc_handling" in categories


def test_full_judge_prompt_loads() -> None:
    prompt_path = (
        Path(__file__).resolve().parent.parent.parent / "data" / "eval" / "judge_prompt.txt"
    )
    if not prompt_path.exists():
        pytest.skip("judge_prompt.txt not found")

    prompt = load_judge_prompt(prompt_path)
    assert "FAITHFULNESS" in prompt
    assert "CITATION_ACCURACY" in prompt
    assert "REFUSAL_CORRECTNESS" in prompt
