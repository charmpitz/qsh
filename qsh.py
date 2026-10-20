#!/usr/bin/env python3
"""Generate, review, and execute a zsh command using a selectable model backend."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from qsh_backends import BACKENDS, DEFAULT_OLLAMA_HOST, DEFAULT_OLLAMA_MODEL, Backend, BackendError
from qsh_risk import DEFAULT_RISK_MODEL, KevRiskReviewer, highest_risk, is_forced_recursive_removal


SCHEMA_PATH = Path(__file__).resolve().with_name("qsh-command-schema.json")
RISK_STYLES = {
    "LOW": ("32", "●"),
    "MEDIUM": ("33", "●"),
    "HIGH": ("31", "▲"),
    "CRITICAL": ("1;31", "⚠"),
}
CRITICAL_PATTERNS = (
    "rm -rf", "sudo ", "git reset --hard", "git clean -fd", "mkfs",
    "diskutil erase", "shutdown ", "reboot ", "kill ", "pkill ",
    "killall ", "| sh", "| bash",
)


@dataclass(frozen=True)
class Suggestion:
    command: str
    risk: str
    explanation: str

    @classmethod
    def from_json(cls, response: str) -> Suggestion:
        data = json.loads(response)
        fields = {"command", "risk", "explanation"}
        if not isinstance(data, dict) or data.keys() != fields:
            raise ValueError("Response must contain command, risk, and explanation only.")
        if any(not isinstance(value, str) or not value.strip() for value in data.values()):
            raise ValueError("All response fields must be nonempty strings.")
        if data["risk"] not in RISK_STYLES:
            raise ValueError("Response risk must be LOW, MEDIUM, HIGH, or CRITICAL.")
        if "\0" in data["command"]:
            raise ValueError("Response command contains a null character.")
        if is_forced_recursive_removal(data["command"]) or any(
            pattern in data["command"] for pattern in CRITICAL_PATTERNS
        ):
            data["risk"] = "CRITICAL"
        return cls(**data)


def build_prompt(request: str, cwd: Path) -> str:
    return f"""You are a shell command generator for macOS zsh.

Current working directory:
{cwd}

User request:
{request}

Do not execute anything.
Do not modify anything.
Do not access the network.

Generate exactly ONE zsh command that fulfills the request.
Assess the real risk of executing it.
Explain briefly what it does.
Return only JSON matching this schema, with no markdown or extra text:
{SCHEMA_PATH.read_text(encoding="utf-8")}"""


def style(text: str, code: str) -> str:
    if sys.stdout.isatty() and "NO_COLOR" not in os.environ:
        return f"\033[{code}m{text}\033[0m"
    return text


def show_suggestion(suggestion: Suggestion, model_label: str, risk_model: str | None = None) -> None:
    color, icon = RISK_STYLES[suggestion.risk]
    divider = style("─" * 56, "2")
    print(divider)
    print(style("MODEL", "1;36"))
    print(f"  {model_label}")
    if risk_model:
        print(f"  Risk reviewer: ollama / {risk_model}")
    print(f"\n{style('COMMAND', '1;36')}")
    print(f"  {style(suggestion.command, '32')}")
    print(f"\n{style('RISK', '1;36')}")
    print(f"  {style(f'{icon} {suggestion.risk}', color)}")
    print(f"\n{style('WHY', '1;36')}")
    print(f"  {style(suggestion.explanation, '2')}")
    print(divider)


def positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be a positive number") from error
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive number")
    return timeout


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend", choices=BACKENDS, default=os.environ.get("AI_BACKEND", "ollama"),
        help="generation backend (default: AI_BACKEND or ollama)",
    )
    parser.add_argument(
        "--model", default=os.environ.get("AI_MODEL") or None,
        help=f"model name (default: Codex configuration or {DEFAULT_OLLAMA_MODEL})",
    )
    parser.add_argument(
        "--ollama-host", default=os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST,
        help="Ollama server URL (default: OLLAMA_HOST or http://127.0.0.1:11434)",
    )
    parser.add_argument(
        "--risk-model", default=os.environ.get("AI_RISK_MODEL") or DEFAULT_RISK_MODEL,
        help=f"Ollama decision model for risk review (default: AI_RISK_MODEL or {DEFAULT_RISK_MODEL})",
    )
    parser.add_argument(
        "--timeout", type=positive_timeout, default=180,
        help="timeout for each generation or risk request in seconds (default: 180)",
    )
    parser.add_argument(
        "--generate-only", action="store_true",
        help="print the validated JSON without confirmation or execution",
    )
    parser.add_argument("prompt", nargs="*", help="request (prompted when omitted)")
    args = parser.parse_args(argv)
    if args.backend not in BACKENDS:
        parser.error(f"unknown backend: {args.backend!r}; choose from {', '.join(BACKENDS)}")
    return args


def run(args: argparse.Namespace) -> int:
    request = " ".join(args.prompt) if args.prompt else input(style("❯ ", "1;36"))
    if not request.strip():
        return 1
    backend: Backend = BACKENDS[args.backend](args.model, args.ollama_host)
    if not args.generate_only:
        selected_model = f" / {backend.model}" if backend.model else ""
        print(f"Generating command ({args.backend}{selected_model})...", file=sys.stderr, flush=True)
    response = backend.generate(build_prompt(request, Path.cwd()), SCHEMA_PATH, args.timeout)
    suggestion = Suggestion.from_json(response)
    reviewer = KevRiskReviewer(model=args.risk_model, host=args.ollama_host)
    if not args.generate_only:
        print(f"Checking risk (ollama / {reviewer.model})...", file=sys.stderr, flush=True)
    review = reviewer.evaluate(suggestion.command, request, Path.cwd(), args.timeout)
    explanation = suggestion.explanation
    if review.findings:
        explanation += f" Risk review ({review.model}): {review.summary}."
    suggestion = replace(
        suggestion, risk=highest_risk(suggestion.risk, review.risk), explanation=explanation,
    )
    model_label = f"{args.backend} / {backend.model or 'configured default'}"
    if args.generate_only:
        print(f"Model: {model_label}", file=sys.stderr, flush=True)
        print(f"Risk reviewer: ollama / {review.model}", file=sys.stderr, flush=True)
        print(json.dumps(asdict(suggestion), ensure_ascii=False))
        return 0

    show_suggestion(suggestion, model_label, review.model)
    try:
        confirmation = input(f"\n{style('Execute this command?', '1')} [y]/[N] ")
    except EOFError:
        confirmation = ""
    if confirmation not in ("y", "Y"):
        print(style("○ Cancelled", "33"))
        return 0

    print(f"\n$ {style(suggestion.command, '32')}\n", flush=True)
    result = subprocess.run(["zsh", "-c", suggestion.command], check=False)
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n○ Cancelled", file=sys.stderr)
        return 130
    except EOFError:
        return 1
    except (BackendError, OSError, ValueError) as error:
        print(f"✗ {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
