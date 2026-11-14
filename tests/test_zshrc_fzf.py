"""Tests for .zshrc's fzf shell integration (key bindings + completion).

`fzf --zsh` only exists from fzf 0.48. Older builds -- notably the one apt
ships on current Ubuntu LTS -- reject it as an unknown option, so the former
one-liner

    command -v fzf >/dev/null 2>&1 && eval "$(fzf --zsh)"

made EVERY new shell print fzf's usage error and left Ctrl-T / Ctrl-R / Alt-C
unbound. The block must instead branch on the exit status of `fzf --zsh`
itself, eval its output only on success, and otherwise source the integration
files the distro packages ship.

.zshrc cannot be sourced whole here (it needs a real Oh My Zsh install), so
the fzf block is sliced out between two stable neighbours and run on its own
in `zsh -f` with a PATH holding nothing but stub fzf binaries. The host's
real fzf (new enough for --zsh on a dev machine) can therefore never answer
for a stub.
"""

import re
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

ZSHRC = REPO_ROOT / ".zshrc"
ZSH = shutil.which("zsh")

requires_zsh = pytest.mark.skipif(ZSH is None, reason="zsh is not installed")

# Where the distro packages put the integration files that predate --zsh:
# Debian/Ubuntu keep them under the package's doc examples, Arch/Fedora-style
# layouts under /usr/share/fzf.
DEBIAN_DIR = "/usr/share/doc/fzf/examples"
SHARE_DIR = "/usr/share/fzf"


def _fzf_block() -> str:
    """The text between `.zshrc`'s rust PATH line and its `# history` section.

    Sliced by neighbours, not by a marker of the block itself, so the same
    slice works whatever shape the block takes (one-liner or `if ... fi`) and
    a regression shows up as a behavioural failure, not "block not found".
    """
    text = ZSHRC.read_text(encoding="utf-8")
    start_marker = 'export PATH="$HOME/.cargo/bin:$PATH"\n'
    start = text.index(start_marker) + len(start_marker)
    end = text.index("\n# history", start)
    block = text[start:end]
    assert "fzf" in block, f"the slice no longer holds the fzf setup:\n{block}"
    return block


def _write_stub(path, body):
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


# fzf >= 0.48: `--zsh` prints the integration script to stdout.
MODERN_FZF = 'if [ "$1" = "--zsh" ]; then echo "FZF_INIT=from-zsh-flag"; exit 0; fi\n'

# fzf < 0.48: an unknown option is a usage error on stderr, status 2.
OLD_FZF = 'echo "unknown option: $1" >&2\necho "usage: fzf [options]" >&2\nexit 2\n'

# A failing fzf that still wrote something executable to stdout: that output
# must never be eval'd.
FAILING_FZF_WITH_OUTPUT = (
    'echo "FZF_INIT=eval-of-a-failed-fzf"\necho "boom" >&2\nexit 1\n'
)

# Prints one line the tests parse. `${NAME-unset}` distinguishes "never set"
# from "set to empty".
REPORT = (
    'print -r -- "init=${FZF_INIT-unset} kb=${FZF_KB-unset} comp=${FZF_COMP-unset}"'
)


class Zsh:
    """One hermetic `zsh -f` run of the fzf block."""

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.debian = tmp_path / "debian-examples"
        self.share = tmp_path / "share-fzf"

    def fzf(self, body):
        _write_stub(self.bin / "fzf", body)

    def distro_files(self, directory, *names, content=None):
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            var = "FZF_KB" if name.startswith("key-bindings") else "FZF_COMP"
            (directory / name).write_text(
                content
                if content is not None
                else f"{var}=sourced-from-{directory.name}\n",
                encoding="utf-8",
            )

    def block(self):
        # The distro directories are absolute paths on purpose; point them at
        # this test's temp dirs instead. A rename in .zshrc is caught by
        # test_the_block_looks_in_both_distro_directories, not hidden here.
        return (
            _fzf_block()
            .replace(DEBIAN_DIR, str(self.debian))
            .replace(SHARE_DIR, str(self.share))
        )

    def run(self, tail=REPORT, with_block=True):
        script = f"{self.block() if with_block else ''}\n{tail}\n"
        return subprocess.run(
            [ZSH, "-f", "-c", script],
            capture_output=True,
            text=True,
            # PATH is the stub dir ALONE: no host fzf, no host coreutils. The
            # block only needs builtins; the stubs use sh builtins too.
            env={"PATH": str(self.bin), "HOME": str(self.home)},
            timeout=30,
        )


@pytest.fixture
def zsh(tmp_path):
    return Zsh(tmp_path)


def _report(res):
    assert res.returncode == 0, res.stderr
    line = [ln for ln in res.stdout.splitlines() if ln.startswith("init=")]
    assert line, f"the report line never printed:\n{res.stdout}\n{res.stderr}"
    return dict(part.split("=", 1) for part in line[-1].split())


@requires_zsh
class TestModernFzf:
    def test_evals_the_output_of_fzf_zsh(self, zsh):
        zsh.fzf(MODERN_FZF)
        res = zsh.run()
        assert _report(res)["init"] == "from-zsh-flag"
        assert res.stderr == ""

    def test_does_not_also_source_the_distro_files(self, zsh):
        # --zsh output already carries both key bindings and completion;
        # sourcing the distro copies on top would bind everything twice.
        zsh.fzf(MODERN_FZF)
        zsh.distro_files(zsh.debian, "key-bindings.zsh", "completion.zsh")
        zsh.distro_files(zsh.share, "key-bindings.zsh", "completion.zsh")
        got = _report(zsh.run())
        assert got["init"] == "from-zsh-flag"
        assert got["kb"] == "unset" and got["comp"] == "unset"


