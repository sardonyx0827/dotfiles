#!/usr/bin/env python3
"""Static credential scanner CLI — shared by the Vim/Neovim AI integration.

The editors' AI features shell out to this before sending a buffer selection,
diff, or instruction to an AI tool (claude / codex / gemini / copilot). It reads
the payload from stdin and reuses the exact same `scan_secrets` the bash-review
hooks use, so credential *values* are refused at the editor the same way they
are refused for Bash — without duplicating the regexes (Lua / VimScript patterns
cannot express the PCRE lookaheads / alternations these rely on).

Contract (kept small on purpose so editor glue stays trivial):

    exit 0  -> clean: no credential value found, nothing written to stdout
    exit 1  -> credential detected: a generic category label on stdout
               (the matched value is NEVER printed — it must not leak onward)
    exit 2  -> the scanner itself is unavailable (import failure). Callers treat
               a non-{0,1} exit (this, or 127 when python is missing) as
               "cannot verify" and fail open with a visible warning.

Payload is read from stdin, never argv: a secret on argv would show up in
`ps aux`, which is its own leak.
"""

import sys
from pathlib import Path

# scan_secrets is the single source of truth for the credential patterns; it
# lives with the bash-review hooks. Import it from there rather than copying the
# regexes (matches how tests/conftest.py reaches the hook modules).
_HOOKS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

try:
    from _bash_review_common import scan_secrets
except Exception as exc:  # pragma: no cover - defensive: unresolved import
    print(f"secret-scan unavailable: {exc}", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    text = sys.stdin.read()
    # The editor payload is free text; feed it as the command haystack. The
    # second arg mirrors scan_secrets' (command, tool_input) shape.
    try:
        found, label = scan_secrets(text, {})
    except Exception as exc:  # noqa: BLE001 - never misreport a crash as clean
        # An unexpected scanner failure must read as "unavailable" (exit 2), not
        # as clean (0). Callers fail open with a warning on a non-{0,1} exit;
        # keeping it distinct from a real detection (1) avoids a confusing empty
        # "credential detected" dialog.
        print(f"secret-scan error: {exc}", file=sys.stderr)
        return 2
    if found:
        sys.stdout.write(label + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
