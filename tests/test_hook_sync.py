"""Structural guard: .codex's hooks reach .claude's shared libraries by path.

.codex/hooks holds no copy of those libraries and no link to them. A copy can
drift from the original; a symlink breaks every checkout with
core.symlinks=false -- Git for Windows' default -- because git materialises a
symlink as a *text file holding its target path*, so the import/`source` reads
that path as source code and the hook dies (install.sh declares Windows
(msys/cygwin) in scope). Instead the entry points resolve ../../.claude/hooks
from their own *physical* location and load the single real file from there:
divergence is impossible and the checkout contains nothing git has to
special-case.

Physical resolution is the load-bearing detail. install.sh links
~/.codex/hooks -> <repo>/.codex/hooks, so in production the hooks run from a
path whose parent is $HOME, not the repo: a logical ../../.claude/hooks would
land on ~/.claude/hooks and only resolve because install.sh happens to link
that too. `cd -P` / os.path.realpath() drop that hidden second dependency.

The end-to-end proof that the shell helpers load lives here (through a
symlinked hooks dir, the production shape). For the Python module, the tests in
test_codex_variant_bash_review.py already execute .codex's bash-review.py with
__file__ set to its real path, so the import path is covered there; what those
cannot show -- that resolution survives a symlinked parent -- is pinned below
without running the hook, because the hook's log dir is a hardcoded /tmp path
and this suite never writes outside tmp_path.
"""

import json
import os
import re
import shutil
import subprocess
import sys

import pytest
from conftest import REPO_ROOT

CLAUDE_HOOKS = REPO_ROOT / ".claude/hooks"
CODEX_HOOKS = REPO_ROOT / ".codex/hooks"

# (shared file, loader that consumes it, functions it must define)
SHARED_FILES = [
    ("_bash_review_common.py", "python", []),
    ("_hook_common.sh", "bash", ["hook_log", "hook_notify"]),
    ("_lint_common.sh", "bash", ["hook_lint_file"]),
    ("_format_common.sh", "bash", ["hook_format_file"]),
    # git-push-review.sh's logic, shared by the Claude and Codex adapters. Unlike the
    # others, its helpers keep their plain names (they were moved out of the hook
    # verbatim, which is what makes the move reviewable as a diff) and only the entry
    # point carries the hook_ prefix. The entry point is listed last because it must be
    # the last function in the file: the adapters treat its presence as proof that the
    # whole file was read.
    (
        "_push_review_common.sh",
        "bash",
        [
            "strip_span_counts",
            "hd_read_delim",
            "hd_read_body",
            "strip_quoted_ranges",
            "git_c_dir_from_words",
            "cd_target_from_words",
            "push_show",
            "build_push_report",
            "hook_push_review_run",
        ],
    ),
]

BASH_SHARED_FILES = [row for row in SHARED_FILES if row[1] == "bash"]
PUSH_REVIEW_LIBRARY = "_push_review_common.sh"
PUSH_REVIEW_FUNCTIONS = next(f for n, _, f in SHARED_FILES if n == PUSH_REVIEW_LIBRARY)
PUSH_REVIEW_ENTRY = PUSH_REVIEW_FUNCTIONS[-1]
PUSH_REVIEW_ADAPTERS = [
    CLAUDE_HOOKS / "git-push-review.sh",
    CODEX_HOOKS / "git-push-review.sh",
]

# .codex entry points that load shared helpers, and the marker each prints when
# the load fails. They fail *open* (exit 0) by design, so an unresolved helper
# would otherwise look like a clean run.
SHELL_ENTRY_POINTS = [
    ("lint.sh", "could not load"),
    ("auto-format.sh", "could not load"),
]


def _tracked_symlinks(prefix: str) -> list[str]:
    """Paths git records with mode 120000 under prefix."""
    out = subprocess.run(
        ["git", "ls-files", "-s", "--", prefix],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        line.split("\t", 1)[1]
        for line in out.splitlines()
        if line.startswith("120000 ")
    ]


@pytest.mark.parametrize(
    "name,loader,functions", SHARED_FILES, ids=[f[0] for f in SHARED_FILES]
)
class TestSharedFileHasNoCodexCopy:
    def test_claude_side_is_the_real_file(self, name, loader, functions):
        real = CLAUDE_HOOKS / name
        assert real.is_file(), f"{name}: the .claude copy must exist"
        assert not real.is_symlink(), f"{name}: the .claude copy must be the real file"

    def test_codex_side_has_no_entry_at_all(self, name, loader, functions):
        # A regular file here is a duplicate that can drift; a symlink breaks
        # core.symlinks=false checkouts. Neither is allowed -- the .codex entry
        # points reach the .claude file by path.
        entry = CODEX_HOOKS / name
        assert not os.path.lexists(entry), (
            f"{name}: .codex must not carry its own copy or link; "
            f"entry points resolve ../../.claude/hooks instead"
        )