@requires_zsh
class TestFzfWithoutTheZshFlag:
    def test_an_unknown_option_error_is_not_shown_in_every_shell(self, zsh):
        # The user-visible bug: fzf's own "unknown option: --zsh" at startup.
        zsh.fzf(OLD_FZF)
        res = zsh.run()
        _report(res)
        assert res.stderr == "", res.stderr
        assert "unknown option" not in res.stdout

    def test_output_of_a_failed_fzf_zsh_is_never_evaluated(self, zsh):
        zsh.fzf(FAILING_FZF_WITH_OUTPUT)
        res = zsh.run()
        assert _report(res)["init"] == "unset"
        assert res.stderr == "", res.stderr

    @pytest.mark.parametrize("where", ["debian", "share"])
    def test_falls_back_to_the_distro_integration_files(self, zsh, where):
        zsh.fzf(OLD_FZF)
        zsh.distro_files(getattr(zsh, where), "key-bindings.zsh", "completion.zsh")
        res = zsh.run()
        got = _report(res)
        assert got["init"] == "unset"
        assert got["kb"].startswith("sourced-from-"), got
        assert got["comp"].startswith("sourced-from-"), got
        assert res.stderr == "", res.stderr

    def test_key_bindings_are_enough_when_completion_is_not_shipped(self, zsh):
        zsh.fzf(OLD_FZF)
        zsh.distro_files(zsh.debian, "key-bindings.zsh")
        res = zsh.run()
        got = _report(res)
        assert got["kb"].startswith("sourced-from-"), got
        assert got["comp"] == "unset"
        assert res.stderr == "", res.stderr

    def test_the_debian_directory_wins_over_the_share_directory(self, zsh):
        zsh.fzf(OLD_FZF)
        zsh.distro_files(zsh.debian, "key-bindings.zsh", "completion.zsh")
        zsh.distro_files(zsh.share, "key-bindings.zsh", "completion.zsh")
        got = _report(zsh.run())
        assert got["kb"] == "sourced-from-debian-examples", got
        assert got["comp"] == "sourced-from-debian-examples", got

    def test_nothing_to_source_is_quiet(self, zsh):
        zsh.fzf(OLD_FZF)
        res = zsh.run()
        got = _report(res)
        assert got == {"init": "unset", "kb": "unset", "comp": "unset"}
        assert res.stderr == "", res.stderr

    def test_an_unreadable_distro_file_is_skipped_not_sourced(self, zsh):
        # `-r`, not `-e`: a dangling symlink or a mode-000 file would make
        # `source` print an error in every new shell.
        zsh.fzf(OLD_FZF)
        zsh.debian.mkdir()
        (zsh.debian / "key-bindings.zsh").symlink_to(zsh.debian / "does-not-exist")
        res = zsh.run()
        assert _report(res)["kb"] == "unset"
        assert res.stderr == "", res.stderr


@requires_zsh
class TestFzfAbsent:
    def test_no_fzf_means_no_integration_and_no_noise(self, zsh):
        # Leftover distro files without the binary must not be sourced: the
        # bindings they define would call a command that is not there.
        zsh.distro_files(zsh.debian, "key-bindings.zsh", "completion.zsh")
        res = zsh.run()
        got = _report(res)
        assert got == {"init": "unset", "kb": "unset", "comp": "unset"}
        assert res.stderr == "", res.stderr


@requires_zsh
class TestNoShellStateLeaks:
    """.zshrc is sourced into the interactive shell, so scratch parameters
    would stay visible there for good."""

    # Every parameter name. The stubs only ever set FZF_* names, which are
    # filtered out, so whatever the block adds beyond a block-less run is
    # leakage -- and the check needs no knowledge of the block's own names.
    DUMP = 'print -rl -- "${(@ko)parameters}"'

    def _added_by_the_block(self, zsh):
        def names(res):
            assert res.returncode == 0, res.stderr
            return {n for n in res.stdout.split() if not n.startswith("FZF_")}

        return names(zsh.run(tail=self.DUMP)) - names(
            zsh.run(tail=self.DUMP, with_block=False)
        )

    def test_modern_path_leaves_no_scratch_variables(self, zsh):
        zsh.fzf(MODERN_FZF)
        assert self._added_by_the_block(zsh) == set()

    def test_fallback_path_leaves_no_scratch_variables(self, zsh):
        zsh.fzf(OLD_FZF)
        zsh.distro_files(zsh.debian, "key-bindings.zsh", "completion.zsh")
        assert self._added_by_the_block(zsh) == set()


class TestTheBlockItself:
    @requires_zsh
    def test_the_block_parses(self):
        res = subprocess.run(
            [ZSH, "-n"],
            input=_fzf_block(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert res.returncode == 0, res.stderr

    def test_the_block_looks_in_both_distro_directories(self):
        block = _fzf_block()
        assert DEBIAN_DIR in block, "Debian/Ubuntu's fzf examples dir is not tried"
        assert SHARE_DIR in block, "/usr/share/fzf is not tried"

    def test_no_bare_eval_of_a_command_substitution_remains(self):
        # The old shape, whose failure status the `eval` swallowed. Comments
        # are skipped: the block's own explanation quotes it.
        code = "\n".join(
            ln for ln in _fzf_block().splitlines() if not ln.lstrip().startswith("#")
        )
        assert not re.search(r'eval\s+"\$\(fzf\s+--zsh\)"', code)
