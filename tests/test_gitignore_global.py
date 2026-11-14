"""Tests for .gitignore_global: ordinary files must not be swallowed.

This file is wired in as `core.excludesFile` (see .gitconfig), so every
pattern in it applies to EVERY repository on the machine. A pattern that is
a little too eager therefore does not just hide editor litter: it hides real
source files in unrelated projects, and `git add` skips them with no
warning. Two such over-matches were verified with
`git check-ignore -v --no-index`:

- `tags` (universal-ctags output) also matched any DIRECTORY named `tags`,
  so `app/tags/x.py` vanished from `git status`.
- `[._]*.s[a-v][a-z]` (vim swap files) matched `db/_schema.sql`. The `_`
  prefix is the DOS-only swap-file convention; on every other platform vim
  names the swap `.foo.swp`.

The decision is made by git itself rather than by re-implementing gitignore
matching here, against real files and directories on disk: directory-only
patterns (`!tags/`) depend on the real file type, so a path that does not
exist would be judged as a plain file.
"""

import os
import subprocess

import pytest
from conftest import REPO_ROOT

GITIGNORE_GLOBAL = REPO_ROOT / ".gitignore_global"


@pytest.fixture
def repo(tmp_path):
    """An empty throwaway repository OUTSIDE the checkout.

    Outside, so the checkout's own .gitignore (which carries a `tags` safety
    net too) can never contribute to the verdict.
    """
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(root)],
        check=True,
        capture_output=True,
        env=_git_env(tmp_path),
    )
    return root


def _git_env(tmp_path):
    """The host's git config must not leak into the verdict."""
    return {
        **os.environ,
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }


def _touch(root, rel):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n", encoding="utf-8")
    return path


def _is_ignored(root, rel, tmp_path):
    """True when git says `rel` is ignored under .gitignore_global.

    Exit status only (`-q`), never the `-v` output: with a negated pattern in
    play `-v` still prints the matching `!` rule, so "something was printed"
    does not mean "ignored". `--no-index` judges the path whatever the index
    holds, which is what a fresh `git add` sees.
    """
    res = subprocess.run(
        [
            "git",
            "-c",
            f"core.excludesFile={GITIGNORE_GLOBAL}",
            "check-ignore",
            "-q",
            "--no-index",
            rel,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        env=_git_env(tmp_path),
    )
    assert res.returncode in (0, 1), res.stderr  # 128 = git itself failed
    return res.returncode == 0


def _why(root, rel, tmp_path):
    """The `-v` explanation, for assertion messages only."""
    res = subprocess.run(
        [
            "git",
            "-c",
            f"core.excludesFile={GITIGNORE_GLOBAL}",
            "check-ignore",
            "-v",
            "--no-index",
            rel,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        env=_git_env(tmp_path),
    )
    return res.stdout.strip() or "(no rule matched)"


class TestOrdinaryFilesAreNotIgnored:
    # Each of these is a real file a project can legitimately track.
    @pytest.mark.parametrize(
        "rel",
        [
            # A DIRECTORY named `tags` is ordinary source layout (a CMS tag
            # module, a Django app, ...): only the ctags FILE is litter.
            "app/tags/x.py",
            "tags/index.md",
            "src/blog/tags/__init__.py",
            # `_` is not a vim swap prefix off DOS; SQL/Sass-style partials
            # named `_something.sql` are common.
            "db/_schema.sql",
            "db/_seed.sql",
            # Vector files: `.foo.svg` fits the swap shape `.*.s[a-v][a-z]`,
            # which is what the `!*.svg` exception is for.
            "assets/logo.svg",
            "assets/.hidden-icon.svg",
        ],
    )
    def test_path_is_tracked_normally(self, repo, tmp_path, rel):
        _touch(repo, rel)
        assert not _is_ignored(repo, rel, tmp_path), _why(repo, rel, tmp_path)


class TestRealEditorLitterIsStillIgnored:
    """The fix must narrow the patterns, not drop them."""

    # vim's real swap names: `.<file>.swp`, then `.swo`, `.swn`, ... when
    # earlier ones are taken, and the bare `.swp` for a nameless buffer.
    @pytest.mark.parametrize(
        "rel",
        [
            ".foo.swp",
            ".foo.swo",
            ".foo.swn",
            "src/.main.go.swp",
            "src/.main.go.swo",
            ".swp",
            ".swo",
        ],
    )
    def test_vim_swap_file(self, repo, tmp_path, rel):
        _touch(repo, rel)
        assert _is_ignored(repo, rel, tmp_path), _why(repo, rel, tmp_path)

    # The ctags output FILE stays ignored on purpose (universal-ctags is
    # installed for vim-gutentags / tagbar); only the directory is spared.
    @pytest.mark.parametrize("rel", ["tags", "src/tags"])
    def test_ctags_file(self, repo, tmp_path, rel):
        _touch(repo, rel)
        assert _is_ignored(repo, rel, tmp_path), _why(repo, rel, tmp_path)

    @pytest.mark.parametrize("rel", ["Session.vim", ".netrwhist", ".foo.un~", "x.py~"])
    def test_other_vim_litter(self, repo, tmp_path, rel):
        _touch(repo, rel)
        assert _is_ignored(repo, rel, tmp_path), _why(repo, rel, tmp_path)
