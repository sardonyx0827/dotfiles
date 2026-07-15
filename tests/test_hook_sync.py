"""Structural guard: .codex's shared hook files are symlinks to .claude's.

These modules used to live as byte-identical tracked pairs kept in step by a
drift test. That guard was reactive -- it only caught divergence once someone
ran the suite, and it only watched the one file it named (52fdba4 found ~80
lines of shared review logic that had leaked outside it the day after the
guard landed).

A relative symlink makes divergence structurally impossible instead: there is
only one file. Codex's symlink-skipping bug (openai/codex#3637, #4383, #5040,
#16452) does not apply here -- it affects config Codex's own scanner walks
(skills/, agents/), whereas these files are opened by CPython's import
machinery and by bash's `source`, both of which follow symlinks like any other
file. install.sh draws the same distinction for the hooks dir.

What still needs guarding is each symlink's *shape*, so this pins it: relative
(an absolute link breaks on every other clone path), pointing at the .claude
copy, and actually loadable through the link by whatever will load it.
"""

import subprocess
import sys

import pytest
from conftest import REPO_ROOT

# (relative path under each hooks dir, loader used to prove it resolves)
SHARED_FILES = [
    ("_bash_review_common.py", "python"),
    ("_hook_common.sh", "bash"),
]


def _claude(name: str):
    return REPO_ROOT / ".claude/hooks" / name


def _codex(name: str):
    return REPO_ROOT / ".codex/hooks" / name


@pytest.mark.parametrize("name,loader", SHARED_FILES, ids=[f[0] for f in SHARED_FILES])
class TestSharedFileIsSymlinked:
    def test_claude_side_is_the_real_file(self, name, loader):
        real = _claude(name)
        assert real.is_file()
        assert not real.is_symlink(), f"{name}: the .claude copy must be the real file"

    def test_codex_side_is_a_symlink(self, name, loader):
        # A regular file here means either a checkout with core.symlinks=false
        # or someone re-introducing the duplicate. Both silently resurrect
        # drift, and the core.symlinks case additionally breaks the hook.
        assert _codex(name).is_symlink(), f"{name}: the .codex entry must be a symlink"

    def test_codex_symlink_is_relative(self, name, loader):
        # An absolute target would embed this machine's $DOTFILES_DIR and break
        # for every other clone location.
        target = _codex(name).readlink()
        assert not target.is_absolute(), (
            f"{name}: symlink target must be relative, got {target}"
        )

    def test_codex_symlink_resolves_to_claude_side(self, name, loader):
        assert _codex(name).resolve() == _claude(name).resolve()

    def test_loads_through_the_symlink(self, name, loader):
        # The contract that actually matters. Both hook trees put their own
        # directory on the search path and load these by name, so the link has
        # to be traversable by the loader, not merely present on disk.
        codex_dir = str(_codex(name).parent)
        if loader == "python":
            module = name.removesuffix(".py")
            cmd = [
                sys.executable,
                "-c",
                f"import sys; sys.path.insert(0, sys.argv[1]); import {module}",
                codex_dir,
            ]
        else:
            # Two separate checks. The first sources in a subshell to prove the
            # file is side-effect free -- the Codex lint.sh reads it before
            # `exec 1>/dev/null`, so output at source time would land in its
            # structured-output channel and fail the hook. The second sources
            # into the current shell to prove the functions actually arrive;
            # the subshell above cannot show that, because $() discards them.
            script = f"""
            set -u
            out=$(. "$1/{name}" 2>&1) || {{ echo "source failed" >&2; exit 1; }}
            [ -z "$out" ] || {{ echo "sourcing emitted output: $out" >&2; exit 1; }}
            . "$1/{name}"
            for fn in hook_log hook_notify; do
              declare -F "$fn" >/dev/null || {{ echo "$fn undefined" >&2; exit 1; }}
            done
            """
            cmd = ["bash", "-c", script, "bash", codex_dir]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert result.returncode == 0, (
            f"{name}: loading through the symlink failed: {result.stderr}"
        )
