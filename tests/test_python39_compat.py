"""Python 3.9 compatibility of the modules on the editor and hook import path.

macOS ships `/usr/bin/python3` as 3.9. A GUI-launched Neovim (PATH=/usr/bin:/bin)
resolves `python3` to it, and so does a pyenv shim in a directory whose
`.python-version` names a version that is not installed. On 3.9 an annotation
such as `list[str] | None` is evaluated when the `def` runs and raises TypeError,
so the module fails to import:

- scripts/secret_scan.py exits 2 ("unavailable"), and the editors treat that as
  "send anyway with a warning": the secret gate fails open for every payload.
- scripts/gemini_api.py dies with a traceback instead of its one-line stderr
  contract.
- bash-review cannot import its shared module, so every Bash call becomes an
  ask (Claude) or a block (Codex).

`from __future__ import annotations` keeps every annotation a string, which is
all these modules need. CI runs 3.12/3.13 only, so the AST guard below is the
part CI can see; the subprocess tests run wherever a 3.9 interpreter exists
(stock macOS) and skip elsewhere.
"""

import ast
import os
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

# Every module the editor gate (secret_scan.py -> _bash_review_common) and the
# bash-review entries import, plus the Gemini helper the editors call.
FUTURE_REQUIRED = [
    ".claude/hooks/_bash_review_common.py",
    ".claude/hooks/bash-review.py",
    ".codex/hooks/bash-review.py",
    "scripts/secret_scan.py",
    "scripts/gemini_api.py",
]


def _has_future_annotations(path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


@pytest.mark.parametrize("rel", FUTURE_REQUIRED)
def test_module_defers_annotation_evaluation(rel):
    assert _has_future_annotations(REPO_ROOT / rel), (
        f"{rel} must start with `from __future__ import annotations` so that "
        "PEP 604/585 annotations do not break the import on Python 3.9"
    )


def _python39() -> str | None:
    for candidate in ("/usr/bin/python3", shutil.which("python3.9")):
        if not candidate or not os.path.exists(candidate):
            continue
        probe = subprocess.run(  # noqa: S603
            [candidate, "-c", "import sys; print(sys.version_info[:2] == (3, 9))"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.stdout.strip() == "True":
            return candidate
    return None


PY39 = _python39()
needs_py39 = pytest.mark.skipif(PY39 is None, reason="no Python 3.9 interpreter")


def _run39(args, *, stdin="", env=None):
    # PATH=/usr/bin:/bin is what a GUI-launched Neovim inherits.
    base = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "")}
    return subprocess.run(  # noqa: S603
        [PY39, "-I", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env={**base, **(env or {})},
        check=False,
        timeout=60,
    )


@needs_py39
def test_secret_scan_detects_a_credential_under_py39():
    r = _run39(
        [str(REPO_ROOT / "scripts/secret_scan.py")], stdin="key AKIAIOSFODNN7EXAMPLE\n"
    )
    assert (r.returncode, r.stdout.strip()) == (1, "AWS access key"), r.stderr


@needs_py39
def test_secret_scan_passes_clean_input_under_py39():
    r = _run39([str(REPO_ROOT / "scripts/secret_scan.py")], stdin="just some code\n")
    assert (r.returncode, r.stdout) == (0, ""), r.stderr


@needs_py39
def test_gemini_api_reports_a_missing_key_in_one_line_under_py39():
    r = _run39([str(REPO_ROOT / "scripts/gemini_api.py"), "--system", "S"], stdin="x")
    assert r.returncode == 2
    assert "GEMINI_API_KEY" in r.stderr
    assert "Traceback" not in r.stderr
    assert len(r.stderr.strip().splitlines()) == 1


@needs_py39
def test_bash_review_common_classifies_under_py39():
    driver = (
        "import sys; sys.path.insert(0, sys.argv[1]); import _bash_review_common as c; "
        "s = c._split_commands('ls -la'); "
        "print(c.find_deny_command(s)[0], all(c._can_skip_review(x) for x in s))"
    )
    r = _run39(["-c", driver, str(REPO_ROOT / ".claude/hooks")])
    assert (r.returncode, r.stdout.strip()) == (0, "False True"), r.stderr


@needs_py39
def test_inert_text_deny_exemption_runs_under_py39():
    # The heredoc / quoted-text exemption (find_deny_command with the command)
    # is its own code path; exercise it on 3.9 as well.
    command = "git commit -m \"$(cat <<'EOF'\nfix: x\n\nssh keys are rotated\nEOF\n)\""
    driver = (
        "import sys; sys.path.insert(0, sys.argv[1]); import _bash_review_common as c; "
        "cmd = sys.argv[2]; "
        "print(c.find_deny_command(c._split_commands(cmd), cmd))"
    )
    r = _run39(["-c", driver, str(REPO_ROOT / ".claude/hooks"), command])
    assert (r.returncode, r.stdout.strip()) == (0, "(False, '')"), r.stderr
