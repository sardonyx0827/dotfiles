"""Harness-write detection: directories, the session cwd, symlinks, obfuscation.

Pins four hardenings of `harness_write_ask_reason` in the shared decision module
(.claude/hooks/_bash_review_common.py):

- `~/.local/bin` is protected as a whole directory, not only the `csb` link
- with the session `cwd`, relative destinations, `cd` / `pushd` chains, directory
  targets (`dir/basename(src)`) and symlink aliases are resolved before matching
- a command word that hides its name (`$'…'`, `cs${x}b`, `c\\sb`) is asked for any
  command
- every harness spelling matches case-insensitively (macOS filesystems fold case)

`cwd=None` is the lexical check alone (no filesystem access, purely lexical
matching); the entry points pass the session cwd. Each positive corpus has a
negative twin.
"""

from __future__ import annotations

import os
import posixpath
import sys

import pytest
from conftest import REPO_ROOT, hook_payload

HOOK = ".claude/hooks/bash-review.py"
CODEX_HOOK = ".codex/hooks/bash-review.py"

sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
import _bash_review_common as _common  # noqa: E402
from test_bash_review_gate_hardening import (  # noqa: E402
    HARNESS_DOCKER_CASES,
    HARNESS_DOCKER_NOT_A_WRITE_CASES,
    HARNESS_SED_CASES,
    HARNESS_SED_NOT_A_WRITE_CASES,
    HARNESS_STATE_PATH_CASES,
    HARNESS_STATE_PATH_NOT_A_WRITE_CASES,
)
from test_bash_review_hardening import (  # noqa: E402
    HARNESS_FOREIGN_CODE_CASES,
    HARNESS_NONCANONICAL_NOT_A_WRITE_CASES,
    HARNESS_NONCANONICAL_SPELLING_CASES,
    HARNESS_NOT_A_WRITE_CASES,
    HARNESS_REDIRECT_CASES,
    HARNESS_SHELL_CODE_CASES,
    HARNESS_WRITE_RESIDUAL_CASES,
    HARNESS_WRITER_CASES,
)

OBFUSCATION_PHRASE = "hides the command name"
UNRESOLVED_CD_PHRASE = "to a destination that cannot be resolved"
HARNESS_PHRASE = "writes to the agent harness configuration"


