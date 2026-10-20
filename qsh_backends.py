"""Command generation backends. Backends return JSON and never execute it."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:1.5b-instruct-q4_K_S"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"


class BackendError(Exception):
    """A backend could not produce a response."""


def ollama_request(host: str, endpoint: str, payload: dict, timeout: float) -> dict:
    """Send a JSON request to either Ollama's chat or decision API."""
    host = host.rstrip("/")
    if "://" not in host:
        host = f"http://{host}"
    parsed = urlsplit(host)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise BackendError("Ollama host must be an HTTP or HTTPS URL.")
    request = Request(
        f"{host}{endpoint}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        if error.code == 404:
            model = payload["model"]
            raise BackendError(
                f"Ollama could not find model {model!r} or endpoint {endpoint}. "
                f"Check your Ollama version and install the model with: ollama pull {model}\n{detail}"
            ) from error
        raise BackendError(f"Ollama returned HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise BackendError(
            f"Cannot reach Ollama at {host}: {error.reason}. Start it with: ollama serve"
        ) from error
    except TimeoutError as error:
        raise BackendError(f"Ollama timed out after {timeout:g} seconds.") from error
    if not isinstance(data, dict):
        raise BackendError("Ollama returned an invalid response.")
    if data.get("error"):
        raise BackendError(f"Ollama failed: {data['error']}")
    return data


class Backend(Protocol):
    model: str | None

    def generate(self, prompt: str, schema_path: Path, timeout: float) -> str:
        """Return a JSON object matching schema_path as text."""
        ...


@dataclass
class CodexBackend:
    model: str | None = None

    def generate(self, prompt: str, schema_path: Path, timeout: float) -> str:
        with tempfile.TemporaryDirectory(prefix="qsh-codex-") as directory:
            response_file = Path(directory) / "response.json"
            args = [
                "codex", "exec",
                "--skip-git-repo-check",
                "--sandbox", "read-only",
                "--output-schema", str(schema_path),
                "--output-last-message", str(response_file),
            ]
            if self.model:
                args.extend(["--model", self.model])
            args.append("-")
            try:
                result = subprocess.run(
                    args,
                    input=prompt,
                    text=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                    check=False,
                )
            except FileNotFoundError as error:
                raise BackendError("Codex is not installed or not in PATH.") from error
            except subprocess.TimeoutExpired as error:
                raise BackendError(f"Codex timed out after {timeout:g} seconds.") from error
            if result.returncode:
                detail = result.stderr.strip()
                raise BackendError(f"Codex failed to generate a command.\n{detail}".rstrip())
            if not response_file.is_file():
                raise BackendError("Codex did not return a response.")
            reported_model = re.search(r"^model:[ \t]*(\S[^\r\n]*)", result.stderr, re.MULTILINE)
            if reported_model:
                self.model = reported_model.group(1).strip()
            return response_file.read_text(encoding="utf-8")


@dataclass
class OllamaBackend:
    model: str = DEFAULT_OLLAMA_MODEL
    host: str = DEFAULT_OLLAMA_HOST

    def generate(self, prompt: str, schema_path: Path, timeout: float) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "format": json.loads(schema_path.read_text(encoding="utf-8")),
            "stream": False,
            "options": {"temperature": 0},
        }
        data = ollama_request(self.host, "/api/chat", payload, timeout)
        message = data.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise BackendError("Ollama did not return message content.")
        reported_model = data.get("model")
        if isinstance(reported_model, str) and reported_model.strip():
            self.model = reported_model.strip()
        return message["content"]


# Add backend factories here; the runner uses the same generate() contract for all.
BACKENDS = {
    "codex": lambda model, host: CodexBackend(model=model),
    "ollama": lambda model, host: OllamaBackend(
        model=model or DEFAULT_OLLAMA_MODEL, host=host
    ),
}
