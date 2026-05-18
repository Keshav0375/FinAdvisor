from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import anthropic
import httpx
import structlog

log = structlog.get_logger()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_EVAL_SET = PROJECT_ROOT / "data" / "eval" / "golden_qa.json"
DEFAULT_JUDGE_PROMPT = PROJECT_ROOT / "data" / "eval" / "judge_prompt.txt"
DEFAULT_USERS = PROJECT_ROOT / "data" / "seed" / "mock_users.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "eval_results.json"
DEFAULT_API_BASE = "http://localhost:8000"
JUDGE_MODEL = "claude-sonnet-4-20250514"


def load_eval_set(path: Path) -> list[dict[str, Any]]:
    with open(path) as f:
        data = json.load(f)
    return data["eval_set"]


def load_judge_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def load_users(path: Path) -> dict[str, dict[str, Any]]:
    with open(path) as f:
        users = json.load(f)
    return {u["sub"]: u for u in users}


async def run_query(
    client: httpx.AsyncClient,
    base_url: str,
    user_id: str,
    question: str,
) -> dict[str, Any]:
    response_text = ""
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[str] = []

    try:
        async with client.stream(
            "POST",
            f"{base_url}/api/chat/stream",
            json={"message": question},
            headers={"X-User-Id": user_id},
            timeout=60.0,
        ) as resp:
            if resp.status_code != 200:
                return {
                    "response": "",
                    "tool_calls": [],
                    "tool_results": [],
                    "error": f"HTTP {resp.status_code}",
                }

            current_event = ""
            current_data = ""

            async for line in resp.aiter_lines():
                stripped = line.strip()
                if stripped.startswith("event:"):
                    current_event = stripped[6:].strip()
                elif stripped.startswith("data:"):
                    current_data = stripped[5:].strip()
                elif stripped == "" and current_event and current_data:
                    try:
                        parsed = json.loads(current_data)
                    except json.JSONDecodeError:
                        current_event = ""
                        current_data = ""
                        continue

                    if current_event == "message" and parsed.get("type") == "text":
                        response_text += parsed.get("content", "")
                    elif current_event == "tool":
                        tool_calls.append(parsed)
                    elif current_event == "tool_result":
                        tool_results.append(parsed.get("content", ""))

                    current_event = ""
                    current_data = ""

    except httpx.ConnectError:
        return {
            "response": "",
            "tool_calls": [],
            "tool_results": [],
            "error": "Connection refused — is the backend running?",
        }
    except httpx.ReadTimeout:
        return {
            "response": response_text,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "error": "Timeout",
        }

    return {
        "response": response_text,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "error": None,
    }


async def judge_response(
    llm: anthropic.AsyncAnthropic,
    judge_prompt: str,
    question: str,
    response: str,
    tool_results: list[str],
) -> dict[str, Any]:
    chunks_text = "\n---\n".join(tool_results) if tool_results else "(no chunks retrieved)"

    user_message = (
        f"## Question\n{question}\n\n"
        f"## System Response\n{response}\n\n"
        f"## Retrieved Source Chunks\n{chunks_text}"
    )

    try:
        result = await llm.messages.create(
            model=JUDGE_MODEL,
            max_tokens=512,
            system=judge_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        text = "".join(b.text for b in result.content if hasattr(b, "text"))

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])

        return {
            "faithfulness": 0.0,
            "citation_accuracy": 0.0,
            "refusal_correctness": 0.0,
            "reasoning": f"Failed to parse judge output: {text[:200]}",
        }
    except Exception as exc:
        return {
            "faithfulness": 0.0,
            "citation_accuracy": 0.0,
            "refusal_correctness": 0.0,
            "reasoning": f"Judge error: {exc}",
        }


def check_expected_tool_calls(
    actual_tool_calls: list[dict[str, Any]],
    expected: list[str],
) -> dict[str, Any]:
    actual_names = [tc.get("tool", "") for tc in actual_tool_calls]
    missing = [t for t in expected if t not in actual_names]
    return {
        "expected": expected,
        "actual": actual_names,
        "missing": missing,
        "match": len(missing) == 0,
    }


def check_must_not_contain(response: str, forbidden: list[str]) -> dict[str, Any]:
    found = [term for term in forbidden if term.lower() in response.lower()]
    return {
        "forbidden_terms": forbidden,
        "found_in_response": found,
        "clean": len(found) == 0,
    }


