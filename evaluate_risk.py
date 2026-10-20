#!/usr/bin/env python3
"""Classify labeled command strings with Kev. None of the commands are executed."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from qsh import Suggestion, positive_timeout
from qsh_backends import DEFAULT_OLLAMA_HOST, BackendError
from qsh_risk import DEFAULT_RISK_MODEL, RISK_LEVELS, KevRiskReviewer, highest_risk


def summarize(results: list[dict]) -> dict:
    valid = [result for result in results if "error" not in result]
    summary = {"cases": len(results), "errors": len(results) - len(valid)}
    for name, key in (("kev", "kev_risk"), ("final", "final_risk")):
        summary[f"{name}_correct"] = sum(result[key] == result["expected_risk"] for result in valid)
        summary[f"{name}_underestimated"] = sum(
            RISK_LEVELS.index(result[key]) < RISK_LEVELS.index(result["expected_risk"])
            for result in valid
        )
        summary[f"{name}_overestimated"] = sum(
            RISK_LEVELS.index(result[key]) > RISK_LEVELS.index(result["expected_risk"])
            for result in valid
        )
    summary["median_seconds"] = round(statistics.median(result["seconds"] for result in results), 3) if results else 0
    return summary


def save_report(path: Path, report: dict) -> None:
    """Replace the previous checkpoint only after the new one is fully written."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.environ.get("AI_RISK_MODEL") or DEFAULT_RISK_MODEL)
    parser.add_argument("--host", default=os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("tests") / "risk_cases.json")
    parser.add_argument("--output", type=Path, default=Path("risk-evaluation.json"))
    parser.add_argument("--timeout", type=positive_timeout, default=180)
    args = parser.parse_args()
    reviewer = KevRiskReviewer(model=args.model, host=args.host)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    for case in cases:
        if case["expected_risk"] not in RISK_LEVELS:
            raise ValueError(f"Invalid expected risk for {case['name']}")
    results = []
    report = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "model": reviewer.model,
        "rules_sha256": hashlib.sha256(reviewer.rules_path.read_bytes()).hexdigest(),
        "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "implementation_sha256": {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("qsh.py", "qsh_risk.py", "qsh_backends.py")
        },
        "expected_cases": len(cases), "complete": False,
        "summary": summarize(results), "results": results,
    }
    save_report(args.output, report)
    for case in cases:
        expected = case["expected_risk"]
        started = time.monotonic()
        result = dict(case)
        try:
            review = reviewer.evaluate(case["command"], "Evaluate this command.", Path("/workspace/project"), args.timeout)
            local = Suggestion.from_json(json.dumps({
                "command": case["command"], "risk": "LOW", "explanation": "Evaluation only.",
            }))
            final_risk = highest_risk(local.risk, review.risk)
            result.update({
                "kev_risk": review.risk, "final_risk": final_risk,
                "review": asdict(review), "local_risk": local.risk,
                "correct": final_risk == expected,
            })
            status = "PASS" if result["correct"] else "MISS"
            print(f"{status}: {case['name']}: expected {expected}, Kev {review.risk}, final {final_risk}", flush=True)
        except (BackendError, OSError, ValueError) as error:
            result.update({"error": str(error), "correct": False})
            print(f"ERROR: {case['name']}: {error}", flush=True)
        result["seconds"] = round(time.monotonic() - started, 3)
        results.append(result)
        report["summary"] = summarize(results)
        report["complete"] = len(results) == len(cases)
        save_report(args.output, report)
        if "error" in result:
            break
    print(json.dumps(report["summary"]), flush=True)
    print(f"Report: {args.output}", flush=True)
    return 0 if report["complete"] and all(result["correct"] for result in results) and results else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"Evaluation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
