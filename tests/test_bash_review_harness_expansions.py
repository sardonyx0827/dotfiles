"""Harness-write detection: expansions the review resolves itself, and when it must not.

Pins, for .claude/hooks/_bash_review_common.py:

- the values the review resolves itself (`$(mktemp)` / `$(whoami)` / `$(id -un)` /
  `$(logname)` / `$USER` / `$PWD` in a value) are not used when the same command can
  change what they rely on: TMPDIR / PATH / USER / LOGNAME / HOME (assigned, `+=`,
  exported, declared, read, unset, `env`, a prefix, `${X:=…}`), `hash`, a function or
  alias, `env -i`, eval / source. A destination whose substitution is overridden asks,
  and a command word never uses them (`$(whoami) X ~/.local/bin/csb`)
- mktemp creates what it names (`export TMPDIR=~/.local/bin; mktemp -d`)
- zsh named directories (`D=~/.local/bin; cp X ~D/csb`, `hash -d`) and braces in
  destinations (`cp X {/tmp,~/.local}/bin/git`)
- HOME / PWD / cdpath rewrites in the other spellings
- `.zwc`, `.zcompdump*`, `~/.zsh_secrets`, `~/.docker/completions`; the Homebrew
  prefix
- a glob command word that names a deny target is DENY (`/usr/bin/cur? http://x`)
"""

from __future__ import annotations

import os
import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
import _bash_review_common as _common  # noqa: E402

HARNESS_PHRASE = "agent harness"
UNRESOLVED_PHRASE = "cannot be resolved"
DYNAMIC_PHRASE = "decided at run time"


def _ask(command: str, cwd: str | None = None) -> str:
    return _common.harness_write_ask_reason(
        _common._split_commands(command), command, cwd=cwd
    )


