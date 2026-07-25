"""Structural guard: .codex's hooks reach .claude's shared libraries by path.

Three designs have held this invariant, each replacing the previous one's
failure mode:

1. Byte-identical tracked pairs kept in step by a drift test. Reactive -- it
   only caught divergence once someone ran the suite, and it only watched the
   file it named (52fdba4 landed ~80 lines of shared review logic outside the
   guard the day after the guard itself landed).
2. Relative symlinks. Divergence became structurally impossible (one file), but
   every checkout with core.symlinks=false -- Git for Windows' default -- broke:
   git materialises a symlink as a *text file holding its target path*, so the
   import/`source` reads that path as source code and the hook dies. install.sh
   declares Windows (msys/cygwin) in scope, so that was a real regression.
3. What this file now pins: .codex/hooks holds no copy at all. Its entry points
   resolve ../../.claude/hooks from their own *physical* location and load the
   single real file from there. Divergence stays impossible, and the checkout
   contains nothing git has to special-case.

Physical resolution is the load-bearing detail. install.sh links
~/.codex/hooks -> <repo>/.codex/hooks, so in production the hooks run from a
path whose parent is $HOME, not the repo: a logical ../../.claude/hooks would
land on ~/.claude/hooks and only resolve because install.sh happens to link
that too. `cd -P` / os.path.realpath() drop that hidden second dependency.

The end-to-end proof that the shell helpers load lives here (through a
symlinked hooks dir, the production shape). For the Python module, the 30+
tests in test_codex_variant_bash_review.py already execute .codex's
bash-review.py with __file__ set to its real path, so the import path is
covered there; what those cannot show -- that resolution survives a symlinked
parent -- is pinned below without running the hook, because the hook's log dir
is a hardcoded /tmp path and this suite never writes outside tmp_path.
"""

import os
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
        # A regular file here is a resurrected duplicate (drift is back); a
        # symlink is design 2 (breaks core.symlinks=false checkouts). Neither is
        # allowed -- the .codex entry points reach the .claude file by path.
        entry = CODEX_HOOKS / name
        assert not os.path.lexists(entry), (
            f"{name}: .codex must not carry its own copy or link; "
            f"entry points resolve ../../.claude/hooks instead"
        )


def test_codex_hooks_tree_carries_no_tracked_symlink():
    """A core.symlinks=false checkout must be byte-identical in this subtree.

    This is the property the previous design lost. Asserting it on the git
    index (not the working tree) is what makes it meaningful: mode 120000 is
    exactly what Git for Windows materialises as a path-bearing text file.
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
