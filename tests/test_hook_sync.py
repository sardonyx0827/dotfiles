"""Structural guard: .codex's shared hook module is a symlink to .claude's.

The module used to live as two byte-identical tracked files kept in step by a
drift test. That guard was reactive -- it only caught divergence once someone
ran the suite, and it only watched the one file it named (52fdba4 found ~80
lines of shared review logic that had leaked outside it the day after the
guard landed).

A relative symlink makes divergence structurally impossible instead: there is
only one file. Codex's symlink-skipping bug (openai/codex#3637, #4383, #5040,
#16452) does not apply here -- it affects config Codex's own scanner walks
(skills/, agents/), whereas this module is opened by CPython's import
machinery, which follows symlinks like any other file. install.sh draws the
same distinction for the hooks dir.

What still needs guarding is the symlink's *shape*, so this test pins it:
relative (an absolute link breaks on every other clone path), pointing at the
.claude copy, and actually importable through the link.
"""

import subprocess
import sys

from conftest import REPO_ROOT

CLAUDE_COPY = REPO_ROOT / ".claude/hooks/_bash_review_common.py"
CODEX_LINK = REPO_ROOT / ".codex/hooks/_bash_review_common.py"


def test_claude_copy_is_the_real_file():
    assert CLAUDE_COPY.is_file()
    assert not CLAUDE_COPY.is_symlink()


def test_codex_entry_is_a_symlink():
    # A regular file here means either a checkout with core.symlinks=false or
    # someone re-introducing the duplicate. Both silently resurrect drift.
    assert CODEX_LINK.is_symlink()


def test_codex_symlink_is_relative():
    # An absolute target would embed this machine's $DOTFILES_DIR and break for
    # every other clone location.
    target = CODEX_LINK.readlink()
    assert not target.is_absolute(), f"symlink target must be relative, got {target}"


def test_codex_symlink_resolves_to_claude_copy():
    assert CODEX_LINK.resolve() == CLAUDE_COPY.resolve()


def test_module_imports_through_the_symlink():
    # The contract that actually matters: bash-review.py puts its own directory
    # on sys.path and imports the module by name, so the link must be traversable
    # by the import system, not merely present on disk.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]);"
            " import _bash_review_common as m;"
            " print(m.__file__)",
            str(CODEX_LINK.parent),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"import through symlink failed: {result.stderr}"
    assert "_bash_review_common.py" in result.stdout