def _deny(command: str) -> tuple[bool, str]:
    return _common.find_deny_command(_common._split_commands(command), command)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A HOME with the protected layout and a lab directory beside it."""
    home = tmp_path / "home"
    for d in (".claude/hooks", ".local/bin", ".docker/completions"):
        (home / d).mkdir(parents=True)
    lab = tmp_path / "lab"
    lab.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CDPATH", raising=False)
    for name in _common._HOMEBREW_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return lab


USER = _common._process_user()

# ---------------------------------------------------------------------------
# the command changes what the resolutions rely on
# ---------------------------------------------------------------------------

PREMISE_CHANGED_CASES = [
    f"whoami() {{ echo {USER}/.local/bin; }}; cp X /Users/$(whoami)",
    f"function whoami {{ echo {USER}/.local/bin; }}; cp X /Users/$(whoami)",
    "foo() { :; }; cp X /x/$(foo)",
    "PATH=/tmp/evil:$PATH; cp X /Users/$(whoami)",
    "PATH=/tmp/evil:$PATH; S=/x/$(whoami); cp X $S",
    "hash -p /tmp/evil/whoami whoami; cp X /Users/$(whoami)",
    "hash whoami=/tmp/evil/w; cp X /Users/$(whoami)",
    "path=(/tmp/evil $path); cp X /Users/$(whoami)",
    "fpath=(/x $fpath)\nautoload -U whoami\nS=/x/$(whoami); cp X $S",
    "alias whoami='echo x'; S=/x/$(whoami); cp X $S",
    "enable -f x whoami; S=/x/$(whoami); cp X $S",
    "export TMPDIR=~/.local/bin; D=$(mktemp -d); cp X $D/../git",
    "TMPDIR=~/.local/bin; export TMPDIR; D=$(mktemp -d); cp X $D/../git",
    "declare -x TMPDIR=~/.local/bin; D=$(mktemp -d); cp X $D/../git",
    "typeset -x TMPDIR=~/.local/bin; D=$(mktemp -d); cp X $D/../git",
    "TMPDIR=~/.local/bin D=$(mktemp -d); cp X $D/../git",
    "env TMPDIR=~/.local/bin sh -c 'D=$(mktemp -d); cp X $D/../git'",
    "unset TMPDIR; D=$(mktemp -d); cp X $D/x",
    "LOGNAME=x; S=/tmp/$(logname); cp X $S",
    "export HOME=/x; S=/tmp/$(whoami); cp X $S",
    "HOME+=/x; S=/tmp/$USER; cp X $S",
    "env -i sh -c 'S=/tmp/$USER; cp X $S/y'",
    "exec -c sh -c 'S=/tmp/$(whoami); cp X $S/y'",
    "eval x; D=$(mktemp -d); cp X $D/y",
    "source ./x; S=/tmp/$(whoami); cp X $S",
]


class TestPremisesChangedInTheCommand:
    @pytest.mark.parametrize("command", PREMISE_CHANGED_CASES)
    def test_asked(self, lab, command):
        assert _ask(command, cwd=str(lab)) != "", command

    def test_the_same_shapes_resolve_without_the_change(self, lab):
        for command in (
            "D=$(mktemp -d); cp X $D/x",
            "S=/tmp/$(whoami); cp X $S/b",
            "S=/tmp/$USER; cp X $S/b",
            "S=/tmp/$(logname); cp X $S/b",
        ):
            assert _ask(command, cwd=str(lab)) == "", command

    @pytest.mark.parametrize(
        "code,changed",
        [
            ("HOME=/x", True),
            ("HOME+=/x", True),
            ("HOME[1]=x", True),
            (": ${HOME:=/x}", True),
            (": ${HOME=/x}", True),
            ("read -r a HOME", True),
            ("printf -v HOME %s x", True),
            ("mapfile HOME < f", True),
            ("getopts ab HOME", True),
            ("typeset HOME", True),
            ("local HOME=/x", True),
            ("export HOME", True),
            ("unset HOME", True),
            ("for HOME in a; do :; done", True),
            ("python3 -c 'import os; print(os.environ[HOME])'", False),
            ("echo $HOME ${HOME}", False),
            ("cp X ~/.MYHOME=x", False),
        ],
    )
    def test_assigns_name(self, code, changed):
        assert _common._assigns_name("HOME", code) is changed

    @pytest.mark.parametrize(
        "code",
        [
            "env -i sh -c x",
            "env - sh -c x",
            "env -u X -i sh",
            "env --ignore-environment sh",
            "exec -c sh",
        ],
    )
    def test_environment_clearing(self, code):
        assert _common._env_clearing(code)
        assert not _common._CwdContext(code).substitutions_ok

    def test_env_without_clearing_keeps_the_resolution(self):
        assert _common._CwdContext("env FOO=1 make").substitutions_ok


class TestCommandWordsDoNotUseDerivedResolutions:
    @pytest.mark.parametrize(
        "command",
        [
            "$(whoami) X ~/.local/bin/csb",
            "whoami() { echo cp; }; $(whoami) X ~/.local/bin/csb",
            "$(mktemp -d)/x X ~/.local/bin/csb",
            "for c in cp mv; do $c X ~/.local/bin/csb; done",
        ],
    )
    def test_asked_next_to_a_protected_path(self, lab, command):
        assert DYNAMIC_PHRASE in _ask(command, cwd=str(lab)), command

    def test_a_loop_command_word_may_be_a_cd(self, lab):
        hooks = os.path.join(os.environ["HOME"], ".claude", "hooks")
        assert _ask(f"for c in cd; do $c {hooks}; cp X y; done", cwd=str(lab)) != ""

    def test_daily_shapes_are_not_asked(self, lab):
        for command in (
            "$(git rev-parse --show-toplevel)/scripts/x.sh arg",
            "for c in ls wc; do $c x; done",
        ):
            assert _ask(command, cwd=str(lab)) == "", command


# ---------------------------------------------------------------------------
# mktemp creates what it names
# ---------------------------------------------------------------------------


class TestMktempCreates:
    @pytest.mark.parametrize(
        "command",
        [
            "export TMPDIR=~/.local/bin; mktemp -d",
            "TMPDIR=~/.local/bin mktemp -d",
            "env TMPDIR=~/.local/bin mktemp -d",
            "D=$(TMPDIR=~/.local/bin mktemp -d)",
            "mktemp -p ~/.local/bin",
            "mktemp --tmpdir=~/.local/bin x.XXXX",
            "mktemp -t x -p ~/.local/bin",
            "mktemp ~/.local/bin/x.XXXX",
            "cd ~/.local/bin && mktemp x.XXXX",
            "read TMPDIR; mktemp",
        ],
    )
    def test_asked(self, lab, command):
        assert _ask(command, cwd=str(lab)) != "", command

    @pytest.mark.parametrize(
        "command",
        ["mktemp -d", "mktemp -t x", "mktemp -u -p ~/.local/bin", "mktemp x.XXXX"],
    )
    def test_not_asked(self, lab, command):
        assert _ask(command, cwd=str(lab)) == "", command

    def test_destinations(self):
        dests = _common._mktemp_destinations
        assert [d.path for d in dests(["-d"], "$TMPDIR")] == ["$TMPDIR/tmp.XXXXXXXXXX"]
        assert [d.path for d in dests(["-p", "/a", "x.X"], "$T")] == ["/a/x.X"]
        assert [d.path for d in dests(["--tmpdir", "/a"], "$T")] == [
            "/a/tmp.XXXXXXXXXX"
        ]
        assert [d.path for d in dests(["-t", "x.X"], "/t")] == ["/t/x.X"]
        assert [d.path for d in dests(["sub/x.X"], "/t")] == ["sub/x.X"]
        assert dests(["--dry-run"], "/t") == []


# ---------------------------------------------------------------------------
# zsh named directories and braces in destinations
# ---------------------------------------------------------------------------

NAMED_DIRECTORY_ASKED = [
    "D=~/.claude/hooks; cp X ~D/bash-review.py",
    "D=~/.local/bin; cp X ~D/csb",
    "hash -d q=~/.local/bin; cp X ~q/git",
    "hash -d q=~/.local/bin; tee ~q/git",
    "D=~/.local/bin; echo x > ~D/x",
    "D=~/.local/bin; rm ~D/csb",
    "D=~/.local/bin; mv a ~D/csb",
    "D=~/.local/bin; ln -sf a ~D/csb",
    "D=~/.local/bin; cp -t ~D a",
    "D=~/.local/bin; cd ~D && cp X csb",
    "D=~/.local/bin; S=~D/x; cp X $S",
    "D=~/.local/bin; S=~D/x; D=/tmp; cp X $S",
    "for d in ~/.local/bin; do cp X ~d/git; done",
    "D=$X; cp X ~D/csb",
    "D=rel; cp X ~D/csb",
    "read D; cp X ~D/csb",
    "eval x; D=/tmp/w; cp X ~D/y",
    "cp X ~HOME/.codex/config.toml",
    "cp X ~nosuchuser3/csb",
    "hash -d $N=/x; cp X ~q/y",
    "hash -dr; cp X ~q/y",
    "hash -d q=/tmp/w; unhash -d q; cp X ~q/y",
    "D=$X; S=~D/x; cp X $S",
    "D=/tmp/w; S=~D/$Y; cp X $S",
    "x && hash -d q=/tmp/w; cp X ~q/y",  # a conditional registration
    "cp X {/tmp/a,~-/x}",
]
NAMED_DIRECTORY_NOT_ASKED = [
    "D=/tmp/w; cp X ~D/y",
    "hash -d q=/tmp/w; cp X ~q/y",
    "D=/tmp/w; S=~D/x; cp X $S",
    "hash -d; cp X /tmp/y",
    "hash -d q; cp X /tmp/y",
    "if x; then hash -d q=/tmp/a; fi; cp X /tmp/y",
]
BRACE_ASKED = [
    "cp X {/tmp,~/.local}/bin/git",
    "cp X ~/{.local,x}/bin/git",
    "tee {/tmp,~/.local}/bin/git",
    "echo x > {/tmp,~/.local}/bin/git",
    "cp X ~/.{zshrc,x}",
    "cp X ~/.local/bi{m..n}/git",
    "cp X ~/.local/{a,{b,bin}}/git",
    "cp X /tmp/{" + ",".join(f"a{i}" for i in range(70)) + "}/x",
]
BRACE_NOT_ASKED = [
    "cp X {/tmp,/var/tmp}/x",
    "cp X x{a}y",
    "cp X x{}y",
    "find . -name x -exec cp a {} \\;",
    "cp X ${TMPDIR}/x",
]


class TestNamedDirectoriesAndBraces:
    @pytest.mark.parametrize("command", NAMED_DIRECTORY_ASKED + BRACE_ASKED)
    def test_asked(self, lab, command):
        assert _ask(command, cwd=str(lab)) != "", command

    @pytest.mark.parametrize("command", NAMED_DIRECTORY_NOT_ASKED + BRACE_NOT_ASKED)
    def test_not_asked(self, lab, command):
        assert _ask(command, cwd=str(lab)) == "", command

    @pytest.mark.parametrize("command", BRACE_ASKED)
    def test_braces_are_asked_without_a_cwd(self, lab, command):
        assert _ask(command) != "", command

    def test_the_own_home_by_user_name(self, lab, monkeypatch):
        home = os.environ["HOME"]
        monkeypatch.setattr(
            _common, "_user_home", lambda name: home if name == "me3" else None
        )
        assert HARNESS_PHRASE in _ask("cp X ~me3/.codex/config.toml")
        assert HARNESS_PHRASE in _ask("cp X ~me3/.codex/config.toml", cwd=str(lab))
        assert _ask("cp X ~me3/notes.txt", cwd=str(lab)) == ""

    def test_user_home(self, monkeypatch):
        assert _common._user_home.__wrapped__("bad name") is None
        assert _common._user_home.__wrapped__("nosuchuser3") is None
        if USER:
            assert _common._user_home.__wrapped__(USER)

    def test_user_home_without_pwd(self, monkeypatch):
        import pwd

        def boom(_name):
            raise OSError

        monkeypatch.setattr(pwd, "getpwnam", boom)
        assert _common._user_home.__wrapped__("root") is None

    def test_user_home_must_be_absolute(self, monkeypatch):
        import pwd

        class Entry:
            pw_dir = "relative"

        monkeypatch.setattr(pwd, "getpwnam", lambda _name: Entry())
        assert _common._user_home.__wrapped__("root") is None

    @pytest.mark.parametrize(
        "word,expected",
        [
            ("a{b,c}d", {"abd", "acd"}),
            ("{1..3}", {"1", "2", "3"}),
            ("{3..1}", {"1", "2", "3"}),
            ("{1..9..4}", {"1", "5", "9"}),
            ("{a..c}", {"a", "b", "c"}),
            ("{a..1}", {"{a..1}"}),
            ("${x,y}", {"${x,y}"}),
            ("x{a}y", {"x{a}y"}),
            ("{a,{b,c}}", {"a", "b", "c"}),
        ],
    )
    def test_brace_alternatives(self, word, expected):
        assert set(_common._brace_alternatives(word) or []) == expected

    def test_brace_limits(self):
        assert _common._brace_alternatives("{1..100000000}") is None
        assert (
            _common._brace_alternatives("{a,b}{c,d}{e,f}{g,h}{i,j}{k,l}{m,n}") is None
        )
        assert _common._is_harness_target("x/{1..1000}")
        ctx = _common._CwdContext("")
        assert _common._dest_paths("x/{1..1000}", frozenset({"/w"}), ctx) is None
        paths = _common._dest_paths("x/{a,b}", frozenset({"/w"}), ctx)
        assert sorted(paths or []) == ["/w/x/a", "/w/x/b"]


# ---------------------------------------------------------------------------
# HOME / PWD / cdpath in the other spellings
# ---------------------------------------------------------------------------

HOME_PWD_CDPATH_ASKED = [
    "HOME+=/.local; cp X ~/bin/git",
    "read HOME <<< x; cp X ~/bin/git",
    "HOME+=/x; cd; cp X bin/git",
    "printf -v HOME %s /x; cp X ~/y",
    "mapfile HOME < f; cp X ~/y",
    "typeset HOME=/x; cp X ~/y",
    ": ${HOME:=/x}; cp X ~/y",
    "export $'HO\\x4dE'=/x; cp X ~/y",
    'PWD+=/../../../.local/bin; cp X "$PWD/git"',
    ": ${PWD:=~/.local/bin}; S=$PWD; cp X $S/git",
    "cdpath=(~/.local); cd bin; cp X git",
    "cdpath=(~/.local)\ncd bin && cp X git",
]


class TestHomePwdCdpathSpellings:
    @pytest.mark.parametrize("command", HOME_PWD_CDPATH_ASKED)
    def test_asked(self, lab, command):
        assert _ask(command, cwd=str(lab)) != "", command

    def test_a_python_read_of_home_keeps_tilde(self, lab):
        code = "python3 -c 'import os; print(os.environ[\"HOME\"])'; cp X ~/notes"
        assert _ask(code, cwd=str(lab)) == ""


# ---------------------------------------------------------------------------
# zsh caches, secrets and completions; the Homebrew prefix
# ---------------------------------------------------------------------------

PROTECTED_NAME_CASES = [
    "cp X ~/.zshrc.zwc",
    "cp X ~/.zshenv.zwc",
    "cp X proj/.zprofile.zwc",
    "cp X ~/.zfunc/compinit.zwc",
    "cp X $D/x.zwc",
    "cp X ~/.zcompdump",
    "cp X ~/.zcompdump-host-5.9",
    "cp X ~/.zcompdump-host-5.9.zwc",
    "cp X ~/.zcomp*",
    "cp X ~/.zshrc.z*",
    "cp X ~/.zsh_secrets",
    "echo x > ~/.zsh_secrets",
    "cp X $D/.zsh_secrets",
    "cp X ~/.docker/completions/_docker",
    "cp X ~/.docker/comp*/_docker",
    "cp X ~/.d*/completions/_docker",
    "cp X /opt/homebrew/bin/git",
    "ln -sf X /opt/homebrew/bin/python3",
    "echo x > /opt/homebrew/opt/fzf/shell/key-bindings.zsh",
    "cp X /home/linuxbrew/.linuxbrew/bin/git",
    "cp X $HOMEBREW_PREFIX/bin/git",
    "cp X ${HOMEBREW_CELLAR}/x",
    "cp X $HOMEBREW_REPOSITORY/x",
    "cp X /opt/home*/bin/git",
    "cp X /o?t/homebrew/bin/git",
    "cp X /opt/{homebrew,x}/bin/git",
    "rm -rf /opt",
    "docker run -v /opt/homebrew:/x img",
]
PROTECTED_NAME_NOT_ASKED = [
    "brew install jq",
    "brew upgrade",
    "eval $(brew shellenv)",
    "ls /opt/homebrew/bin",
    "cat /opt/homebrew/etc/x",
    "/opt/homebrew/bin/python3 -m pytest -q > out.txt",
    "cp X ~/.docker/config.json",
    "cp X /opt/other/x",
    "cp X ~/.zshrc.bak",
    "cp X proj/.zsh_secrets.example",
]


class TestProtectedNames:
    @pytest.mark.parametrize("command", PROTECTED_NAME_CASES)
    def test_asked_with_and_without_a_cwd(self, lab, command):
        assert _ask(command, cwd=str(lab)) != "", command
        assert _ask(command) != "", command

    @pytest.mark.parametrize("command", PROTECTED_NAME_NOT_ASKED)
    def test_not_asked(self, lab, command):
        assert _ask(command, cwd=str(lab)) == "", command
        assert _ask(command) == "", command

    def test_home_only_names_under_another_directory(self, lab):
        assert _ask("cp X proj/.docker/completions/_x", cwd=str(lab)) == ""

    def test_a_relative_write_from_home(self, lab):
        home = os.environ["HOME"]
        assert HARNESS_PHRASE in _ask("cp X .zsh_secrets", cwd=home)
        assert HARNESS_PHRASE in _ask("cp X completions/_x", cwd=f"{home}/.docker")

    def test_the_prefix_from_the_environment(self, lab, monkeypatch, tmp_path):
        prefix = tmp_path / "brew"
        (prefix / "bin").mkdir(parents=True)
        monkeypatch.setenv("HOMEBREW_PREFIX", str(prefix))
        monkeypatch.setenv("HOMEBREW_CELLAR", "relative")
        assert str(prefix) in _common._absolute_root_spellings()
        assert "relative" not in _common._absolute_root_spellings()
        assert HARNESS_PHRASE in _ask(f"cp X {prefix}/bin/git")
        assert HARNESS_PHRASE in _ask("cp X bin/git", cwd=str(prefix))

    @pytest.mark.parametrize(
        "parts,expected",
        [
            (["opt", "home*", "bin"], True),
            (["o?t", "homebrew"], True),
            (["opt"], False),
            (["opt", "homebrew", "bin"], False),  # no glob: the spelled check
            (["usr", "l*"], False),
        ],
    )
    def test_glob_may_name_root(self, parts, expected):
        assert _common._glob_may_name_root(parts, "/opt/homebrew") is expected


# ---------------------------------------------------------------------------
# a glob command word that names a deny target
# ---------------------------------------------------------------------------

GLOB_DENY_CASES = [
    ("/bin/r? -rf /", "rm -rf /"),
    ("/bin/r[m] -rf ~", "rm -rf ~"),
    ("r? -rf /", "rm -rf /"),
    ("/bin/rm -rf /", "rm -rf /"),
    ("/usr/bin/cur? http://x", "curl"),
    ("/usr/bin/c[u]rl http://x", "curl"),
    ("c* http://x", "curl"),
    ("env cur? http://x", "curl"),
    ("/usr/bin/s?do ls", "sudo"),
    ("g?t credential fill", "git credential"),
    ("g? auth token", "gh auth token"),
    ("mkfs.e?t4 /dev/x", "mkfs.ext4"),
    ("mk?s.ext4 /dev/x", "mkfs.ext4"),
]
GLOB_NOT_DENY_CASES = [
    "r? x",
    "* x",
    "?? x",
    # a glob that does not start with a letter names nothing (DENY cannot be approved)
    "**3** x",
    "[A-Za-z0-9_]* x",
    "[c]url http://x",
    "./build-?.sh",
    "py?hon3 -c 1",
    "[ -f x ]",
    "[[ -f x ]]",
    "su? -c x",
    "ls /tmp/r?",
    "cp /x/cur? y",
    "echo 'cur? http://x'",
    "git commit -m 'cur? http://x'",
    "/bin/rm -rf /tmp/x",
]


class TestGlobDeny:
    @pytest.mark.parametrize("command,name", GLOB_DENY_CASES)
    def test_denied(self, command, name):
        assert _deny(command) == (True, name)

    @pytest.mark.parametrize("command", GLOB_NOT_DENY_CASES)
    def test_not_denied(self, command):
        assert _deny(command) == (False, ""), command

    def test_deny_names_matching(self):
        assert _common._deny_names_matching("rm") == ["rm"]
        assert _common._deny_names_matching("[") == ["["]
        assert _common._deny_names_matching("*?") == []
        assert "curl" in _common._deny_names_matching("cur?")