def test_codex_hooks_tree_carries_no_tracked_symlink():
    """A core.symlinks=false checkout must be byte-identical in this subtree.

    Asserting it on the git index (not the working tree) is what makes it
    meaningful: mode 120000 is exactly what Git for Windows materialises as a
    path-bearing text file.
    """
    assert _tracked_symlinks(".codex/hooks") == []


@pytest.mark.parametrize(
    "name,marker", SHELL_ENTRY_POINTS, ids=[e[0] for e in SHELL_ENTRY_POINTS]
)
def test_shell_entry_point_loads_helpers_through_symlinked_dir(
    shell_env, tmp_path, name, marker
):
    """Run the real hook through a symlinked hooks dir, as install.sh sets up.

    Invoking it at the repo path (what every other test does) cannot catch a
    logical-resolution regression, because there the logical and physical
    parents are the same directory.
    """
    installed = tmp_path / "installed-codex-hooks"
    installed.symlink_to(CODEX_HOOKS)

    res = shell_env.run(installed / name, stdin="{}")

    assert marker not in res.stderr, (
        f"{name}: shared helpers did not resolve through a symlinked hooks dir: "
        f"{res.stderr.strip()}"
    )


def test_python_entry_point_resolves_shared_dir_physically():
    """The .codex hook must derive its import dir from realpath(__file__).

    abspath() does not resolve symlinks, so under install.sh's
    ~/.codex/hooks -> <repo>/.codex/hooks link it would put $HOME/.claude/hooks
    on sys.path -- resolving only by the coincidence that install.sh links that
    path too, and silently failing for anyone who installed only the Codex side.
    """
    source = (CODEX_HOOKS / "bash-review.py").read_text(encoding="utf-8")
    assert "os.path.realpath(__file__)" in source, (
        "bash-review.py must resolve its own real path before deriving the "
        "shared hooks dir (abspath() would not follow the installed symlink)"
    )
    assert ".claude" in source, "bash-review.py must point sys.path at .claude/hooks"


def test_realpath_resolution_survives_a_symlinked_parent(tmp_path):
    """Pin the mechanism the hook relies on, without running the hook itself.

    Mirrors install.sh's layout: an installed dir symlinked at the *hooks*
    level, with the shared tree reachable only via the repo's real path.
    """
    repo = tmp_path / "repo"
    (repo / ".claude/hooks").mkdir(parents=True)
    (repo / ".codex/hooks").mkdir(parents=True)
    (repo / ".claude/hooks/_marker.py").write_text(
        "VALUE = 'shared'\n", encoding="utf-8"
    )

    entry = repo / ".codex/hooks/entry.py"
    entry.write_text(
        "import os, sys\n"
        "_HOOK_DIR = os.path.dirname(os.path.realpath(__file__))\n"
        "sys.path.insert(0, os.path.join(_HOOK_DIR, '..', '..', '.claude', 'hooks'))\n"
        "import _marker\n"
        "print(_marker.VALUE)\n",
        encoding="utf-8",
    )

    installed = tmp_path / "home-codex-hooks"
    installed.symlink_to(repo / ".codex/hooks")

    res = subprocess.run(
        [sys.executable, str(installed / "entry.py")],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
    )

    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "shared"