def _ask(command: str, cwd: str | None = None) -> str:
    return _common.harness_write_ask_reason(
        _common._split_commands(command), command, cwd=cwd
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A real HOME with the protected layout, so realpath / lstat see real entries."""
    h = tmp_path / "home"
    for d in (
        ".claude/hooks",
        ".claude/mods",
        ".claude/rules",
        ".claude/agents",
        ".codex",
        ".gemini",
        ".local/bin",
        ".local/share",
        ".local/state/claude-sandbox",
        "proj/sub",
        "proj/stuff",
    ):
        (h / d).mkdir(parents=True)
    (h / ".claude" / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("CDPATH", raising=False)
    return h


# ---------------------------------------------------------------------------
# 1. ~/.local/bin is a protected directory (cwd-independent)
# ---------------------------------------------------------------------------

LOCAL_BIN_CASES = [
    "cp build/csb ~/.local/bin/",
    "cp build/csb ~/.local/bin",
    "cp x ~/.local/bin/cs?",
    "cp x ~/.local/bin/*",
    "cp x ~/.local/bin/tool",
    "cp -t ~/.local/bin a b",
    "cp --target-directory=$HOME/.local/bin a",
    "install -m 755 x ~/.local/bin/",
    "install -Dt ~/.local/bin x",
    "ln -sf /tmp/e ~/.local/bin/tool",
    "rsync -a dist/ ~/.local/bin/",
    "mv x $HOME/.local/bin",
    "echo x > ~/.local/bin/csbx",
    "tee ${HOME}/.local/bin/x",
    "rm ~/.local/bin/ruff",
    "chmod +x ~/.local/bin/x",
    "cp x ~/.local//bin/y",
    "cp x ~/.local/./bin/",
    # an expansion that can land on ~/.local/bin
    "cp x ~/.local/b*/csb",
    "cp x ~/.local/{bin,share}/x",
    "cp x ~/.l*/bin/x",
    "cp x ~/.lo?al/bin/",
    "docker cp web:/x ~/.local/bin/evil",
]

LOCAL_BIN_NOT_A_WRITE_CASES = [
    "ls ~/.local/bin",
    "~/.local/bin/csb ls",
    "~/.local/bin/ruff check .",
    "cat ~/.local/bin/x",
    "cp ~/.local/bin/ruff /tmp/",
    "echo x > ~/.local/share/x",
    "echo x > ~/.local/binx/y",
    "echo x > ~/.localx/bin/y",
    "cp x src/bin/y",
    "cp x ./bin/",
    "cp x node_modules/*/bin/",
    "cp x ~/.local/share/*/y",
]


class TestLocalBinIsAProtectedDirectory:
    @pytest.mark.parametrize("command", LOCAL_BIN_CASES)
    def test_writes_under_local_bin_are_asked(self, command):
        assert HARNESS_PHRASE in _ask(command), command

    @pytest.mark.parametrize("command", LOCAL_BIN_NOT_A_WRITE_CASES)
    def test_reads_runs_and_lookalikes_are_not_asked(self, command):
        assert _ask(command) == "", command

    def test_the_directory_itself_is_a_harness_target(self):
        for path in ("~/.local/bin", "~/.local/bin/", "$HOME/.local/bin/x"):
            assert _common._is_harness_target(path), path


# ---------------------------------------------------------------------------
# 2. destinations resolved against the session cwd
# ---------------------------------------------------------------------------

# (command, cwd relative to HOME) -- asked only once the cwd is known
CWD_ASK_CASES = [
    ("cp x bash-review.py", ".claude/hooks"),
    ("echo x > bash-review.py", ".claude/hooks"),
    ("cp x settings.json", ".claude"),
    ("tee -a CLAUDE.md", ".claude"),
    ("sed -i s/a/b/ settings.json", ".claude"),
    ("rm -rf hooks", ".claude"),
    ("cp x hooks/a.py", ".claude"),
    ("cp x ./hooks/../settings.json", ".claude"),
    ("cp x ../settings.json", ".claude/agents"),
    ("cp x config.toml", ".codex"),
    ("cp x csb", ".local/bin"),
    ("dd if=x of=bash-review.py", ".claude/hooks"),
    ("sed -n 'w settings.json' f", ".claude"),
    ("cp /tmp/x .", ".claude/hooks"),
    ("cp x .", ".local/bin"),
    # cd chains in the same command
    ("cd ~/.local/bin && cp /tmp/x .", "proj"),
    ("cd ~/.claude/hooks && echo x > a.py", "proj"),
    ("cd ~/.claude/hooks; cp x bash-review.py", "proj"),
    ("cd ~/.claude && cd hooks && cp x y", "proj"),
    ("cd ~/.claude\ncp x settings.json", "proj"),
    ("cd ../.claude && cp x settings.json", "proj"),
    ("cd && cd .claude && cp x settings.json", "proj"),
    ("cd ~ && cp x .codex/config.toml", "proj"),
    ("cd $HOME/.claude/hooks && cp x y", "proj"),
    ("cd ${HOME} && cp x .local/bin/z", "proj"),
    ("cd -P ~/.claude && cp x hooks/y", "proj"),
    ("cd -L ~/.claude && cp x hooks/y", "proj"),
    ("cd -- ~/.claude && cp x hooks/y", "proj"),
    ("pushd ~/.claude/hooks && cp x y", "proj"),
    ("pushd ~/.claude/hooks >/dev/null && cp x y && popd", "proj"),
    ("pushd /tmp && popd && cp x bash-review.py", ".claude/hooks"),
    ("builtin cd ~/.claude/hooks && cp x y", "proj"),
    ("command cd ~/.claude/hooks && cp x y", "proj"),
    ("'cd' ~/.claude/hooks && cp x y", "proj"),
    ("chdir ~/.claude/hooks && cp x y", "proj"),
    ("if cd ~/.claude/hooks; then cp x y; fi", "proj"),
    ("cd ~/.claude/hooks || exit 1; cp x y", "proj"),
    ("cd /nonexistent || cd ~/.claude/hooks && cp x y", "proj"),
    ("cd ~/.claude/hooks && ls && cp x y", "proj"),
    ("cd ~/.claude/hooks && ls || cp x y", ".claude"),
    ("cd /tmp; cp x settings.json", ".claude"),  # a failed cd leaves the cwd as is
    ("true | cd ~/.claude/hooks && cp x y", "proj"),  # zsh runs the last stage here
    ("! cd /tmp && cp x settings.json", ".claude"),
    ("cd ~/.claude/hooks & cp x settings.json", ".claude"),
    # subshells see the cd made inside them
    ("(cd ~/.claude/hooks; cp x bash-review.py)", "proj"),
    ("(cd ~/.claude/hooks && cp x y) && ls", "proj"),
    ("cd /tmp && (cd ~/.claude/hooks && cp x y)", "proj"),
    # the cwd spelled as $PWD / $(pwd)
    ('echo x > "$PWD/settings.json"', ".claude"),
    ("echo x > $(pwd)/hooks/x", ".claude"),
    ("cp x ${PWD}/bash-review.py", ".claude/hooks"),
    # shell code and substitutions run in the cwd they are reached in
    ("bash -c 'cd ~/.claude/hooks && cp x y'", "proj"),
    ("sh -c 'cp x bash-review.py'", ".claude/hooks"),
    ("cd ~/.claude/hooks && bash -c 'cp x y'", "proj"),
    ('echo "$(cp x bash-review.py)"', ".claude/hooks"),
    ("echo $(cd ~/.claude/hooks && cp x y)", "proj"),
    ("echo `cp x bash-review.py`", ".claude/hooks"),
    ("diff <(cp x bash-review.py) y", ".claude/hooks"),
    ('git commit -m "$(cp x \\"bash-review.py\\")"', ".claude/hooks"),
    # a directory destination receives dir/basename(src)
    ("cp -r /tmp/stage/.codex ~", "proj"),
    ("cp -r /tmp/stage/.claude ~/", "proj"),
    ("rsync -a /tmp/stage/.claude ~/", "proj"),
    ("cp -r /tmp/stage/.claude stuff", "proj"),
    ("cp -r /tmp/stage/.claude /tmp/x stuff", "proj"),
    ("mv /tmp/stage/.claude ~/proj/stuff", "proj"),
    ("cp -t stuff /tmp/stage/.claude", "proj"),
    ("ln -s /tmp/evil/settings.json", ".claude"),
    ("ln -s /tmp/evil/settings.json .", ".claude"),
    ("install /tmp/stage/csb ~/proj/../.local/bin", "proj"),
    # a symlink alias made earlier in the same command
    ("ln -s ~/.claude/hooks h && cp x h/bash-review.py", "proj"),
    ("ln -s ~/.claude/hooks h; cd h && cp x y", "proj"),
    ("ln -sf ~/.claude h && echo x > h/settings.json", "proj"),
    ("ln -s ../.claude/hooks sub/h && cp x sub/h/y", "proj"),
    ("ln -s ~/.claude/hooks /tmp/h && cp x /tmp/h/y", "proj"),
    ("ln ~/.claude/settings.json s && echo x > s", "proj"),
    ("ln -s ~/.claude /tmp/a && ln -s /tmp/a/hooks h && cp x h/y", "proj"),
    ("ln -s -t sub ~/.claude/hooks && cp x sub/hooks/y", "proj"),
    ("ln -s ~/.claude/hooks sub/ && cp x sub/hooks/y", "proj"),
    ("ln -s ~/.claude/hooks H && cp x h/y", "proj"),  # case-insensitive filesystems
    # a link to a protected path is asked at the link, written through or not
    ("ln -s ~/.claude/hooks h", "proj"),
    ("ln -s ~/.claude/hooks h && cat h/bash-review.py", "proj"),
    ("ln -s ~/.claude/hooks h && cp x hx/y", "proj"),
    # a variable assigned a literal earlier in the same command
    ("S=~/.claude/hooks; cd $S && cp x y", "proj"),
    ('S="$HOME/.claude/hooks"; cd "$S"; cp x y', "proj"),
    ('S=~/.claude/hooks; cp x "$S/bash-review.py"', "proj"),
    ("S=~/.claude; D=$S/hooks; cd ${D} && cp x y", "proj"),
    ("export S=~/.claude/hooks; cd $S && cp x y", "proj"),
    ("S=~/.claude/hooks; export S; cd $S && cp x y", "proj"),
    ("S=/tmp; true || S=~/.claude/hooks; cd $S && cp x y", "proj"),
    ("S=/tmp\nif true; then S=~/.claude/hooks; fi\ncd $S && cp x y", "proj"),
    ("S=/tmp/stage/.claude; cp -r $S ~/", "proj"),
    ("S=~/.claude/hooks; ln -s $S h && cp x h/y", "proj"),
    ("A=/tmp B=~/.claude/hooks; cd $B && cp x y", "proj"),
    # bash sends an unset `cd $S` to HOME, so a backgrounded assignment does not hide it
    ("S=/tmp & cd ~ && cp x .codex/config.toml", "proj"),
    # more spellings of the same forms
    ("cp -r /tmp/stage/.claude ..", "proj"),
    ("echo x &>> bash-review.py", ".claude/hooks"),
    ("cat <> bash-review.py", ".claude/hooks"),
    ('cd "$PWD/hooks" && cp x y', ".claude"),
    ("pushd ~/.claude/hooks > /dev/null && cp x y", "proj"),
    ("ln ../.claude/settings.json s && echo x > s", "proj"),
    ("bash <<'EOF'\ncd ~/.claude/hooks\ncp x y\nEOF", "proj"),
    ("cp x 'unterminated", ".claude/hooks"),
]

# (command, cwd relative to HOME) -- not asked even with the cwd known
CWD_NOT_A_WRITE_CASES = [
    ("cp x bash-review.py", "proj"),
    ("cp .claude/../x y", "proj"),
    ("cat bash-review.py", ".claude/hooks"),
    ("cp settings.json /tmp/bk", ".claude"),
    ("echo x > ../agents/x.md", ".claude/hooks"),
    ("cp x agents/y.md", ".claude"),
    ("echo x 2>&1", ".claude/hooks"),
    ("echo x >&2", ".claude/hooks"),
    ("echo x > /dev/null", ".claude/hooks"),
    ("echo x 2>/dev/null >&-", ".claude/hooks"),
    ("cd ~/.claude/hooks && cat x > /tmp/y", "proj"),
    ("cd /tmp && cp x settings.json", ".claude"),  # cp runs only once cd succeeded
    ("pushd /tmp && cp x settings.json", ".claude"),
    ("cd ~/.claude/hooks && cd /tmp && cp x y", "proj"),
    # a subshell's cd does not leak out of it
    ("(cd ~/.claude/hooks; ls); cp x bash-review.py", "proj"),
    ("(cd ~/.claude/hooks && ls) && cp x bash-review.py", "proj"),
    ("x=$(cd ~/.claude/hooks; pwd); cp a bash-review.py", "proj"),
    ('echo "$(cd ~/.claude/hooks; pwd)"; cp a bash-review.py', "proj"),
    ("bash -c 'cd ~/.claude/hooks'; cp a bash-review.py", "proj"),
    ("(cd /tmp; ls); cp x y", "proj"),
    # dynamic cd that cannot reach a later relative write
    ('(cd "$X" && make); echo x > out.txt', "proj"),
    ('cd "$X" && cp a /tmp/b', "proj"),
    ('cd "$X" && ls', "proj"),
    ('cd "$X"; echo hi > /dev/null', "proj"),
    ('cd "$X" && make 2>&1 | head', "proj"),
    ('cp a b && cd "$X"', "proj"),
    # a directory destination with a source name that is not protected
    ("cp -r /tmp/stage/src stuff", "proj"),
    ("cp /tmp/x/settings.json /tmp/bk/", "proj"),
    ("cp -r ~/.claude /tmp/bk-claude", "proj"),  # /tmp/bk-claude does not exist
    ("cp -r /tmp/stage/.claude newname", "proj"),  # newname does not exist
    ("rsync -a /tmp/stage/ ~/", "proj"),
    # an alias nobody writes through is fine
    ("ln -s /tmp/a h && cp x h/y", "proj"),
    # heredoc bodies and commit messages are data
    ("git commit -F - <<'EOF'\ncd ~/.claude/hooks && cp x y\nEOF", "proj"),
    ("cat > notes.md <<'EOF'\ncp x bash-review.py\nEOF", "proj"),
    ('git commit -m "cd ~/.claude/hooks\ncp x y"', "proj"),
    ("cp x y # cd ~/.claude/hooks", "proj"),
    # case arms do not open or close a scope
    ("case $a in x) cd ~/.claude;; esac; cp a b", "/"),
    ("f() { ls; }; cp a b", "proj"),
    ("ls @(a|b); cp a b", "proj"),
    ("arr=(cd /x); cp a b", "proj"),
    ("echo $((1 + 2)) > out", "proj"),
    ("echo ${x:-a)} > out", "proj"),
    # a literal variable keeps the cd resolvable
    ('S=/tmp/work; cd "$S" && echo x > out', "proj"),
    ("S=/tmp/w; mkdir -p $S && cd $S && cp a b", "proj"),
    ("SP=/tmp/a; cd $SP; printf x > f", "proj"),
    ("S=/tmp; D=$S/x; cd $D && cp a b", "proj"),
    ("S=/tmp; cd $S && echo x > ${S}/y", "proj"),
    ("S=/tmp/x; (S=~/.claude/hooks); cd $S && cp a b", "proj"),
    ("S=/tmp; x=$(S=~/.claude/hooks; echo); cd $S && cp a b", "proj"),
    ("S=/tmp; S=/var/tmp; cd $S && cp a b", "proj"),
    ("S=/tmp\nwhile x; do S=/var/tmp; done\ncd $S && cp a b", "proj"),
    ("S=/a; T=$S$S; cd $T && cp a b", "proj"),
    # code in a heredoc body does not redefine cd / HOME for the shell
    ("cd /tmp && cat > t.js <<'EOF'\nconst HOME = 1; f();\nEOF", "proj"),
    ("cd ~/proj && cat > a.py <<'EOF'\ndef f(): s.strip()\nPWD=1\nEOF", "proj"),
    ("cd ~ && python3 - <<'EOF'\nalias x\nEOF", "proj"),
    ('python3 -c "f()"; cd /tmp && cp a b', "proj"),
    ("echo 'function x'; cd /tmp && cp a b", "proj"),
    # a heredoc body that merely mentions `| sh` is still data
    ("cat > t.txt <<'EOF'\ncd \"$X\" && cp a b\necho x | sh\nEOF", "proj"),
    # link cycles and self-nesting links stay bounded
    ("ln -s /tmp/b /tmp/a && ln -s /tmp/a /tmp/b && cp x /tmp/a/y", "proj"),
    ("ln -s ~other/x h && cp a h/y", "proj"),
    ("cd /tmp; \\\n; cp a b", "proj"),
    ("echo x >", "proj"),
    ("echo x > >(cat)", "proj"),
    ("bash <<'EOF'\nls # ; cd ~/.claude/hooks\ncp x y\nEOF", "proj"),
    ("diff <(ls x", "proj"),
]

# a cd whose target is not known, followed by a relative write
UNRESOLVED_CD_CASES = [
    'cd "$X" && cp a b',
    'cd "$X"; echo x > out.txt',
    "cd $(mktemp -d) && echo x > out",
    "cd `git rev-parse --show-toplevel` && cp a b",
    "cd - && cp a b",
    "cd ~other && cp a b",
    "cd /tmp/* && cp a b",
    "cd {a,b} && cp a b",
    "cd a b && cp x y",  # zsh: cd OLD NEW substitutes in $PWD
    "cd -q x && cp a b",
    "pushd && cp a b",
    "pushd +1 && cp a b",
    "pushd -n /tmp && cp a b",
    "popd && cp a b",
    "popd +1 && cp a b",
    "CDPATH=~/.claude cd hooks && cp x y",
    "export CDPATH=~/.claude; cd hooks; cp x y",
    "HOME=/tmp/h; cd && cp x y",
    "HOME=/tmp/h; cd ~ && cp x y",
    "for d in a b; do cd $d && cp x y; done",
    'cd "$X" && echo x > "$PWD/y"',
    'cd "$X" && sh -c "cp a b"',
    'cd "$X" && echo "$(cp a b)"',
    'cd "$X" && ln -s /tmp/x y',
    # variables that are not a known literal
    "S=$(mktemp -d); cd $S && cp a b",
    "D=$(mktemp -d) && cd $D && cp a b",
    "true && S=/tmp; cd $S && cp a b",
    "S=/tmp; if x; then S=$Y; fi; cd $S && cp a b",
    "S=/tmp; read S; cd $S && cp a b",
    "S=/tmp; read -r S < f; cd $S && cp a b",
    "S=/tmp; unset S; cd $S && cp a b",
    'S=/tmp; eval "S=/x"; cd $S && cp a b',
    "S=/tmp; source ./env.sh; cd $S && cp a b",
    "S=/tmp; for S in a b; do :; done; cd $S && cp a b",
    "S=/tmp; S+=/x; cd $S && cp a b",
    "S=/tmp; S=$S$X; cd $S && cp a b",
    "S=/tmp; local S; cd $S && cp a b",
    "S=/tmp; declare -n S=T; cd $S && cp a b",
    "S=/tmp; printf -v S x; cd $S && cp a b",
    "S=/tmp; $X S=/y; cd $S && cp a b",
    "S=/tmp; getopts ab S; cd $S && cp a b",
    "S=/tmp; mapfile S < f; cd $S && cp a b",
    "cd $S && cp a b",
    "S=/tmp; cd ${S:-/x} && cp a b",
    "S=/a; true || S=/b; T=$S$S$S$S; cd $T && cp a b",  # too many combinations
    "S='$x'; cd $S && cp a b",
    "HOME=/tmp/h; S=~/x; cd $S && cp a b",
    # cd / pwd redefined in the same command
    "cd() { builtin cd ~/.claude/hooks; }; cd /tmp && cp x bash-review.py",
    "function pwd { echo /x; }; cp x $(pwd)/y",
    "alias cd=pushd; cd /tmp && cp a b",
    'PWD=/tmp/x; echo x > "$PWD/y"',
    "PWD=/tmp/x; cd $PWD && cp a b",
    "HOME=/tmp/h; echo x > ~/y",
    # an assignment in a background job or a pipeline stage runs in a subshell
    "S=/tmp & cd $S && cp a b",
    "S=/tmp | cat; cd $S && cp a b",
    "export S=/tmp & cd $S && cp a b",
    "S=/tmp |& cat; cd $S && cp a b",
    'cd "$X"; S=out; echo x > $S/y',
]


class TestCwdIsResolved:
    @pytest.mark.parametrize("command,where", CWD_ASK_CASES)
    def test_relative_and_chained_destinations_are_asked(self, home, command, where):
        cwd = str(home / where)
        assert HARNESS_PHRASE in _ask(command, cwd=cwd), (command, where)

    @pytest.mark.parametrize("command,where", CWD_NOT_A_WRITE_CASES)
    def test_unprotected_or_scoped_destinations_are_not_asked(
        self, home, command, where
    ):
        cwd = where if where.startswith("/") else str(home / where)
        assert _ask(command, cwd=cwd) == "", (command, where)

    @pytest.mark.parametrize("command", UNRESOLVED_CD_CASES)
    def test_relative_write_after_unresolvable_cd_is_asked(self, home, command):
        reason = _ask(command, cwd=str(home / "proj"))
        assert UNRESOLVED_CD_PHRASE in reason, command
        assert reason.endswith("— confirm manually")

    def test_cdpath_in_the_hook_environment_makes_relative_cd_unresolvable(
        self, home, monkeypatch
    ):
        monkeypatch.setenv("CDPATH", str(home / ".claude"))
        assert UNRESOLVED_CD_PHRASE in _ask("cd hooks && cp x y", str(home / "proj"))
        # `./x` and `../x` never consult CDPATH, absolute names neither
        assert _ask("cd ./sub && cp x y", str(home / "proj")) == ""
        assert _ask("cd /tmp && cp x y", str(home / "proj")) == ""

    def test_without_a_home_tilde_and_bare_cd_are_unresolvable(self, home, monkeypatch):
        monkeypatch.setenv("HOME", "")
        assert UNRESOLVED_CD_PHRASE in _ask("cd && cp x y", str(home / "proj"))
        assert UNRESOLVED_CD_PHRASE in _ask("cd ~/x && cp a b", str(home / "proj"))

    @pytest.mark.parametrize("cwd", ["", "relative/dir", "~/proj"])
    def test_an_unusable_cwd_is_treated_as_unknown(self, home, cwd):
        assert UNRESOLVED_CD_PHRASE in _ask("cp x y", cwd)
        assert _ask("cp x /tmp/y", cwd) == ""
        assert _ask("ls", cwd) == ""

    @pytest.mark.parametrize("command", HARNESS_WRITE_RESIDUAL_CASES[:1])
    def test_the_cd_residual_is_closed_once_the_cwd_is_known(self, home, command):
        # pinned as a residual for cwd=None (the README's known gap)
        assert _ask(command) == ""
        assert _ask(command, cwd=str(home)) != ""

    @pytest.mark.parametrize(
        "command",
        [
            "cp x bash-review.py",
            "cp x h/bash-review.py",
            "cd ~/.local/bin && cp /tmp/x .",
            "cd ~/.claude && cd hooks && cp x y",
            "cp -r /tmp/stage/.codex ~",
            'cd "$X" && cp a b',
            'echo x > "$PWD/hooks/x"',
        ],
    )
    def test_none_keeps_the_lexical_behaviour(self, home, command):
        assert _ask(command) == "", command

    def test_none_never_touches_the_filesystem(self, home, monkeypatch):
        def boom(*_a, **_k):
            raise AssertionError("filesystem access with cwd=None")

        monkeypatch.setattr(_common.os.path, "realpath", boom)
        monkeypatch.setattr(_common.os, "lstat", boom)
        for command in ("cp x h/bash-review.py", "cd ~/.claude && cp x y", "ls"):
            _ask(command)

    def test_the_lexical_verdict_is_kept_when_a_cwd_is_given(self, monkeypatch):
        # the gate-hardening corpora are written against this HOME
        monkeypatch.setenv("HOME", "/Users/someone")
        corpora = (
            HARNESS_REDIRECT_CASES
            + HARNESS_WRITER_CASES
            + HARNESS_SHELL_CODE_CASES
            + HARNESS_NONCANONICAL_SPELLING_CASES
            + HARNESS_DOCKER_CASES
            + HARNESS_SED_CASES
            + HARNESS_STATE_PATH_CASES
            + LOCAL_BIN_CASES
        )
        for command in corpora:
            assert _ask(command) != "", command
            for cwd in ("/", "/Users/someone", "/Users/someone/.claude"):
                assert _ask(command, cwd=cwd) != "", (command, cwd)

    def test_negative_corpora_stay_quiet_in_a_neutral_cwd(self, home):
        corpora = (
            HARNESS_NOT_A_WRITE_CASES
            + HARNESS_NONCANONICAL_NOT_A_WRITE_CASES
            + HARNESS_FOREIGN_CODE_CASES
            + HARNESS_DOCKER_NOT_A_WRITE_CASES
            + HARNESS_SED_NOT_A_WRITE_CASES
            + HARNESS_STATE_PATH_NOT_A_WRITE_CASES
            + LOCAL_BIN_NOT_A_WRITE_CASES
        )
        for command in corpora:
            assert _ask(command, cwd=str(home / "proj")) == "", command


class TestSymlinkAliasesAreResolved:
    def test_an_alias_made_earlier_is_followed(self, home, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        (work / "h").symlink_to(home / ".claude" / "hooks")
        assert _ask("cp x h/bash-review.py") == ""  # lexical only
        assert HARNESS_PHRASE in _ask("cp x h/bash-review.py", cwd=str(work))
        assert HARNESS_PHRASE in _ask(f"cp x {work}/h/bash-review.py", cwd="/")
        assert HARNESS_PHRASE in _ask("cd h && cp x y", cwd=str(work))
        assert HARNESS_PHRASE in _ask("cp x h", cwd=str(work))
        assert _ask("cp h/bash-review.py /tmp/x", cwd=str(work)) == ""

    def test_a_symlinked_cwd_is_resolved(self, home, tmp_path):
        (tmp_path / "hk").symlink_to(home / ".claude" / "hooks")
        assert HARNESS_PHRASE in _ask("cp x y", cwd=str(tmp_path / "hk"))

    def test_a_symlinked_directory_destination_takes_the_source_name(
        self, home, tmp_path
    ):
        (tmp_path / "hm").symlink_to(home)
        cmd = f"cp -r /tmp/stage/.codex {tmp_path}/hm"
        assert HARNESS_PHRASE in _ask(cmd, cwd="/")

    def test_a_protected_root_that_is_itself_a_link_is_followed(self, home, tmp_path):
        # ~/.codex -> elsewhere/codex-real: the resolved spelling no longer says
        # `.codex`, so the resolved roots decide.
        real = tmp_path / "elsewhere" / "codex-real"
        real.mkdir(parents=True)
        (home / ".codex").rmdir()
        (home / ".codex").symlink_to(real)
        (tmp_path / "c").symlink_to(home / ".codex")
        assert HARNESS_PHRASE in _ask("cp x c/config.toml", cwd=str(tmp_path))
        assert HARNESS_PHRASE in _ask(f"cp x {real}/config.toml", cwd="/")

    def test_a_symlinked_home_is_followed(self, home, tmp_path, monkeypatch):
        link = tmp_path / "homelink"
        link.symlink_to(home)
        monkeypatch.setenv("HOME", str(link))
        alias = tmp_path / "a"
        alias.symlink_to(home / ".codex")
        assert HARNESS_PHRASE in _ask("cp x a/config.toml", cwd=str(tmp_path))
        assert HARNESS_PHRASE in _ask(f"cp x {home}/.codex/x", cwd="/")

    def test_too_many_possible_cwds_stop_the_resolution(self, home):
        cds = "; ".join(f"cd /tmp/d{i}" for i in range(_common._CWD_SET_LIMIT + 1))
        assert UNRESOLVED_CD_PHRASE in _ask(cds + "; cp a b", cwd=str(home / "proj"))
        assert _ask(cds + "; cp a /tmp/b", cwd=str(home / "proj")) == ""

    def test_too_many_links_stop_the_resolution(self, home):
        links = "; ".join(
            f"ln -s /tmp/a{i} /tmp/l{i}" for i in range(_common._ALIAS_LIMIT + 1)
        )
        assert "could not be resolved" in _ask(links, cwd=str(home / "proj"))

    def test_a_self_nesting_link_runs_out_of_spellings_and_asks(self, home):
        # /tmp/a -> /tmp/a/b keeps growing: the spellings run out, which asks
        # instead of dropping the rest
        cmd = "ln -s /tmp/a/b /tmp/a && cp x /tmp/a/y"
        assert "could not be resolved" in _ask(cmd, cwd=str(home / "proj"))

    def test_a_link_loop_does_not_raise(self, home, tmp_path):
        (tmp_path / "l1").symlink_to(tmp_path / "l2")
        (tmp_path / "l2").symlink_to(tmp_path / "l1")
        assert _ask("cp x l1/y", cwd=str(tmp_path)) == ""

    @pytest.mark.parametrize("error", [OSError("boom"), ValueError("nul")])
    def test_a_resolution_error_is_asked_not_raised(self, home, monkeypatch, error):
        def broken(_path):
            raise error

        monkeypatch.setattr(_common.os.path, "realpath", broken)
        reason = _ask("cp x y", cwd=str(home / "proj"))
        assert "could not be resolved" in reason
        assert reason.endswith("— confirm manually")

    def test_an_unreadable_directory_check_counts_as_a_directory(
        self, home, monkeypatch
    ):
        real_lstat = os.lstat

        def flaky(path, *a, **k):
            if str(path).endswith("/proj/odd"):
                raise PermissionError("denied")
            return real_lstat(path, *a, **k)

        monkeypatch.setattr(_common.os, "lstat", flaky)
        cmd = "cp -r /tmp/stage/.claude odd"
        assert HARNESS_PHRASE in _ask(cmd, cwd=str(home / "proj"))

    def test_resolved_roots_skip_without_a_home(self, home, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", "")
        (tmp_path / "h").symlink_to(home / ".claude" / "hooks")
        # the resolved spelling still names .claude/hooks
        assert HARNESS_PHRASE in _ask("cp x h/y", cwd=str(tmp_path))
        assert _ask("cp x /tmp/y", cwd=str(tmp_path)) == ""


# ---------------------------------------------------------------------------
# 3. a command word that hides its name (any command, cwd-independent)
# ---------------------------------------------------------------------------

OBFUSCATED_COMMAND_CASES = [
    # an expansion with an operator can make any name
    '"${PY:-python3}" -V',
    "${PYTHON:-python3} -V",
    "$'csb' ls",
    "$'\\x63sb' ls",
    "c$'s'b ls",
    "cs${x}b ls",
    "c$@sb ls",
    "c$1sb ls",
    "py$V x.py",
    "python${V} -c 1",
    "c$(echo s)b ls",
    "c`echo s`b ls",
    'c"$x"sb ls',
    "${x}sb ls",
    "$x.sh",
    "./x$y",
    "/usr/bin/c${x}url x",
    "c\\sb ls",
    "\\csb ls",
    "\\rm -f /tmp/x",  # the alias-bypass idiom is counted separately in FRICTION
    "cs\\b ls",
    "X=1 c\\sb ls",
    "env c\\sb ls",
    "timeout 5 cs${x}b ls",
    "nohup python${V} x &",
    "(c\\sb ls)",
    "{ c\\sb ls; }",
    "echo hi | c\\sb",
    "true && cs${x}b",
    "if c\\sb; then :; fi",
    "echo $(c\\sb ls)",
    'echo "$(c\\sb ls)"',
    "sh -c 'c\\sb ls'",
    ">out c\\sb ls",
    "2>/dev/null c\\sb ls",
]

PLAIN_COMMAND_CASES = [
    "$PY -m pytest",
    '"$PY" -m pytest',
    "${PY} x.py",
    "$1",
    "$@",
    '"$HOME/bin/x"',
    "$HOME/.local/bin/csb ls",
    "${HOME}/.local/bin/csb ls",
    "$VENV/bin/python -m pip list",
    '"${VENV}/bin/python" -m x',
    "$(which python3) -V",
    '"$(dirname "$0")/run.sh"',
    "$(git rev-parse --show-toplevel)/scripts/x.sh",
    '"$DIR"/run.sh',
    "./node_modules/.bin/eslint .",
    "~/bin/$TOOL",
    "$a$b",
    "${a}${b}3",
    # expansions and escapes in arguments, assignments and flags are not command words
    "echo $'a\\tb'",
    "git log --format=$'%h\\t%s'",
    "printf 'c\\sb'",
    'printf "c\\sb"',
    "grep -E 'a\\sb' f",
    "X=$'\\n' cmd",
    "X=c\\sb cmd",
    "ls ${dir}x",
    "cmd --flag=${x}y",
    "echo ${x}y",
    "echo a\\ b",
    "find . -name '*.py' -exec grep -l x {} \\;",
    "grep a\\|b f",
    "command -v c\\sb",
    "type -a c\\sb",
    "echo \\$HOME",
    "sed 's/\\s//' f",
    "cmd \\-x",
    "cmd 'a b' \\.",
    # quoted command words without expansions
    "'csb' ls",
    '"csb" ls',
    # heredoc bodies and commit messages are data
    "git commit -F - <<'EOF'\nc\\sb ls\nEOF",
    "cat > n.md <<'EOF'\n$'x' y\nEOF",
    'git commit -m "x\n\\rm y"',
    # code lines of another language in a command the live-text scan cannot parse
    # (`((` / an unterminated quote): heredoc bodies and multi-line quotes are data
    "python3 - <<'EOF'\nprint((1))\nx = f\"node_$V.x\"\nEOF",
    "cat > t.js <<'EOF'\nexpect((a)).toBe(`${S}x`)\nEOF",
    "python3 -c '\nprint((1))\n\"node_$V.x\": (\n'",
    "node -e '\nfoo((1))\n`${A}b`\n'",
    "cat > t.txt <<'EOF'\nc\\sb x\necho y | sh\nEOF",
    "cat > t.txt <<'EOF'\nsource x\nc\\sb y\nEOF\ncat <(ls)",
]


class TestObfuscatedCommandWordsAreAsked:
    @pytest.mark.parametrize("command", OBFUSCATED_COMMAND_CASES)
    def test_hidden_command_names_are_asked(self, command):
        reason = _ask(command)
        assert OBFUSCATION_PHRASE in reason, command
        assert HARNESS_PHRASE not in reason
        assert reason.endswith("— confirm manually")

    @pytest.mark.parametrize("command", PLAIN_COMMAND_CASES)
    def test_plain_expansions_and_arguments_are_not_asked(self, command):
        assert _ask(command) == "", command

    def test_reason_is_fixed_vocabulary(self):
        reason = _ask("c\\sb ghp_notarealtoken")
        assert "ghp_" not in reason and "c\\sb" not in reason

    def test_shell_code_in_a_sourced_heredoc_is_still_checked(self):
        assert OBFUSCATION_PHRASE in _ask("bash <<'EOF'\nc\\sb ls\nEOF")
        assert OBFUSCATION_PHRASE in _ask("bash -c 'echo hi\nc\\sb ls'")
        assert OBFUSCATION_PHRASE in _ask("echo $((1+2)); c\\sb ls")
        # inside double quotes a backtick is still a command substitution
        assert OBFUSCATION_PHRASE in _ask('node -e "\nfoo((1))\n`${A}b`\n"')

    @pytest.mark.parametrize(
        "text",
        ['"$(ls', "${x:-'a}", '"abc', "${a", "`ls", "$(ls"],
    )
    def test_unterminated_spans_are_reported(self, text):
        assert _common._span_at(text, 0) == (-1, [])

    def test_nested_parameter_expansions_are_one_span(self):
        assert _common._span_at("${a:-${b}}x", 0) == (len("${a:-${b}}"), [])
        assert _common._span_at("${a:-{b}}x", 0) == (len("${a:-{b}}"), [])
        assert _common._span_at('"a$(b)"', 0) == (len('"a$(b)"'), ["b"])

    def test_an_unterminated_word_is_judged_on_its_prefix(self):
        assert OBFUSCATION_PHRASE in _ask("c\\sb 'unterminated")
        assert _ask("ls 'unterminated") == ""

    def test_claude_entry_asks_without_a_model(self, run_hook):
        res = run_hook(HOOK, hook_payload("c\\sb ls"))
        assert res.decision == "ask"
        assert OBFUSCATION_PHRASE in res.reason
        assert "hook error" not in res.reason

    def test_codex_entry_blocks_without_a_model(self, run_hook):
        res = run_hook(CODEX_HOOK, hook_payload("cs${x}b ls"))
        assert res.exit_code == 2
        assert OBFUSCATION_PHRASE in res.stderr
        assert "hook error" not in res.stderr


# ---------------------------------------------------------------------------
# 4. case-insensitive harness spellings
# ---------------------------------------------------------------------------

CASE_FOLDED_CASES = [
    "echo x > ~/.CLAUDE/Settings.json",
    "echo x > ~/.Claude/SETTINGS.LOCAL.JSON",
    "echo x > .claude/claude.MD",
    "cp x ~/.claude/HOOKS/x",
    "cp x .CLAUDE/Mods/x",
    "cp x .claude/RULES/x.md",
    "cp x ~/.Codex/config.toml",
    "cp x $HOME/.CODEX/config.toml",
    "cp x .codex/Hooks/x",
    "cp x .Codex/agents.MD",
    "tee ~/.Gemini/gemini.MD",
    "cp x ~/.CLAUDE",
    "cp x ~/.Local/Bin/anything",
    "cp x ~/.LOCAL/BIN/",
    "cp x ~/.Local/State/Claude-Sandbox/x",
    "cp x ~/.CL*/SETTINGS.JSON",
    "cp x ~/.LOCAL/B*/x",
    "cp x ~/.claude/hooKs/x",  # KELVIN SIGN folds to k
    "cp x ~/.claude/ſettings.json",  # LONG S folds to s
    "docker run -v ~/.LOCAL/bin:/x img",
]


class TestHarnessSpellingsFoldCase:
    @pytest.mark.parametrize("command", CASE_FOLDED_CASES)
    def test_case_variants_are_asked(self, command):
        assert HARNESS_PHRASE in _ask(command), command

    def test_absolute_home_in_another_case(self, monkeypatch):
        monkeypatch.setenv("HOME", "/Users/someone")
        assert _ask("cp x /USERS/SOMEONE/.CODEX/x") != ""

    @pytest.mark.parametrize(
        "command,where",
        [
            ("cp x SETTINGS.JSON", ".CLAUDE"),
            ("cp x y", ".Claude/Hooks"),
            ("cp x Csb", ".LOCAL/bin"),
            ("cd ~/.CLAUDE/HOOKS && cp x y", "proj"),
            ("cd ~/.Codex && cp x y", "proj"),
        ],
    )
    def test_case_variants_through_the_cwd_are_asked(self, home, command, where):
        assert HARNESS_PHRASE in _ask(command, cwd=str(home / where)), command

    def test_resolved_roots_fold_case(self, home, tmp_path):
        # The check compares spellings case-folded always, and resolves a path
        # through the file system (ctx.resolve). So `a/config.toml` reaches
        # ~/.codex through the alias `A` only where the file system folds case
        # (macOS's default APFS): there `a` is the same entry as `A`. Where it
        # does not (Linux), `a` is another, missing name, and the write lands
        # there, outside the harness.
        alias = tmp_path / "A"
        alias.symlink_to(home / ".codex")
        folds_case = (tmp_path / "a").exists()
        reason = _ask("cp x a/config.toml", cwd=str(tmp_path))
        if folds_case:
            assert HARNESS_PHRASE in reason
        else:
            assert reason == ""
        # through the alias's own spelling it resolves into ~/.codex everywhere
        assert HARNESS_PHRASE in _ask("cp x A/config.toml", cwd=str(tmp_path))
        # and a spelling in another case folds without the file system
        upper = posixpath.join(str(home).upper(), ".CODEX", "x")
        assert HARNESS_PHRASE in _ask(f"cp x {upper}", cwd="/")