async def run_eval(
    eval_set: list[dict[str, Any]],
    judge_prompt: str,
    base_url: str,
    output_path: Path,
    anthropic_key: str,
    compare_baseline: Path | None = None,
) -> dict[str, Any]:
    llm = anthropic.AsyncAnthropic(api_key=anthropic_key)
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        for i, entry in enumerate(eval_set):
            eval_id = entry["id"]
            log.info(
                "eval_start",
                id=eval_id,
                category=entry["category"],
                progress=f"{i + 1}/{len(eval_set)}",
            )

            start = time.perf_counter()
            query_result = await run_query(
                client,
                base_url,
                entry["user_profile"],
                entry["question"],
            )
            query_ms = (time.perf_counter() - start) * 1000

            if query_result["error"]:
                log.warning("eval_query_error", id=eval_id, error=query_result["error"])

            scores = await judge_response(
                llm,
                judge_prompt,
                entry["question"],
                query_result["response"],
                query_result["tool_results"],
            )

            tool_check = check_expected_tool_calls(
                query_result["tool_calls"],
                entry.get("expected_tool_calls", []),
            )

            content_check = check_must_not_contain(
                query_result["response"],
                entry.get("must_not_contain", []),
            )

            result_entry = {
                "id": eval_id,
                "category": entry["category"],
                "user_profile": entry["user_profile"],
                "question": entry["question"],
                "response": query_result["response"],
                "error": query_result["error"],
                "scores": scores,
                "tool_call_check": tool_check,
                "content_check": content_check,
                "query_ms": round(query_ms, 1),
            }
            results.append(result_entry)

            log.info(
                "eval_complete",
                id=eval_id,
                faithfulness=scores.get("faithfulness", 0.0),
                citation_accuracy=scores.get("citation_accuracy", 0.0),
                refusal_correctness=scores.get("refusal_correctness", 0.0),
                query_ms=round(query_ms, 1),
            )

    scored = [r for r in results if r["error"] is None]
    n = len(scored) if scored else 1

    aggregate = {
        "total_entries": len(eval_set),
        "completed": len(scored),
        "errors": len(results) - len(scored),
        "avg_faithfulness": round(sum(r["scores"].get("faithfulness", 0.0) for r in scored) / n, 4),
        "avg_citation_accuracy": round(
            sum(r["scores"].get("citation_accuracy", 0.0) for r in scored) / n, 4
        ),
        "avg_refusal_correctness": round(
            sum(r["scores"].get("refusal_correctness", 0.0) for r in scored) / n, 4
        ),
        "tool_call_match_rate": round(
            sum(1 for r in scored if r["tool_call_check"]["match"]) / n, 4
        ),
        "content_clean_rate": round(sum(1 for r in scored if r["content_check"]["clean"]) / n, 4),
    }

    by_category: dict[str, dict[str, Any]] = {}
    for r in scored:
        cat = r["category"]
        if cat not in by_category:
            by_category[cat] = {
                "count": 0,
                "faithfulness": 0.0,
                "citation_accuracy": 0.0,
                "refusal_correctness": 0.0,
            }
        by_category[cat]["count"] += 1
        by_category[cat]["faithfulness"] += r["scores"].get("faithfulness", 0.0)
        by_category[cat]["citation_accuracy"] += r["scores"].get("citation_accuracy", 0.0)
        by_category[cat]["refusal_correctness"] += r["scores"].get("refusal_correctness", 0.0)

    for _cat, data in by_category.items():
        c = data["count"]
        data["avg_faithfulness"] = round(data["faithfulness"] / c, 4)
        data["avg_citation_accuracy"] = round(data["citation_accuracy"] / c, 4)
        data["avg_refusal_correctness"] = round(data["refusal_correctness"] / c, 4)
        del data["faithfulness"]
        del data["citation_accuracy"]
        del data["refusal_correctness"]

    output = {
        "aggregate": aggregate,
        "by_category": by_category,
        "results": results,
    }

    if compare_baseline and compare_baseline.exists():
        with open(compare_baseline) as f:
            baseline = json.load(f)
        baseline_agg = baseline.get("aggregate", {})
        output["comparison"] = {
            "baseline_citation_accuracy": baseline_agg.get("avg_citation_accuracy", 0.0),
            "current_citation_accuracy": aggregate["avg_citation_accuracy"],
            "delta_citation_accuracy": round(
                aggregate["avg_citation_accuracy"] - baseline_agg.get("avg_citation_accuracy", 0.0),
                4,
            ),
            "baseline_faithfulness": baseline_agg.get("avg_faithfulness", 0.0),
            "current_faithfulness": aggregate["avg_faithfulness"],
            "delta_faithfulness": round(
                aggregate["avg_faithfulness"] - baseline_agg.get("avg_faithfulness", 0.0),
                4,
            ),
        }
        log.info("baseline_comparison", **output["comparison"])

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    log.info("eval_results_written", path=str(output_path))

    return output


