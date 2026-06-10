---
name: python-scripting-patterns
description: Python scripting best practices for automation, CLI tools, and Claude Code hooks. Use this skill whenever writing or modifying Python scripts, CLI tools, or Claude Code hooks written in Python (.py files under .claude/hooks/), automation scripts, data processing scripts, or any standalone Python utility — even for small edits, since stdin-parsing mistakes, broad except clauses, and missing exit-code discipline are easy to introduce silently and hard to debug later.
---

# Python Scripting Patterns

Best practices for Python scripts, with specific patterns for Claude Code hooks and automation on macOS.

## Script Skeleton

Every standalone script uses this structure so it is importable AND runnable:

```python
#!/usr/bin/env python3
"""One-line description of what this script does."""

import sys


def main() -> int:
    # ... do work ...
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- `raise SystemExit(main())` propagates the integer return code to the OS without leaving a traceback on the terminal. `sys.exit()` does the same but is less idiomatic for a `main()` that already returns int.
- The `def main() -> int` boundary makes the function unit-testable with `monkeypatch` without spawning a subprocess.

### Dependency policy

Hooks and one-off scripts must work with stdlib only. Adding a third-party dependency to a hook means every developer needs it installed before Claude Code runs at all.

```python
# ❌ WRONG: pip dependency in a hook
import httpx  # breaks if not installed

# ✅ CORRECT: stdlib HTTP for hooks/scripts
import urllib.request
import urllib.error
```

When a third-party package is genuinely needed (e.g., a standalone CLI tool), use `uv` as the runner so the venv is managed automatically:

```
#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["httpx", "rich"]
# ///
```

## Reading JSON from stdin Safely

Hooks receive structured input on stdin. Crash = the entire tool call is blocked for the user.

```python
# ❌ WRONG: crashes on empty stdin or malformed JSON, blocks Claude
hook_input = json.loads(sys.stdin.read())

# ✅ CORRECT: fail-open — let the tool call through on any parse error
import json, sys

try:
    hook_input = json.loads(sys.stdin.buffer.read())
except (json.JSONDecodeError, ValueError):
    sys.exit(0)  # unrecognised input → fail open

tool_name  = hook_input.get("tool_name", "")
tool_input = hook_input.get("tool_input", {})
command    = tool_input.get("command", "")
```

- Use `.get()` with defaults everywhere; the field layout varies across hook event types.
- `sys.stdin.buffer.read()` (bytes) is more robust than `sys.stdin.read()` (text) when locale encoding is ambiguous.

## Claude Code Hook Specifics

See `~/.claude/skills/shell-scripting-patterns/SKILL.md` for the full exit-code table and bash equivalents. The Python-specific rules:

### Exit code discipline

| Code  | Meaning                                                     |
| ----- | ----------------------------------------------------------- |
| 0     | Allow / success (stdout shown in transcript mode only)      |
| 2     | Block; **stderr** is fed back to Claude for self-correction |
| other | Non-blocking error; stderr shown to user                    |

```python
# ❌ WRONG: printing a human message to stderr and exiting 0 — Claude never sees it
print("lint failed", file=sys.stderr)
sys.exit(0)

# ✅ CORRECT: exit 2 so Claude sees the feedback and self-corrects
print("lint failed: unused variable 'x' at line 42", file=sys.stderr)
sys.exit(2)
```

### Emitting hookSpecificOutput (PreToolUse)

Always build hook JSON with `json.dumps`, never string formatting — the reason field can contain quotes, newlines, or Unicode.

```python
# ❌ WRONG: breaks when reason contains quotes or newlines
print('{"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "' + reason + '"}}')

# ✅ CORRECT
import json, sys

def emit_decision(decision: str, reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,       # "allow" | "deny" | "ask"
            "permissionDecisionReason": reason,
        }
    }))
```

### Fail-open guard pattern

```python
# Exit early for irrelevant tool calls — never block what you don't understand
if tool_name != "Bash":
    sys.exit(0)

if not command:
    sys.exit(0)
```

### Stop hook loop guard

A Stop hook that blocks causes Claude to re-run, re-triggering the hook. Always check:

```python
if hook_input.get("stop_hook_active", False):
    sys.exit(0)
```

## subprocess Best Practices

```python
# ❌ WRONG: shell=True with any external data is a command injection risk
result = subprocess.run(f"codex exec {user_prompt}", shell=True, ...)

# ✅ CORRECT: arg list + timeout + capture_output
import subprocess

try:
    result = subprocess.run(
        ["codex", "exec", "--skip-git-repo-check", prompt],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,   # handle returncode manually; check=True raises on non-zero
    )
except subprocess.TimeoutExpired:
    return "ERROR", "timed out"
except FileNotFoundError:
    return "ERROR", "binary not found"
