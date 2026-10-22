# qsh

Generate a POSIX `sh` command for Linux or macOS from a request, review its
command/risk/explanation, then execute it after answering `y`. Python handles
generation, validation, and execution. `qsh` runs from any shell, and generated
commands run in `/bin/sh`. The target platform is detected automatically and can
be overridden with `--platform`. The preview shows the backend and model used, including
Codex's resolved default. With `--generate-only`, model information goes to stderr
so stdout remains JSON.

Requires Python 3.10+ and Ollama 0.40.4+. The Linux defaults are
`qwen2.5:3b-instruct` and `kev:0.8b-q8_0`; macOS retains
`qwen2.5-coder:1.5b-instruct-q4_K_S` and `kev:0.8b-mxfp8`.
No Python packages or `jq` are needed. Ollama is required even when Codex generates
the command.

## Setup

Install [Ollama](https://ollama.com) and start its app (or run `ollama serve`),
then run:

```sh
curl -fsSL https://raw.githubusercontent.com/charmpitz/qsh/main/install.sh | sh
```

Before writing anything, the installer checks for `git`, Python 3.10+, and a
running Ollama 0.40.4+, and that it can write to the install locations. It then
clones the repository into `~/qsh` (or updates an existing checkout), links `qsh`
into `~/.local/bin`, and pulls the generator and risk-review models. No `sudo` is
needed. Set `QSH_DIR` or `QSH_BIN_DIR` for the `sh` command to use other locations,
for example `... | QSH_DIR=~/src/qsh sh`. If `~/.local/bin` is not on your `PATH`,
add it in your shell's startup file.

## Local models with Ollama

Ollama selects models for the target platform: `qwen2.5:3b-instruct` on Linux,
or the original `qwen2.5-coder:1.5b-instruct-q4_K_S` on macOS, to generate commands:

```sh
qsh "show the 10 largest files in this directory"
```

The Ollama backend talks directly to the local server at `http://127.0.0.1:11434`;
it does not require Codex. It uses Ollama's
[structured output API](https://docs.ollama.com/capabilities/structured-outputs)
with the same schema as Codex.

Choose another installed model with `--model` or `AI_MODEL`. Use `AI_BACKEND` to
override the default backend.

```sh
# Use any other model you have downloaded.
qsh --model OTHER_MODEL "list git branches"
```

## Codex

Select Codex with `--backend codex` after installing and authenticating its CLI.
Its configured model is used unless you pass `--model` or set `AI_MODEL`.

```sh
qsh --backend codex "show the 10 largest files in this directory"
qsh --backend codex --model YOUR_CODEX_MODEL "list git branches"
```

## Risk review with Kev

Every valid generated command is independently reviewed by the target platform's Kev
model (`kev:0.8b-q8_0` on Linux; the original `kev:0.8b-mxfp8` on macOS) through
Ollama's [System One API](https://docs.ollama.com/api/systemone), including with
`--generate-only`. Kev is a decision model, so it scores each rule rather than
generating a command or a free-form explanation. The preview shows both models.

The editable policy in `qsh-risk-rules.json` checks local changes, network access,
data loss, broad destruction, privilege escalation, remote code execution,
credential access, data uploads, system settings, process disruption, remote
mutations, and code whose effects cannot be determined. It instructs Kev to inspect
nested commands and substitutions and to distinguish executed code from inert text.
Kev is unloaded after each review to release its model memory.

A rule matches when its returned score is at least `threshold` (default `0.5`).
The final risk is the highest of the generator's rating, existing local critical
checks, and matched rule severities. Kev cannot lower an existing rating. Matched
rule descriptions are added to the explanation. The scores are model judgments,
not guarantees that a command is safe.

Local critical checks also recognize reordered and separated recursive/force
`rm` flags, such as `rm -fr` and `rm -r -f`, independently of Kev's answer.

If Kev is unavailable or returns missing/invalid scores, `qsh` stops before the
execution prompt. Execution still requires `y` or `Y`. Choose another installed
decision model through `--risk-model` or `AI_RISK_MODEL`; `--model` continues to
select the command generator. Both local stages use `--ollama-host`/`OLLAMA_HOST`.

## Options

| Option | Default | Purpose |
| --- | --- | --- |
| `--backend` | `AI_BACKEND` or `ollama` | Select `codex` or `ollama` |
| `--model` | `AI_MODEL`, then target-platform default | Select a model |
| `--risk-model` | `AI_RISK_MODEL`, then target-platform default | Select the Ollama decision model for risk review |
| `--ollama-host` | `OLLAMA_HOST` or `http://127.0.0.1:11434` | Select an Ollama server |
| `--platform` | `QSH_PLATFORM` or `auto` | Target `linux` or `macos`; auto-detect by default |
| `--timeout` | `180` | Timeout in seconds for each generation or risk-review request |
| `--generate-only` | Off | Print validated JSON without executing |

With no request, `qsh` prompts for one. Use `--` before a request containing flags.

```sh
qsh --generate-only "print the current directory"
qsh --platform macos "show the size of this file"
qsh -- "explain how to use ls --all"
```

Both backends use the same validation, local risk escalation, preview, and
confirmation. Invalid responses fail before execution; only `y` or `Y` executes.
Commands run through Python in a child `/bin/sh` with the current directory and
exported environment, and their exit status is returned. Changes such as `cd`
or `export` apply only inside that child shell; aliases and functions from the
calling interactive shell are not inherited.

## Adding a backend

In `qsh_backends.py`, implement `generate(prompt, schema_path, timeout) -> str` and
register a factory in `BACKENDS`. Expose a `model` attribute with the selected model
name, updating it with the resolved model if the backend reports one. Return JSON
with `command`, `risk` (`LOW`, `MEDIUM`, `HIGH`, or `CRITICAL`), and `explanation` as defined in
`qsh-command-schema.json`. Raise `BackendError` for backend failures. The shared
Python runner handles the remaining workflow.

## Checks

```sh
python3 -m unittest discover -s tests -v
```

Evaluate the policy against labeled command strings without executing them:

```sh
python3 evaluate_risk.py --output risk-evaluation.json
```

The report records Kev's scores and matched rules, the final risk after local
checks, expected labels, classification errors, timings, and policy/case hashes.
It saves a checkpoint after each case and stops on a failed review. The script
exits nonzero for incorrect labels or incomplete/failed runs. Use `--model`,
`--host`, or `--cases` to evaluate another decision model, server, or labeled set.