def print_summary(output: dict[str, Any]) -> None:
    agg = output["aggregate"]
    print("\n" + "=" * 60)
    print("  FinAdvisor Eval Results")
    print("=" * 60)
    print(f"  Completed:            {agg['completed']}/{agg['total_entries']}")
    print(f"  Errors:               {agg['errors']}")
    print(f"  Avg Faithfulness:     {agg['avg_faithfulness']:.2%}")
    print(f"  Avg Citation Accuracy:{agg['avg_citation_accuracy']:.2%}")
    print(f"  Avg Refusal Correct:  {agg['avg_refusal_correctness']:.2%}")
    print(f"  Tool Call Match Rate: {agg['tool_call_match_rate']:.2%}")
    print(f"  Content Clean Rate:   {agg['content_clean_rate']:.2%}")
    print("-" * 60)

    if "by_category" in output:
        print("\n  By Category:")
        for cat, data in output["by_category"].items():
            print(
                f"    {cat:25s}  n={data['count']:2d}  "
                f"faith={data['avg_faithfulness']:.2%}  "
                f"cite={data['avg_citation_accuracy']:.2%}  "
                f"refuse={data['avg_refusal_correctness']:.2%}"
            )

    if "comparison" in output:
        comp = output["comparison"]
        print("\n  Baseline Comparison:")
        delta_cite = comp["delta_citation_accuracy"]
        delta_faith = comp["delta_faithfulness"]
        cite_arrow = "+" if delta_cite >= 0 else ""
        faith_arrow = "+" if delta_faith >= 0 else ""
        cite_str = f"{comp['current_citation_accuracy']:.2%}"
        faith_str = f"{comp['current_faithfulness']:.2%}"
        print(f"    Citation Accuracy:  {cite_str} ({cite_arrow}{delta_cite:.2%})")
        print(f"    Faithfulness:       {faith_str} ({faith_arrow}{delta_faith:.2%})")

    print("=" * 60 + "\n")

    if agg["avg_citation_accuracy"] < 0.95:
        print("  FAIL: Citation accuracy below 95% threshold")
        sys.exit(1)
    if agg["avg_faithfulness"] < 0.90:
        print("  FAIL: Faithfulness below 90% threshold")
        sys.exit(1)
    print("  PASS: All thresholds met")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FinAdvisor Eval Runner")
    parser.add_argument(
        "--eval-set",
        type=Path,
        default=DEFAULT_EVAL_SET,
        help="Path to golden_qa.json",
    )
    parser.add_argument(
        "--judge-prompt",
        type=Path,
        default=DEFAULT_JUDGE_PROMPT,
        help="Path to judge_prompt.txt",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output results JSON path",
    )
    parser.add_argument(
        "--api-base",
        type=str,
        default=DEFAULT_API_BASE,
        help="Backend API base URL",
    )
    parser.add_argument(
        "--compare-baseline",
        type=Path,
        default=None,
        help="Path to baseline results.json for comparison",
    )
    parser.add_argument(
        "--anthropic-key",
        type=str,
        default=None,
        help="Anthropic API key for judge (falls back to ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of eval entries to run (for quick testing)",
    )
    return parser.parse_args()


def main() -> None:
    import os

    args = parse_args()

    api_key = args.anthropic_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.error("no_anthropic_key", hint="Set ANTHROPIC_API_KEY or pass --anthropic-key")
        sys.exit(1)

    eval_set = load_eval_set(args.eval_set)
    judge_prompt = load_judge_prompt(args.judge_prompt)

    if args.limit:
        eval_set = eval_set[: args.limit]
        log.info("eval_set_limited", count=len(eval_set))

    log.info(
        "eval_runner_start",
        eval_set=str(args.eval_set),
        entries=len(eval_set),
        api_base=args.api_base,
        output=str(args.output),
    )

    output = asyncio.run(
        run_eval(
            eval_set=eval_set,
            judge_prompt=judge_prompt,
            base_url=args.api_base,
            output_path=args.output,
            anthropic_key=api_key,
            compare_baseline=args.compare_baseline,
        )
    )

    print_summary(output)


if __name__ == "__main__":
    main()
