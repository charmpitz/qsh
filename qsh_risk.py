"""Evaluate shell command risk with Kev's decision API; never execute commands."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from qsh_backends import DEFAULT_OLLAMA_HOST, BackendError, ollama_request


LINUX_RISK_MODEL = "kev:0.8b-q8_0"
MACOS_RISK_MODEL = "kev:0.8b-mxfp8"
# Preserve the Linux value for callers that do not select a target platform.
DEFAULT_RISK_MODEL = LINUX_RISK_MODEL
RULES_PATH = Path(__file__).resolve().with_name("qsh-risk-rules.json")
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def default_risk_model(target_platform: str) -> str:
    """Return the bundled Kev model for a supported target."""
    return {
        "linux": LINUX_RISK_MODEL,
        "macos": MACOS_RISK_MODEL,
    }[target_platform]


def is_forced_recursive_removal(command: str) -> bool:
    """Conservatively match combined, reordered, and separated rm flags."""
    for match in re.finditer(r"\brm\s+((?:--?[A-Za-z][A-Za-z-]*\s+)+)", command):
        options = match.group(1).split()
        short_flags = "".join(option[1:] for option in options if not option.startswith("--"))
        recursive = "r" in short_flags or "R" in short_flags or "--recursive" in options
        forced = "f" in short_flags or "--force" in options
        if recursive and forced:
            return True
    return False


def highest_risk(*risks: str) -> str:
    return max(risks, key=RISK_LEVELS.index)


@dataclass(frozen=True)
class Finding:
    rule: str
    risk: str
    description: str
    probability: float


@dataclass(frozen=True)
class RiskReview:
    model: str
    risk: str
    findings: tuple[Finding, ...]
    probabilities: dict[str, float]

    @property
    def summary(self) -> str:
        return "; ".join(finding.description for finding in self.findings)


@dataclass
class KevRiskReviewer:
    model: str = DEFAULT_RISK_MODEL
    host: str = DEFAULT_OLLAMA_HOST
    rules_path: Path = RULES_PATH
    policy: dict = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.policy = json.loads(self.rules_path.read_text(encoding="utf-8"))
        if not isinstance(self.policy, dict):
            raise ValueError("Risk rules must be a JSON object.")
        threshold = self.policy.get("threshold")
        if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 < threshold <= 1:
            raise ValueError("Risk rule threshold must be greater than 0 and at most 1.")
        if not isinstance(self.policy.get("instructions"), str) or not self.policy["instructions"].strip():
            raise ValueError("Risk rules must include review instructions.")
        rules = self.policy.get("rules")
        if not isinstance(rules, list) or not rules:
            raise ValueError("Risk rules must include at least one rule.")
        seen = set()
        for rule in rules:
            fields = ("id", "risk", "description", "when_true", "when_false")
            if not isinstance(rule, dict) or any(
                not isinstance(rule.get(key), str) or not rule[key].strip() for key in fields
            ):
                raise ValueError("Every risk rule needs an id, risk, description, and both criteria.")
            if rule["risk"] not in RISK_LEVELS or rule["id"] in seen:
                raise ValueError("Risk rules need valid risk levels and unique ids.")
            seen.add(rule["id"])

    def evaluate(
        self, command: str, request: str, cwd: Path, timeout: float, target_platform: str = "linux",
    ) -> RiskReview:
        platform_name = {"linux": "Linux", "macos": "macOS"}[target_platform]
        questions = {
            rule["id"]: {
                "type": "noul",
                "instructions": (
                    f"Does executing the supplied {platform_name} POSIX sh command match this rule: "
                    f"{rule['description']}? "
                    f"{self.policy['instructions']}"
                ),
                "criteria": {"true": rule["when_true"], "false": rule["when_false"]},
            }
            for rule in self.policy["rules"]
        }
        payload = {
            "model": self.model,
            "state": {
                "shell": f"{platform_name} POSIX sh", "cwd": str(cwd), "command": command,
                "user_request": request,
            },
            "questions": questions,
            "keep_alive": 0,
        }
        try:
            data = ollama_request(self.host, "/v1/systemone", payload, timeout)
            answers = data.get("answers")
            if not isinstance(answers, dict) or answers.keys() != questions.keys():
                raise ValueError("Kev must return an answer for every risk rule.")
            probabilities = {}
            findings = []
            for rule in self.policy["rules"]:
                answer = answers[rule["id"]]
                if not isinstance(answer, dict) or answer.get("type") != "noul":
                    raise ValueError(f"Kev returned an invalid answer for {rule['id']}.")
                probability = answer.get("noul")
                if type(probability) not in (int, float) or not math.isfinite(probability) or not 0 <= probability <= 1:
                    raise ValueError(f"Kev returned an invalid probability for {rule['id']}.")
                probabilities[rule["id"]] = probability
                if probability >= self.policy["threshold"]:
                    findings.append(Finding(rule["id"], rule["risk"], rule["description"], probability))
        except (BackendError, OSError, ValueError) as error:
            raise BackendError(f"Kev risk review failed: {error}") from error
        return RiskReview(
            model=self.model,
            risk=highest_risk("LOW", *(finding.risk for finding in findings)),
            findings=tuple(findings),
            probabilities=probabilities,
        )
