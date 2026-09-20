"""format-then-lint.sh -- the PostToolUse handler that orders the two hooks.

auto-format.sh and lint.sh used to sit side by side in one matcher's `hooks`
array in settings.json. That does not order them: Claude Code runs the
handlers matching an event in parallel, and its docs say so outright --
"Since hooks run in parallel, the order is non-deterministic." The repo
assumed otherwise everywhere: README called the pair "(runs in order)",
lint.sh's header says it expects to run after auto-format.sh, and the shared
lint module reports findings the formatter owns (ruff's import sort, rubocop's
Layout) on that basis. When lint won the race it returned those as exit 2 --
handing the agent a hand-fix turn for something the formatter was about to do.

Measured before the fix, on a project whose ruff config selects I:
  lint.sh alone       -> exit 2, one I001 finding
  format-then-lint.sh -> exit 0, no I001

The wrapper cannot be `auto-format.sh && lint.sh` on one line: both read the
payload straight off stdin with jq, so the first consumes it and the second
sees an empty document, finds no file path and returns 0 -- the gate silently
stops existing. It spools the payload to a temp file and feeds both.

These tests stub ruff rather than running it, the way the rest of this suite
stays hermetic: the CI pytest job installs pytest alone, no linters.
"""

import json

import pytest
from conftest import REPO_ROOT

WRAPPER = REPO_ROOT / ".claude/hooks/format-then-lint.sh"
LINT = REPO_ROOT / ".claude/hooks/lint.sh"

# The formatter writes this line; the linter accepts a file only once it is
# present. That encodes the whole invariant under test -- "lint must see what
# the formatter produced" -- without depending on real ruff behaviour.
MARKER = "# formatted"

_RUFF_STUB = f"""
target="${{@: -1}}"
case "$1" in
  format) printf '%s\\n' '{MARKER}' >>"$target"; exit 0 ;;
  check)
    for a in "$@"; do [ "$a" = "--fix" ] && exit 0; done
    grep -q '{MARKER}' "$target" && exit 0
    echo "I001 [*] Import block is un-sorted or un-formatted"
    exit 1
    ;;
esac
exit 0
"""


def _payload(path) -> str:
    return json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(path)}})


@pytest.fixture
def py_target(shell_env, tmp_path):
    """A .py file plus the stub toolchain both hooks reach for."""
    shell_env.stub("ruff", _RUFF_STUB)
    shell_env.stub("bandit")
    target = tmp_path / "m.py"
    target.write_text("x = 1\n", encoding="utf-8")
    return target


def test_both_hooks_receive_the_payload(shell_env, py_target):
    """The stdin hand-off, which is the whole reason a wrapper exists.

    `ruff format` only comes from auto-format.sh and a bare `ruff check` only
    from lint.sh, so seeing both proves each child got the payload. Chaining
    the two scripts without spooling would leave the second with an empty
    stdin, no file path, and an early exit 0 -- visible here as a missing
    call rather than as a failure.
    """
    res = shell_env.run(WRAPPER, stdin=_payload(py_target))
    assert res.returncode == 0, res.stderr
    assert any(c.startswith("ruff format ") for c in shell_env.calls), shell_env.calls
    assert any(
        c.startswith("ruff check ") and "--fix" not in c for c in shell_env.calls
    ), shell_env.calls


def test_lint_runs_after_formatting(shell_env, py_target):
    """The ordering itself: same input, opposite verdicts.

    The lint.sh leg is not decoration -- it is the anti-vacuity guard. If the
    stub ever stopped reproducing a formatter-owned finding, both legs would
    return 0 and the first assertion would pass while pinning nothing.
    """
    alone = shell_env.run(LINT, stdin=_payload(py_target))
    assert alone.returncode == 2, (
        "precondition: lint alone must reproduce the formatter-owned finding, "
        f"got {alone.returncode}: {alone.stdout}"
    )

    py_target.write_text("x = 1\n", encoding="utf-8")
    ordered = shell_env.run(WRAPPER, stdin=_payload(py_target))
    assert ordered.returncode == 0, (
        f"lint saw the unformatted file: {ordered.stdout}\n{ordered.stderr}"
    )


def test_lint_exit_code_is_the_hooks_exit_code(shell_env, tmp_path):
    """A real finding must still reach the agent as exit 2.

    Ordering the pair is worthless if the wrapper swallows the gate's verdict,
    so pin that the last word belongs to lint.sh.
    """
    shell_env.stub("ruff", 'echo "F821 Undefined name"; [ "$1" = check ] && exit 1\n')
    shell_env.stub("bandit")
    target = tmp_path / "m.py"
    target.write_text("x = 1\n", encoding="utf-8")

    res = shell_env.run(WRAPPER, stdin=_payload(target))
    assert res.returncode == 2, f"expected the lint gate's 2, got {res.returncode}"
    assert "F821" in res.stderr, res.stderr


def test_missing_file_is_let_through(shell_env, tmp_path):
    """Fail-open: an edit to something neither hook handles must not block."""
    res = shell_env.run(WRAPPER, stdin=_payload(tmp_path / "gone.py"))
    assert res.returncode == 0, res.stderr