# --- the shell libraries do what their SHARED_FILES row says -------------------------
# The `functions` column above is the contract a consumer relies on, so it is checked by
# sourcing the file rather than trusted: a rename or a dropped function would otherwise
# only surface as "command not found" inside a hook that fails open.
@pytest.mark.parametrize(
    "name,loader,functions",
    BASH_SHARED_FILES,
    ids=[f[0] for f in BASH_SHARED_FILES],
)
def test_bash_shared_file_defines_its_listed_functions(name, loader, functions):
    script = (
        '. "$1" || exit 99\n'
        "shift\n"
        'for fn in "$@"; do declare -F "$fn" >/dev/null || echo "$fn"; done\n'
    )
    res = subprocess.run(
        ["bash", "-c", script, "bash", str(CLAUDE_HOOKS / name), *functions],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.split() == [], f"{name}: not defined after sourcing: {res.stdout}"


# Shell state a sourced library is not allowed to touch. The adapters source it with
# the harness's stdout already spoken for (the Claude adapter's stdout IS the JSON the
# harness parses), and the hooks fail open by design, so a stray `set -e` / `shopt` /
# `cd` / global variable left behind would change what the adapter does next with no
# error to point at.
_PURITY_SCRIPT = r"""
lib=$1 out=$2
compgen -v | sort >"$out/vars_before"
set -o >"$out/opts_before"
shopt -p >"$out/shopt_before"
trap -p >"$out/trap_before"
printf '%s' "$IFS" | od -An -c >"$out/ifs_before"
pwd >"$out/pwd_before"
. "$lib" >"$out/stdout" 2>"$out/stderr"
echo $? >"$out/rc"
compgen -v | sort >"$out/vars_after"
set -o >"$out/opts_after"
shopt -p >"$out/shopt_after"
trap -p >"$out/trap_after"
printf '%s' "$IFS" | od -An -c >"$out/ifs_after"
pwd >"$out/pwd_after"
"""


def _source_and_diff(name, tmp_path, cwd):
    out = tmp_path / "purity"
    out.mkdir()
    subprocess.run(
        ["bash", "-c", _PURITY_SCRIPT, "bash", str(CLAUDE_HOOKS / name), str(out)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=cwd,
    )

    def read(stem):
        return (out / stem).read_text(encoding="utf-8")

    return read


@pytest.mark.parametrize(
    "name,loader,functions",
    BASH_SHARED_FILES,
    ids=[f[0] for f in BASH_SHARED_FILES],
)
def test_sourcing_a_bash_shared_file_is_silent_and_leaves_the_shell_untouched(
    tmp_path, name, loader, functions
):
    cwd = tmp_path / "somewhere"
    cwd.mkdir()
    read = _source_and_diff(name, tmp_path, cwd)

    assert read("rc").strip() == "0"
    assert read("stdout") == ""
    assert read("stderr") == ""
    for what in ("opts", "shopt", "trap", "ifs", "pwd"):
        assert read(f"{what}_after") == read(f"{what}_before"), (
            f"{name}: sourcing it changed the shell's {what}"
        )
    # Functions only: a top-level assignment would show up as a new variable.
    new_vars = set(read("vars_after").split()) - set(read("vars_before").split())
    assert new_vars == set(), f"{name}: sourcing it left global variables: {new_vars}"


def _function_names_in_file_order(path):
    text = path.read_text(encoding="utf-8")
    return re.findall(r"(?m)^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", text)


def test_the_push_review_entry_point_is_the_last_function_in_its_library():
    """The adapters read "hook_push_review_run is defined" as "the whole file loaded".

    A shell that hits a truncated file or a syntax error stops reading there, so every
    function after the break is simply missing. That inference only holds if nothing
    comes after the entry point.
    """
    names = _function_names_in_file_order(CLAUDE_HOOKS / PUSH_REVIEW_LIBRARY)
    assert names[-1] == PUSH_REVIEW_ENTRY
    assert sorted(names) == sorted(PUSH_REVIEW_FUNCTIONS), "unexpected function set"
    assert len(names) == len(set(names)), "a function is defined twice"


@pytest.mark.parametrize("adapter", PUSH_REVIEW_ADAPTERS, ids=["claude", "codex"])
def test_an_adapter_defines_none_of_the_functions_it_loads(adapter):
    """One copy of the logic: the adapters only call it."""
    text = adapter.read_text(encoding="utf-8")
    for fn in PUSH_REVIEW_FUNCTIONS:
        assert not re.search(rf"(?m)^\s*(function\s+)?{fn}\s*(\(\)|\{{)", text), (
            f"{adapter}: defines {fn} itself instead of loading it from "
            f"{PUSH_REVIEW_LIBRARY}"
        )


_GUARD = re.compile(
    r"(?m)^if ! printf '%s' \"\$cmd\" \| tr -d .*grep -qiE 'push\|send-pack'; then\n"
    r"  exit 0\nfi$"
)


def _guard_of(path):
    matches = _GUARD.findall(path.read_text(encoding="utf-8"))
    assert len(matches) == 1, f"{path}: expected exactly one cheap push guard"
    return matches[0]


def test_both_adapters_use_the_same_cheap_guard_before_loading_the_library():
    claude, codex = PUSH_REVIEW_ADAPTERS
    assert _guard_of(claude) == _guard_of(codex)

    for adapter in PUSH_REVIEW_ADAPTERS:
        lines = adapter.read_text(encoding="utf-8").splitlines()
        guard_at = next(
            i
            for i, line in enumerate(lines)
            if line.startswith("if ! printf '%s' \"$cmd\"")
        )
        load_at = next(
            (
                i
                for i, line in enumerate(lines)
                if PUSH_REVIEW_LIBRARY in line and not line.lstrip().startswith("#")
            ),
            None,
        )
        assert load_at is not None, f"{adapter}: never loads {PUSH_REVIEW_LIBRARY}"
        assert guard_at < load_at, (
            f"{adapter}: the library is loaded before the guard, so a non-push "
            f"command would pay to parse it"
        )


# --- the adapters find the library from where they are installed ---------------------
def _push_payload(command="git push origin main"):
    return json.dumps({"tool_input": {"command": command}})


def _reason(res):
    return json.loads(res.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def test_codex_push_review_runs_the_library_through_a_symlinked_hooks_dir(
    shell_env, tmp_path, git_repo
):
    """The real adapter and the real library, reached the way install.sh lays them out.

    `push target:` is printed by the library alone, so seeing it proves the adapter
    resolved ../../.claude/hooks physically and ran what it found there. Unlike the
    lint/format entry points this one does not fail open on a load failure -- it asks
    -- so the check is for the library's output, not for the absence of a marker.
    """
    installed = tmp_path / "installed-codex-hooks"
    installed.symlink_to(CODEX_HOOKS)

    res = shell_env.run(
        installed / "git-push-review.sh", stdin=_push_payload(), cwd=git_repo
    )

    assert res.returncode == 2, res.stderr
    assert "push target: remote=origin refspec=main" in res.stderr
    assert "could not load" not in res.stderr


def test_claude_push_review_runs_the_library_through_a_symlinked_hooks_dir(
    shell_env, tmp_path, git_repo
):
    installed = tmp_path / "installed-claude-hooks"
    installed.symlink_to(CLAUDE_HOOKS)

    res = shell_env.run(
        installed / "git-push-review.sh", stdin=_push_payload(), cwd=git_repo
    )

    assert res.returncode == 0, res.stderr
    assert "push target: remote=origin refspec=main" in _reason(res)
    assert "could not load" not in res.stderr


def _marker_library():
    stubs = "".join(f"{fn}() {{\n  :\n}}\n" for fn in PUSH_REVIEW_FUNCTIONS[:-1])
    return stubs + (
        f"{PUSH_REVIEW_ENTRY}() {{\n"
        "  push_review_body='BODY-FROM-THE-SIBLING-LIBRARY'\n"
        "  return 0\n"
        "}\n"
    )


@pytest.mark.parametrize("layout", ["whole-dir-links", "per-file-links-in-real-dir"])
def test_codex_adapter_picks_up_the_library_that_sits_next_to_the_claude_hooks(
    shell_env, tmp_path, git_repo, layout
):
    """Which library gets loaded is decided by the layout, not by the repo this runs in.

    A checkout whose .claude/hooks holds a marker library stands in for the repo, and
    the adapter is reached through each install shape: ~/.codex/hooks as one link to
    the checkout's directory (what install.sh creates now), or a real directory of
    per-file links (what an older install left behind, and what this machine has).
    The marker can only come from the library next to the checkout's .claude/hooks.
    """
    checkout = tmp_path / "checkout"
    (checkout / ".claude/hooks").mkdir(parents=True)
    (checkout / ".codex/hooks").mkdir(parents=True)
    (checkout / ".claude/hooks" / PUSH_REVIEW_LIBRARY).write_text(
        _marker_library(), encoding="utf-8"
    )
    shutil.copy2(
        CODEX_HOOKS / "git-push-review.sh", checkout / ".codex/hooks/git-push-review.sh"
    )

    home = tmp_path / "fake-home"
    if layout == "whole-dir-links":
        (home / ".codex").mkdir(parents=True)
        (home / ".codex/hooks").symlink_to(checkout / ".codex/hooks")
    else:
        (home / ".codex/hooks").mkdir(parents=True)
        (home / ".codex/hooks/git-push-review.sh").symlink_to(
            checkout / ".codex/hooks/git-push-review.sh"
        )
        # The per-file layout reaches ../../.claude/hooks only through ~/.claude.
        (home / ".claude").mkdir()
        (home / ".claude/hooks").symlink_to(checkout / ".claude/hooks")

    res = shell_env.run(
        home / ".codex/hooks/git-push-review.sh", stdin=_push_payload(), cwd=git_repo
    )

    assert res.returncode == 2, res.stderr
    assert "BODY-FROM-THE-SIBLING-LIBRARY" in res.stderr
    assert "could not load" not in res.stderr