```

- Always set `timeout=` — a hung subprocess hangs the hook, which stalls Claude indefinitely.
- Use `check=False` when you need to inspect `returncode` yourself; use `check=True` only when a non-zero exit is truly unexpected and you want an automatic `CalledProcessError`.

## pathlib over os.path

```python
# ❌ WRONG: string juggling
log_dir = os.path.join(os.path.expanduser("~"), ".claude", "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "hook.log")

# ✅ CORRECT: pathlib is readable and composable
from pathlib import Path

LOG_DIR = Path.home() / ".claude" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_file = LOG_DIR / "hook.log"
```

For file I/O, always specify encoding to avoid locale surprises:

```python
log_file.write_text(content, encoding="utf-8")
text = log_file.read_text(encoding="utf-8")
```

Atomic writes (never leave a partial file visible):

```python
from pathlib import Path
import tempfile, os

def atomic_write(path: Path, content: str) -> None:
    tmp = Path(tempfile.mktemp(dir=path.parent, suffix=".tmp"))
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)   # atomic on POSIX same-filesystem
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
```

## Logging with Rotation

Use `logging` with `RotatingFileHandler` — not bare `print()` calls left in the code, not manual line-counting.

```python
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

def _make_logger(name: str, log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=500_000,   # ~500 KB per file
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    return logger

logger = _make_logger("bash-review", Path.home() / ".claude" / "logs" / "bash-review.log")
```

- `RotatingFileHandler` handles rotation atomically — no manual tail/rewrite needed.
- Never leave `print()` debugging statements in committed code; the Stop hook will flag them.

## Error Handling & Exit Discipline

```python
# ❌ WRONG: swallows all exceptions, hides real bugs
try:
    result = do_work()
except Exception:
    pass

# ✅ CORRECT: narrow clauses, log full traceback to file, keep stderr concise
import traceback

try:
    result = do_work()
except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
    logger.error("API call failed: %s\n%s", exc, traceback.format_exc())
    print(f"review skipped: {exc}", file=sys.stderr)
    sys.exit(0)   # fail-open for hooks; fail-closed (sys.exit(1)) for CLI tools
```

- Group related error types into a named tuple at module level (see `_API_ERRORS` pattern in `bash-review.py`) so the catch clause stays readable.
- Log `traceback.format_exc()` to the file, but write only a short summary to stderr — Claude reads stderr and a wall of traceback is unhelpful.

## Type Hints & Dataclasses

Add type hints to all public functions — they are documentation, they catch mistakes with mypy, and they make the function testable.

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class ReviewConfig:
    api_key: str
    model: str = "gemini-flash-lite-latest"
    fallback_model: str = "gemini-flash-latest"
    timeout: int = 30
    safe_commands: list[str] = field(default_factory=list)

def run_review(command: str, cfg: ReviewConfig) -> tuple[str, str]:
    """Return (verdict, raw_output). verdict is ALLOW | ASK | DENY | ERROR."""
    ...
```

- `frozen=True` on dataclasses makes config objects immutable (matches the coding-style rule).
- Prefer `tuple[str, str]` return types over parallel global variables or mutable dicts.

## argparse for CLI Scripts

```python
import argparse

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Review bash commands before execution")
    p.add_argument("--dry-run", action="store_true", help="Print decision without acting")
    p.add_argument("--log-dir", type=Path, default=Path.home() / ".claude" / "logs")
    return p.parse_args(argv)

def main() -> int:
    args = _parse_args()
    ...
```

Accepting `argv` as a parameter (defaulting to `None`, which argparse interprets as `sys.argv[1:]`) makes the CLI fully testable without spawning a subprocess.

## Testing Hooks & Scripts

```python
# pytest with capsys + monkeypatch + tmp_path
import json
import pytest
from my_hook import main   # importable because of the if __name__ == "__main__" guard

def _make_hook_json(**overrides) -> str:
    base = {"tool_name": "Bash", "tool_input": {"command": "git status"}}
    base.update(overrides)
    return json.dumps(base)

def test_safe_command_is_allowed(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(_make_hook_json()))
    rc = main()
    out = capsys.readouterr().out
    decision = json.loads(out)["hookSpecificOutput"]["permissionDecision"]
    assert rc == 0
    assert decision == "allow"

def test_deny_command_is_blocked(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(_make_hook_json(tool_input={"command": "rm -rf /"})))
    rc = main()
    decision = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"]
    assert decision == "deny"
```

- Use `tmp_path` for any test that writes log files so tests never pollute `~/.claude/logs/`.
- Use `monkeypatch.setenv` to inject API keys; never hard-code them in tests.
- Run with `pytest` and check coverage: `pytest --cov=my_hook --cov-report=term-missing`.

## Tooling

```bash
# Format + lint in one pass (replaces black + isort + flake8)
ruff check --fix my_hook.py
ruff format my_hook.py

# Optional static type check
mypy my_hook.py --strict

# Test a hook by piping synthetic JSON (mirrors the shell-scripting-patterns approach)
echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' | python3 my_hook.py
```

## Pre-Commit Checklist

Before committing a Python script or hook:

- [ ] `#!/usr/bin/env python3` shebang and `if __name__ == "__main__": raise SystemExit(main())`
- [ ] stdin parsed with try/except; hook exits 0 on parse failure (fail-open)
- [ ] `hookSpecificOutput` built with `json.dumps`, never string formatting
- [ ] No `shell=True` in subprocess calls; `timeout=` always set
- [ ] `pathlib.Path` used instead of `os.path`; all file I/O uses `encoding="utf-8"`
- [ ] `RotatingFileHandler` used for logs; no bare `print()` debugging left behind
- [ ] Narrow `except` clauses — no bare `except Exception: pass`
- [ ] Type hints on all public functions; dataclasses for structured config
- [ ] `ruff check` and `ruff format` pass with no warnings
- [ ] Tests cover the main allow/deny/error paths with synthetic hook JSON
