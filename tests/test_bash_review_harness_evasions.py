"""Harness-write detection: the shapes that reach a protected path indirectly.

Pins, for `harness_write_ask_reason` in .claude/hooks/_bash_review_common.py:

- a link whose target is protected or an ancestor of a protected path (`~`,
  `~/.local`, `/`) asks at the `ln` itself; moving or removing such an ancestor asks;
  the link map does not trust the filesystem for paths the same command changed
- running out of link spellings asks instead of dropping spellings
- GNU `g`-prefixed writers, `busybox APPLET`, `sudo` / `doas` wrappers, and zsh's
  `=cmd` are seen through
- in cwd mode, `~-` / `~N` / `$OLDPWD` / `$DIRSTACK` and variables the same command
  assigned but that cannot be resolved ask
- `eval` / `source` / a function or alias defined in the command leave the cwd unknown
- globs, a leading `=` and operator expansions hide a command name; a command word
  taken from a variable is resolved when the command assigned it a literal, and
  otherwise asks only when the same command names a protected path
- nesting beyond what the review unwraps or parses asks
- `/net` and `/Network` are never touched on the filesystem
- `sed -I` (BSD in-place), and `HARNESS_CWD_UNKNOWN` for entries that know the cwd is
  unknown
- `~/.pyenv`, shell startup files and `~/.oh-my-zsh` are protected; values the
  command builds that resolve to unprotected paths are not asked
"""

from __future__ import annotations

import os
import posixpath
import shlex
import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
import _bash_review_common as _common  # noqa: E402

HARNESS_PHRASE = "agent harness"
OBFUSCATION_PHRASE = "hides the command name"
UNRESOLVED_PHRASE = "cannot be resolved"
NESTING_PHRASE = "nests"


def _ask(command: str, cwd: str | None = None) -> str:
    return _common.harness_write_ask_reason(
        _common._split_commands(command), command, cwd=cwd
    )


def _home_is_under(directory: str) -> bool:
    """Whether HOME, as spelled or resolved, sits under the absolute `directory`."""
    if not directory.startswith("/"):
        return False
    home = os.environ["HOME"]
    for base in (posixpath.normpath(directory), os.path.realpath(directory)):
        for spelled in (home, os.path.realpath(home)):
            if spelled == base or spelled.startswith(base.rstrip("/") + "/"):
                return True
    return False


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A real HOME with the protected layout, and a lab directory beside it.

    lab/d is a symlink to a directory and lab/docs a real directory, for the shapes
    that replace an existing directory within one command.
    """
    home = tmp_path / "home"
    for d in (
        ".claude/hooks",
        ".codex",
        ".local/bin",
        ".local/share",
        ".local/state/claude-sandbox",
    ):
        (home / d).mkdir(parents=True)
    lab = tmp_path / "lab"
    (lab / "olddir").mkdir(parents=True)
    (lab / "docs").mkdir()
    (lab / "d").symlink_to(lab / "olddir")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CDPATH", raising=False)
    return lab


# ---------------------------------------------------------------------------
# links to protected paths and their ancestors; replacing an ancestor
# ---------------------------------------------------------------------------

LINK_TARGET_CASES = [
    "ln -s ~/.local d",
    "ln -sfn ~/.local d",
    "ln -s ~/.local/bin d",
    "ln -s ~/.local/state d",
    "ln -s ~ d",
    "ln -s $HOME d",
    "ln -s ${HOME}/ d",
    "ln -s / d",
    "ln -s ~/.claude d",
    "ln -s ~/.codex d",
    "ln -s .local d",
    "ln -s x/.local d",
    "ln -s x/.local/state d",
    "ln ~/.claude/settings.json s",
    "ln -s .claude/hooks/x.py /tmp/l",
    "ln -s -t dir ~/.local",
    "ln -st dir ~/.local/bin/csb",
    "ln -s ~/.local",
    "ln -s ~/.l* d",
    "cp -s ~/.local/bin/csb x",
    "cp -l ~/.claude/settings.json s",
    "cp -al ~/.local x",
    "cp --symbolic-link ~/.local x",
    "gln -s ~/.local d",
    # a link replaced and written through in the same command, caught at the link
    "ln -sfn ~/.local d && cp X d/bin/csb",
    "rm d; ln -s ~/.local d; cp X d/bin/csb",
    "mv d e; ln -s ~/.local/bin d; cp X d/csb",
    "rm d; ln -s ~/.local d; cd d/bin; cp X csb",
    "mv docs docs.bak; ln -s ~/.local/bin docs; cp payload docs/csb",
]

ANCESTOR_REPLACE_CASES = [
    "mv ~/.local /tmp/o",
    "mv ~/.local/state /tmp/o",
    "rm -rf ~/.local",
    "rm -r ~",
    "mv .local x",
    "rm -rf x/.local",
    "mv /tmp/x ~",
    "ln -sfn /tmp/evil ~/.local",
    "ln -s /tmp/evil ~/",
    "mv ~/.local /tmp/o; ln -s /tmp/evil ~/.local",
    "gmv ~/.local /tmp/o",
]

LINK_NOT_A_WRITE_CASES = [
    "ln -s /tmp/a b",
    "ln -s ../lib/x.so x",
    "ln -sf ~/work/x ~/bin/x",
    "ln -s ~/.local/share/nvim n",
    "ln -s ~/work/proj p",
    "ln -s ~/.localrc r",
    "rm -rf build",
    "mv a b",
    "mv ~/.local/share/x /tmp/",
    "rm ~/.local/share/x",
    "cp x ~/",
    "cp x ~/.local/",
    "cp -r a b",
]


class TestLinksAndAncestors:
    @pytest.mark.parametrize("command", LINK_TARGET_CASES + ANCESTOR_REPLACE_CASES)
    def test_lexically(self, lab, command):
        assert HARNESS_PHRASE in _ask(command), command

    @pytest.mark.parametrize("command", LINK_TARGET_CASES + ANCESTOR_REPLACE_CASES)
    def test_with_the_cwd(self, lab, command):
        assert HARNESS_PHRASE in _ask(command, cwd=str(lab)), command

    @pytest.mark.parametrize("command", LINK_NOT_A_WRITE_CASES)
    def test_everyday_links_and_moves_are_not_asked(self, lab, command):
        # A real HOME never sits under /tmp. pytest's tmp_path, and so this lab's
        # HOME, does on Linux (/tmp/pytest-of-<user>/…), and then `/tmp/` is an
        # ancestor of the protected paths: a move into it counts as replacing an
        # ancestor whatever name is moved (the rule is lexical; see the test
        # below), and asking is right for that layout.
        home_is_under_the_destination = _home_is_under(command.split()[-1])
        for cwd in (None, str(lab)):
            reason = _ask(command, cwd=cwd)
            if home_is_under_the_destination:
                assert HARNESS_PHRASE in reason, command
            else:
                assert reason == "", command

    def test_a_move_into_a_directory_that_holds_home_is_asked(self, lab):
        # the destination is an ancestor of ~/.claude: the rule does not look at
        # the name that is moved, so `mv x <ancestor>/` asks on every system
        holder = os.path.dirname(os.environ["HOME"])
        assert HARNESS_PHRASE in _ask(f"mv ~/.local/share/x {holder}/")
        assert HARNESS_PHRASE in _ask(f"mv ~/.local/share/x {holder}/", cwd=str(lab))

    def test_an_ancestor_reached_through_the_cwd(self, lab):
        home = os.environ["HOME"]
        assert HARNESS_PHRASE in _ask("cd ~; mv .local x", cwd=str(lab))
        assert HARNESS_PHRASE in _ask("mv .local x", cwd=home)
        assert HARNESS_PHRASE in _ask("ln -s .. up", cwd=f"{home}/.local/bin")
        assert HARNESS_PHRASE in _ask("rm -rf ..", cwd=f"{home}/.local/bin")
        assert HARNESS_PHRASE in _ask("ln -s ../.. up", cwd=f"{home}/.local/share")

    def test_without_a_home_a_tilde_link_is_an_ancestor(self, lab, monkeypatch):
        monkeypatch.setenv("HOME", "")
        assert HARNESS_PHRASE in _ask("ln -s ~ d")

    def test_a_link_through_an_existing_symlink_is_resolved(self, lab):
        (lab / "toloc").symlink_to(os.path.join(os.environ["HOME"], ".local"))
        assert _ask("ln -s toloc x") == ""  # lexical: the name says nothing
        assert HARNESS_PHRASE in _ask("ln -s toloc x", cwd=str(lab))

    def test_paths_changed_earlier_count_as_both_a_name_and_a_directory(self, lab):
        # newdir does not exist yet; the command creates it before copying into it
        cmd = "rm -rf newdir; mkdir newdir; cp -r /tmp/stage/.claude newdir"
        assert HARNESS_PHRASE in _ask(cmd, cwd=str(lab))
        # an existing symlink replaced in the same command: both spellings are kept
        cmd = "ln -sfn /tmp/z d; ln -sfn /tmp/y d; cp X d/y/settings.json"
        assert _ask(cmd, cwd=str(lab)) == ""
        cmd = "rm d; ln -s /tmp/stage d; cp -r /tmp/s/.claude d"
        assert HARNESS_PHRASE in _ask(cmd, cwd=str(lab))


class TestResolutionBudget:
    def test_too_many_destinations_to_resolve_ask(self, lab):
        cds = " || ".join(f"cd /tmp/c{i}" for i in range(15))
        many = " ".join(f"s{i}" for i in range(_common._RESOLUTION_BUDGET // 10))
        assert "could not be resolved" in _ask(f"{cds}; mv {many} d", cwd=str(lab))
        assert _ask(f"mv {many} d") == ""


class TestAliasSpellingLimit:
    def test_too_many_spellings_raise_instead_of_dropping(self, lab):
        ctx = _common._CwdContext("")
        ctx.aliases = [(f"/a{i}", f"/a{i + 1}") for i in range(40)]
        with pytest.raises(_common._Unresolvable):
            _common._alias_spellings("/a0/x", ctx)

    def test_a_long_link_chain_asks(self, lab):
        chain = "; ".join(f"ln -sfn /n{i} a" for i in range(1, 21)) + "; cp X a/csb"
        assert _ask(chain, cwd=str(lab)) != ""


# ---------------------------------------------------------------------------
# writers under another name
# ---------------------------------------------------------------------------

RENAMED_WRITER_CASES = [
    "gtee ~/.local/bin/csb < payload",
    "ginstall payload ~/.local/bin/csb",
    "gln -sf payload ~/.local/bin/csb",
    "gmv x ~/.local/bin/csb",
    "gdd if=X of=~/.local/bin/csb",
    "gcp X ~/.local/bin/csb",
    "/opt/homebrew/bin/gcp X ~/.local/bin/csb",
    "gtouch ~/.claude/settings.json",
    "grm ~/.claude/hooks/x",
    "gchmod +x ~/.local/bin/x",
    "gtruncate -s0 ~/.claude/CLAUDE.md",
    "GCP X ~/.local/bin/csb",
    "busybox cp X ~/.local/bin/csb",
    "busybox tee ~/.local/bin/csb",
    "/bin/busybox ln -sf x ~/.local/bin/csb",
    "sudo -u root cp X ~/.local/bin/csb",
    "sudo cp X ~/.local/bin/csb",
    "sudo -E -u root -- tee ~/.claude/settings.json",
    "sudo --user=root mv x ~/.local/bin/csb",
    "sudo -e ~/.claude/settings.json",
    "sudo -Z cp X ~/.local/bin/csb",  # an unknown flag: every later writer counts
    "doas -u root tee ~/.local/bin/csb",
    "doas --foo cp X ~/.local/bin/csb",  # doas has no long options: every writer counts
    "sudo --unknown cp X ~/.local/bin/csb",
    "sudo --preserve-env cp X ~/.local/bin/csb",
    "sudo --edit ~/.claude/settings.json",
    "sudo $X cp X ~/.local/bin/csb",
    "sudo env X=1 cp X ~/.local/bin/csb",
    "sudo sh -c 'cp X ~/.local/bin/csb'",
    "busybox sh -c 'cp X ~/.local/bin/csb'",
]

RENAMED_NOT_A_WRITE_CASES = [
    "gcc -o out x.c",
    "grep x ~/.claude/settings.json",
    "gls -la ~/.local/bin",
    "gcp abc123",
    "busybox ls ~/.local/bin",
    "busybox",
    "sudo -l",
    "sudo -u root ls ~/.local/bin",
    "doas cat ~/.claude/settings.json",
]


class TestRenamedWriters:
    @pytest.mark.parametrize("command", RENAMED_WRITER_CASES)
    def test_renamed_and_wrapped_writers_are_asked(self, command):
        assert HARNESS_PHRASE in _ask(command), command

    @pytest.mark.parametrize("command", RENAMED_NOT_A_WRITE_CASES)
    def test_lookalikes_are_not_asked(self, command):
        assert _ask(command) == "", command

    def test_a_long_runner_chain_does_not_recurse(self):
        chain = "doas " * 3000 + "cp a ~/.local/bin/x"
        assert HARNESS_PHRASE in _ask(chain)
        assert _ask("doas " * 20 + "ls") == ""

    def test_zsh_equals_expansion_hides_the_name(self):
        assert OBFUSCATION_PHRASE in _ask("=cp payload ~/.local/bin/csb")
        assert OBFUSCATION_PHRASE in _ask("=cp payload out")

    def test_a_wrapped_writer_through_the_cwd(self, lab):
        cwd = os.path.join(os.environ["HOME"], ".local", "bin")
        assert HARNESS_PHRASE in _ask("gcp X csb", cwd=cwd)
        assert HARNESS_PHRASE in _ask("busybox cp X csb", cwd=cwd)
        assert HARNESS_PHRASE in _ask("sudo -u root cp X csb", cwd=cwd)


# ---------------------------------------------------------------------------
# destinations that depend on the directory history or on an unresolved
# variable of the same command (cwd mode)
# ---------------------------------------------------------------------------

HISTORY_DEST_CASES = [
    "cd ~/.local/bin; cd /tmp; cp X ~-/csb",
    'cd ~/.local/bin; cd /tmp; cp X "$OLDPWD"',
    "cd ~/.local/bin; cd /tmp; cp X ~-",
    "cd ~/.local/bin; pushd /tmp; cp X ~1",
    "cd ~/.local/bin; pushd /tmp; cp X ~+1",
    "cp X ~+/x",
    "cp X ~-2/x",
    "cp X ${OLDPWD}/x",
    "cp X ${DIRSTACK[1]}/x",
    "cp X $dirstack[1]/x",
    "echo x > ~-/y",
    "ln -s ~- d",
]

UNRESOLVED_VARIABLE_CASES = [
    "S=/v0; if x; then "
    + "; ".join(f"S=/v{i}" for i in range(1, 10))
    + "; S=~/.local/bin; fi; cp X $S/csb",
    "if x; then S=~/.local; fi; ln -s $S d; cp X d/bin/csb",
    "if x; then S=~/.local; fi; ln -s $S newname",
    "D=$(echo ~/.local/bin); cp build/csb $D/csb",
    "X=$(f); cp -r $X ~/",
    "true && S=/tmp; cp X $S/y",
    "S=/tmp; read S; cp X $S/y",
    "S=/tmp; eval x; cp X $S/y",
    "S=/tmp; true || S=/a; T=$S$S$S$S; cp X $T/y",
]


class TestHistoryAndVariableDestinations:
    @pytest.mark.parametrize("command", HISTORY_DEST_CASES + UNRESOLVED_VARIABLE_CASES)
    def test_asked_with_the_cwd(self, lab, command):
        assert _ask(command, cwd=str(lab)) != "", command

    @pytest.mark.parametrize("command", HISTORY_DEST_CASES)
    def test_left_to_cwd_mode(self, lab, command):
        assert _ask(command) == "", command

    @pytest.mark.parametrize(
        "command",
        [
            "cp X $TMPDIR/x",
            'cp X "$HOME/x"',
            "S=/tmp/a; cp X $S/b",
            "S=/tmp/a; cd $S && cp X b",
            "cp X ~/x",
            # `~root` は実在のユーザー (同名の変数も代入も無い): その HOME へ解決して照合
            "cp X ~root/x",
        ],
    )
    def test_environment_and_literal_variables_are_unchanged(self, lab, command):
        assert _ask(command, cwd=str(lab)) == "", command

    def test_a_tilde_name_that_is_no_user_is_asked(self, lab):
        # ユーザーでも代入でもない `~name` は zsh の名前付きディレクトリ (環境の
        # 変数) かもしれず、bash では字面の相対パスになるので、決めずに確認へ回す
        assert UNRESOLVED_PHRASE in _ask("cp X ~nosuchuser3/x", cwd=str(lab))

    def test_cd_and_destination_agree_on_an_unresolved_variable(self, lab):
        vals = "; ".join(f"S=/v{i}" for i in range(1, 10))
        head = f"S=/v0; if x; then {vals}; S=~/.local/bin; fi; "
        assert _ask(head + "cd $S; cp X .", cwd=str(lab)) != ""
        assert _ask(head + "cp X $S/csb", cwd=str(lab)) != ""


# ---------------------------------------------------------------------------
# commands that may change the cwd without a literal cd
# ---------------------------------------------------------------------------

HIDDEN_CD_CASES = [
    "eval cd ~/.local/bin; cp X csb",
    "eval 'cd ~/.local/bin'; cp X .",
    "source <(echo cd ~/.local/bin); cp X .",
    ". ./env.sh; cp X y",
    "g() { cd ~/.local/bin; }; g; cp X csb",
    "function g { cd ~/.local/bin; }; g && cp X csb",
    "alias g='cd ~/.local/bin'; g; cp X .",
    "alias -g G='cd ~/.local/bin'; ls G; cp X .",  # zsh global alias: any word
    "f () { cd ~/.local/bin; }; f; cp X csb",
]


class TestDefinedCommands:
    @pytest.mark.parametrize(
        "text,names",
        [
            ("f () { cd x; }; f", {"f"}),
            ("f() { :; }", {"f"}),
            ("function g { :; }", {"g"}),
            ("function", {"*"}),
            ("alias g='cd x' h=ls", {"g", "h"}),
            ("alias -g G=x", {"*", "g"}),
            ("alias g", set()),  # only prints the alias
            ("case $a in x) f () { :; };; esac", {"f"}),
            ("echo 'f()'; (ls)", set()),
        ],
    )
    def test_names(self, text, names):
        assert _common._defined_commands(text) == frozenset(names)


class TestHiddenCd:
    @pytest.mark.parametrize("command", HIDDEN_CD_CASES)
    def test_relative_writes_after_a_hidden_cd_are_asked(self, lab, command):
        assert _ask(command, cwd=str(lab)) != "", command

    @pytest.mark.parametrize(
        "command",
        [
            'eval "$(ssh-agent)"; cp a /tmp/b',
            "f() { :; }; ls; cp a /tmp/b",
            "source ./env.sh",
        ],
    )
    def test_absolute_writes_and_no_writes_are_not_asked(self, lab, command):
        assert _ask(command, cwd=str(lab)) == "", command


# ---------------------------------------------------------------------------
# command words that hide their name, and commands taken from variables
# ---------------------------------------------------------------------------

HIDDEN_NAME_CASES = [
    "${x:-cp} X ~/.local/bin/csb",
    '"${PY:-python3}" -V',
    "${PYTHON:-python3} -V",
    "${x/a/b} y",
    "${x#a} y",
    "${!x} y",
    "${#x} y",
    "${x:1:2} y",
    "${x^^} y",
    "${x@Q} y",
    # an unquoted glob in the command word, whatever it would match
    "c? X ~/.local/bin/csb",
    "/bin/c[p] X ~/.local/bin/csb",
    "/bin/c? X out",
    "/usr/bin/cur? http://x",
    "~/.local/bin/cs? apply 0123456789ab",
    "*.sh",
    "./build-?.sh",
    "env c? x",
]

VARIABLE_COMMAND_CASES = [
    "CP=cp; $CP X ~/.local/bin/csb",
    'CP=cp; "$CP" X ~/.local/bin/csb',
    "CP=/bin/cp; ${CP} X ~/.local/bin/csb",
    "T=tee; echo x | $T ~/.claude/settings.json",
    "SH=sh; $SH -c 'cp X ~/.local/bin/csb'",
    "CP=ls & $CP X ~/.local/bin/csb",  # assigned in a background job: unset here
    "$CP X ~/.local/bin/csb",
    "$x$y X ~/.local/bin/csb",
    "$(echo cp) X ~/.local/bin/csb",
    '"$@" ~/.claude/settings.json',
    "$MV ~/.local /tmp/o",
    "$PY .claude/hooks/bash-review.py < p.json",
]

VARIABLE_COMMAND_NOT_A_WRITE_CASES = [
    "$PY -m pytest",
    '"$PY" -m pytest tests/',
    "PY=python3; $PY .claude/hooks/bash-review.py < p.json",
    "CP=cp; $CP a b",
    "$EDITOR notes.md",
    '"$@"',
    "$1 --version",
    '"$" x',
    "[ -f x ]",
    "[[ -f x ]]",
    "test -f x",
    "echo *",
    "ls *.py",
    "cp -- *.py /tmp/",
    "'c?' x",  # a quoted glob is a literal name
]


class TestCommandWords:
    @pytest.mark.parametrize("command", HIDDEN_NAME_CASES)
    def test_hidden_names_are_asked(self, command):
        assert OBFUSCATION_PHRASE in _ask(command), command

    @pytest.mark.parametrize("command", VARIABLE_COMMAND_CASES)
    def test_variable_commands_touching_the_harness_are_asked(self, lab, command):
        assert _ask(command) != "", command
        assert _ask(command, cwd=str(lab)) != "", command

    @pytest.mark.parametrize("command", VARIABLE_COMMAND_NOT_A_WRITE_CASES)
    def test_other_variable_commands_are_not_asked(self, lab, command):
        assert _ask(command) == "", command
        assert _ask(command, cwd=str(lab)) == "", command

    def test_a_variable_command_through_the_cwd(self, lab):
        cwd = os.path.join(os.environ["HOME"], ".local", "bin")
        assert _ask("$CP X csb") == ""
        assert _ask("$CP X csb", cwd=cwd) != ""
        assert _ask("CP=cp; $CP X csb", cwd=cwd) != ""
        assert _ask("CP=ls; $CP X csb", cwd=cwd) == ""
        assert _ask("CD=cd; $CD ~/.local/bin && cp X csb", cwd=str(lab)) != ""
        # either value may run: `cd` moves, `ls` does not
        cmd = "X=cd; true || X=ls; $X ~/.local/bin && cp a b"
        assert _ask(cmd, cwd=str(lab)) != ""

    def test_a_glob_command_word_is_asked_in_both_modes(self, lab):
        assert OBFUSCATION_PHRASE in _ask("/usr/bin/cur? X csb")
        assert OBFUSCATION_PHRASE in _ask("/usr/bin/cur? X csb", cwd=str(lab))

    def test_too_many_words_for_a_variable_command_ask(self, lab):
        words = " ".join(f"a{i}" for i in range(_common._VARIABLE_WORDS_LIMIT + 1))
        assert _ask(f"$X {words}", cwd=str(lab)) != ""
        assert _ask(f"$X {words}") == ""


# ---------------------------------------------------------------------------
# nesting beyond what the review unwraps or parses
# ---------------------------------------------------------------------------


def _wrap(command: str, levels: int) -> str:
    for _ in range(levels):
        command = "sh -c " + shlex.quote(command)
    return command


class TestNesting:
    def test_shell_code_deeper_than_the_lexical_check_asks(self):
        assert HARNESS_PHRASE in _ask(_wrap("cp X ~/.local/bin/csb", 2))
        assert NESTING_PHRASE in _ask(_wrap("cp X ~/.local/bin/csb", 3))
        assert NESTING_PHRASE in _ask(_wrap("ls", 3))
        assert _ask(_wrap("ls", 2)) == ""

    def test_shell_code_deeper_than_the_cwd_walk_asks(self, lab):
        assert _ask(_wrap("cd ~/.local/bin && cp X .", 2), cwd=str(lab)) != ""
        assert _ask(_wrap("cd ~/.local/bin && cp X .", 4), cwd=str(lab)) != ""

    def test_substitutions_deeper_than_the_walk_ask(self, lab):
        deep = "echo " + "$(echo " * 5 + "x" + ")" * 5
        assert NESTING_PHRASE in _ask(deep)
        assert NESTING_PHRASE in _ask(deep, cwd=str(lab))
        assert _ask("echo $(echo $(echo x))") == ""
        # shell code reached only through substitutions, at the walk's depth limit
        assert NESTING_PHRASE in _ask("echo $(echo $(echo $(sh -c 'ls')))")

    def test_span_nesting_is_bounded(self):
        deep = "${a:-" * (_common._SPAN_DEPTH_LIMIT + 2) + "}" * (
            _common._SPAN_DEPTH_LIMIT + 2
        )
        with pytest.raises(_common._TooDeep):
            _common._span_at(deep, 0)

    @pytest.mark.parametrize("cwd", [None, "lab"])
    def test_deeply_nested_parameter_expansions_ask_without_a_hook_error(
        self, lab, cwd
    ):
        command = "x " + "${a:-" * 3000 + "}" * 3000
        reason = _ask(command, cwd=str(lab) if cwd else None)
        assert NESTING_PHRASE in reason
        assert reason.endswith("— confirm manually")

    def test_a_recursion_error_inside_the_walk_asks(self, lab, monkeypatch):
        def boom(*_a, **_k):
            raise RecursionError

        monkeypatch.setattr(_common, "_cwd_events", boom)
        assert NESTING_PHRASE in _ask("ls")


# ---------------------------------------------------------------------------
# automount points are never touched
# ---------------------------------------------------------------------------


class TestAutomountPaths:
    @pytest.mark.parametrize(
        "command",
        [
            "cp X /net/example.invalid/x",
            "cp X /Network/Servers/x",
            "cp X /NET/host/x",
            "cd /net/host && ls",
            "cd /Network && cp a b",
            "ln -s /net/host/x l",
        ],
    )
    def test_asked_without_touching_the_filesystem(self, lab, monkeypatch, command):
        real_realpath, real_lstat = os.path.realpath, os.lstat

        def guard(fn):
            def call(path, *a, **k):
                if str(path).casefold().startswith(("/net", "/network")):
                    raise AssertionError(f"touched {path}")
                return fn(path, *a, **k)

            return call

        monkeypatch.setattr(_common.os.path, "realpath", guard(real_realpath))
        monkeypatch.setattr(_common.os, "lstat", guard(real_lstat))
        reason = _ask(command, cwd=str(lab))
        assert "could not be resolved" in reason, command

    def test_a_resolved_spelling_under_net_asks(self, lab, monkeypatch):
        monkeypatch.setattr(_common.os.path, "realpath", lambda _p: "/net/host/x")
        assert "could not be resolved" in _ask("cp X y", cwd=str(lab))


# ---------------------------------------------------------------------------
# BSD sed -I; the HARNESS_CWD_UNKNOWN constant
# ---------------------------------------------------------------------------


class TestSedCapitalI:
    @pytest.mark.parametrize(
        "command",
        [
            "sed -I '' s/a/b/ ~/.claude/settings.json",
            "sed -I .bak -e s/a/b/ .claude/settings.json",
            "sed -EI '' s/a/b/ .claude/hooks/x",
        ],
    )
    def test_bsd_in_place_is_a_write(self, command):
        assert HARNESS_PHRASE in _ask(command), command

    def test_reading_is_not(self):
        assert _ask("sed -n -E p .claude/settings.json") == ""


class TestUnknownCwdApi:
    def test_the_constant_marks_the_cwd_unknown(self, lab):
        unknown = _common.HARNESS_CWD_UNKNOWN
        assert UNRESOLVED_PHRASE in _ask("cp x y", cwd=unknown)
        assert _ask("cp x /tmp/y", cwd=unknown) == ""
        assert _ask("ls", cwd=unknown) == ""
        assert not unknown.startswith("/")

    def test_the_lexical_check_still_applies(self, lab):
        unknown = _common.HARNESS_CWD_UNKNOWN
        assert HARNESS_PHRASE in _ask("cp x ~/.local/bin/csb", cwd=unknown)


# ---------------------------------------------------------------------------
# ~/.pyenv (the hook's python3 is a pyenv shim that follows .python-version)
# ---------------------------------------------------------------------------

PYENV_WRITE_CASES = [
    "cp x ~/.pyenv/versions/x/bin/python3",
    "mkdir -p ~/.pyenv/versions/x/bin && cat > ~/.pyenv/versions/x/bin/python3 <<'EOF'"
    "\nprint(1)\nEOF",
    "tee ~/.pyenv/version",
    "install -m 755 x ~/.pyenv/versions/x/bin/python3",
    "ln -s /tmp/evil ~/.pyenv/versions/x",
    "cp -r /tmp/fake ~/.pyenv/versions/",
    "mv /tmp/fake ~/.pyenv/versions/x",
    "rm -rf ~/.pyenv/versions/3.12.0",
    "ln -sfn /tmp/fake ~/.pyenv",
    "echo x > $PYENV_ROOT/versions/x/bin/python3",
    "echo x > ${PYENV_ROOT}/version",
    "cp x $HOME/.pyenv/shims/python3",
    "cp x ~/.Pyenv/versions/x",
    "cp x ~/.py*/versions/x",
    "docker run -v ~/.pyenv:/p img",
]

PYENV_NOT_A_WRITE_CASES = [
    "pyenv install 3.12.0",  # not a writer the check knows (the README's gaps)
    "pip install requests",
    "pyenv global 3.12.0",
    "python3 -m venv .venv",
    "ls ~/.pyenv/versions",
    "cat ~/.pyenv/version",
    "~/.pyenv/shims/python3 -V",
    "cp ~/.pyenv/version /tmp/v",
    "echo x > ~/.pyenvrc",
    # what the shim reads from the cwd is the launcher's to pin, not this check's
    "echo 3.12 > .python-version",
]


class TestPyenvIsProtected:
    @pytest.mark.parametrize("command", PYENV_WRITE_CASES)
    def test_writes_into_pyenv_are_asked(self, lab, command):
        assert HARNESS_PHRASE in _ask(command), command
        assert HARNESS_PHRASE in _ask(command, cwd=str(lab)), command

    @pytest.mark.parametrize("command", PYENV_NOT_A_WRITE_CASES)
    def test_installers_reads_and_runs_are_not_asked(self, lab, command):
        assert _ask(command) == "", command
        assert _ask(command, cwd=str(lab)) == "", command

    def test_pyenv_root_from_the_environment(self, lab, tmp_path, monkeypatch):
        monkeypatch.setenv("PYENV_ROOT", "/opt/pyenv")
        assert HARNESS_PHRASE in _ask("cp x /opt/pyenv/versions/x/bin/python3")
        assert HARNESS_PHRASE in _ask("cp x /OPT/PYENV/versions/x")
        assert HARNESS_PHRASE in _ask("docker run -v /opt/pyenv:/p img")
        assert HARNESS_PHRASE in _ask("mv /opt /tmp/o")  # an ancestor of it
        assert _ask("cp x /opt/other/x") == ""

    def test_a_symlinked_pyenv_root_is_resolved_with_the_cwd(
        self, lab, tmp_path, monkeypatch
    ):
        real = tmp_path / "real-pyenv"
        (real / "versions").mkdir(parents=True)
        (tmp_path / "pylink").symlink_to(real)
        monkeypatch.setenv("PYENV_ROOT", str(tmp_path / "pylink"))
        cmd = f"cp x {real}/versions/x"
        assert _ask(cmd) == ""  # lexical: the real spelling is not PYENV_ROOT
        assert HARNESS_PHRASE in _ask(cmd, cwd=str(lab))

    def test_a_relative_pyenv_root_is_ignored(self, lab, monkeypatch):
        monkeypatch.setenv("PYENV_ROOT", "relative/pyenv")
        assert _ask("cp x relative/pyenv/versions/x") == ""


# ---------------------------------------------------------------------------
# shell startup files (anywhere) and ~/.oh-my-zsh
# ---------------------------------------------------------------------------

SHELL_STARTUP_WRITE_CASES = [
    "echo 'export X=1' >> ~/.zshrc",
    "echo x > .zshrc",
    "sed -i '' s/a/b/ ~/.zshenv",
    "sed -I '' s/a/b/ ~/.bash_profile",
    "cp x ~/.profile",
    "mv x /Users/x/proj/.zshrc",
    "install x ~/.zlogin",
    "ln -sf x ~/.zlogout",
    "dd if=x of=~/.bashrc",
    "tee -a ~/.bash_login",
    "truncate -s0 .zprofile",
    "perl -pi -e s/a/b/ dotfiles/.zshrc",
    "cp x ~/.ZSHRC",
    "echo x >> ~/.z*rc",
    "cp x $D/.zshrc",
    "cp x ~/.oh-my-zsh/custom/x.zsh",
    "echo x > ~/.oh-my-zsh/oh-my-zsh.sh",
    "cp x $ZSH/custom/x.zsh",
    "cp x ${ZSH_CUSTOM}/plugins/x/x.plugin.zsh",
    "rm -rf ~/.oh-my-zsh",
    "cp -r /tmp/p ~/.oh-my-zsh/plugins/",
    "ln -s ~/.zshrc z",  # an alias of a startup file
    "docker run -v ~/.oh-my-zsh:/o img",
]

SHELL_STARTUP_NOT_A_WRITE_CASES = [
    "source ~/.zshrc",
    ". ~/.profile",
    "cat ~/.zshrc",
    "grep x .bashrc",
    "echo x > .zshrc.bak",
    "echo x > .profile-old",
    "echo x > foo.profile",
    "cp ~/.zshrc /tmp/zshrc.bak",
    "ls ~/.oh-my-zsh/plugins",
    "echo x > ~/.oh-my-zsh-backup/x",
    "zsh -c 'echo hi'",
    "echo $ZSH_VERSION > v",
    "echo x > .zsh_history",
]


class TestShellStartupFilesAreProtected:
    @pytest.mark.parametrize("command", SHELL_STARTUP_WRITE_CASES)
    def test_writes_are_asked(self, lab, command):
        assert HARNESS_PHRASE in _ask(command), command
        assert HARNESS_PHRASE in _ask(command, cwd=str(lab)), command

    @pytest.mark.parametrize("command", SHELL_STARTUP_NOT_A_WRITE_CASES)
    def test_reads_and_lookalikes_are_not_asked(self, lab, command):
        assert _ask(command) == "", command
        assert _ask(command, cwd=str(lab)) == "", command

    def test_relative_writes_through_the_cwd(self, lab):
        home = os.environ["HOME"]
        assert HARNESS_PHRASE in _ask("cp x zshrc-new && mv zshrc-new .zshrc", home)
        assert HARNESS_PHRASE in _ask("cd ~/.oh-my-zsh && cp x custom/y", str(lab))

    def test_a_startup_file_that_is_a_symlink_to_another_name(self, lab, tmp_path):
        target = tmp_path / "dotfiles" / "zshrc"
        target.parent.mkdir()
        target.write_text("")
        (tmp_path / "home" / ".zshrc").symlink_to(target)
        assert _ask(f"echo x >> {target}") == ""  # lexical: the name says nothing
        assert HARNESS_PHRASE in _ask(f"echo x >> {target}", cwd=str(lab))


class TestEditAskPathsAreHarnessTargets:
    # p4-wire adds these Edit(...) asks; test_config_wiring.py maps each rule to
    # concrete spellings and requires _is_harness_target to accept them.
    RULES = [
        *(
            f"//**/{name}"
            for name in (
                ".zshrc",
                ".zshenv",
                ".zprofile",
                ".zlogin",
                ".zlogout",
                ".bashrc",
                ".bash_profile",
                ".bash_login",
                ".profile",
            )
        ),
        "~/.oh-my-zsh/**",
        "~/.pyenv/**",
    ]

    @staticmethod
    def _spellings(glob: str) -> list[str]:
        # the same mapping as tests/test_config_wiring.py::_edit_ask_paths
        if glob.endswith("/**"):
            glob = glob[: -len("/**")] + "/a"
        if glob.startswith("//**/"):
            tail = glob[len("//**/") :]
            return [f"/r/{tail}", tail]
        return [glob]

    @pytest.mark.parametrize("rule", RULES)
    def test_every_spelling_is_a_harness_target(self, rule):
        for path in self._spellings(rule):
            assert _common._is_harness_target(path), path

    @pytest.mark.parametrize(
        "path",
        [
            "~/.zshrc",
            ".zshrc",
            "~/.zshenv",
            "~/.bash_profile",
            "~/.profile",
            "~/.oh-my-zsh/custom/x.zsh",
            "/Users/x/proj/.zshrc",
        ],
    )
    def test_the_named_spellings(self, path):
        assert _common._is_harness_target(path), path


# ---------------------------------------------------------------------------
# friction: values the command builds but that resolve to unprotected paths
# ---------------------------------------------------------------------------

RESOLVED_NOT_A_WRITE_CASES = [
    "D=$(mktemp -d); cp a $D/x",
    "D=$(mktemp -d) && echo x > $D/log",
    "T=$(mktemp); echo x > $T",
    "T=`mktemp`; echo x > $T",
    "D=$(mktemp -d -t foo); cp a $D/x",
    "D=$(mktemp -p /tmp); cp a $D",
    "D=$(mktemp --tmpdir=/tmp x.XXXX); cp a $D",
    "D=$(mktemp /tmp/x.XXXX); cp a $D",
    "D=$(mktemp /private/tmp/x.XXXX); cp a $D",
    "D=$(mktemp -q -u); cp a $D",
    "S=/tmp/claude-$(whoami)/x; echo x > $S/log",
    "S=/tmp/$(id -un)/x; cp a $S",
    "U=$(logname); echo x > /tmp/$U/x",
    "S=/tmp/$USER; cp a $S/b",
    "WT=/tmp/w; for f in a b c; do cp x $WT/$f; done",
    "for f in 'a b' c; do echo x > /tmp/$f; done",
    "for d in ~/x ~/y; do cp a $d; done",
    "S=$PWD/out; echo x > $S",
    "S=$(pwd); cp a $S/b",
    # a redirection or a printf without -v names no variable
    "read x < $f; D=$(mktemp -d); cp a $D/x",
    "printf '%s' \"$x\"; D=$(mktemp -d); cp a $D/x",
]

RESOLVED_STILL_ASKED_CASES = [
    # the resolved value is protected
    "D=$(mktemp ~/.local/bin/x.XXXX); cp a $D",
    "D=$(mktemp -p ~/.claude/hooks); cp a $D",
    "for f in .claude/hooks/a.py b; do cp x $f; done",
    "S=/x/$(whoami); for d in ~/.local/bin; do cp a $d/csb; done",
    # not resolvable: a relative or unread template, a glob or expansion list
    "D=$(mktemp ./x.XXXX); cp a $D",
    "D=$(mktemp --weird); cp a $D",
    "D=$(mktemp a b); cp x $D",
    "D=$(mktemp -p rel); cp x $D",
    "D=$(mktemp -p /opt/x); cp a $D",
    "for f in *.py; do cp x $f; done",
    "for f in $(ls); do cp x $f; done",
    "for f in a b c d e f g h i; do cp x $f; done",  # more values than the limit
    "for f; do cp x $f; done",
    "S=$PWD/$X; cp a $S",
    # shlex reads `D=$(mktemp "$X` as a command run from a variable: every variable
    # stays unknown after it, as before
    'D=$(mktemp "$TMPDIR/x.XXXX"); cp a $D',
    # a cd through a newly resolved value stays unknown, as it was before
    "D=$(mktemp -d); cd $D && cp a b",
    'cd "$(mktemp -d)" && cp a b',
    "S=/tmp/$(whoami); cd $S && cp a b",
    "for d in a b; do cd $d && cp x y; done",
    "S=$PWD; cd $S/x && cp a b",
    # the command changes what the resolution assumes: TMPDIR, the lookup of the
    # executable, the user, or PWD
    "TMPDIR=~/.claude/hooks; D=$(mktemp -d); cp a $D/x",
    "export TMPDIR=~/.local/bin; T=$(mktemp); echo x > $T",
    "TMPDIR=~/.claude/hooks; D=$(mktemp -d -t foo); cp a $D/x",
    "TMPDIR=~/.claude/hooks D=$(mktemp -d); cp a $D/x",
    "export TMPDIR=~/.claude/hooks; D=$(mktemp -p ${TMPDIR:-/tmp}); cp a $D",
    "read TMPDIR; D=$(mktemp -d); cp a $D/x",
    "declare TMPDIR=~/.claude/hooks; D=$(mktemp -d); cp a $D/x",
    "printf -v TMPDIR %s ~/.claude/hooks; D=$(mktemp -d); cp a $D/x",
    "cd ~/.claude && TMPDIR=hooks; D=$(mktemp -d); cp a $D/x",
    "env TMPDIR=~/.claude/hooks sh -c 'D=$(mktemp -d); cp a $D/x'",
    "alias mktemp='echo ~/.claude/hooks/x'; D=$(mktemp); cp a $D",
    "alias pwd='echo ~/.claude/hooks'; S=$(pwd); cp a $S/x",
    "PATH=/x:$PATH; D=$(mktemp -d); cp a $D/x",
    "export PATH=~/.local/bin:$PATH; D=$(mktemp -d); cp a $D/x",
    "PATH=/x:$PATH sh -c 'S=/tmp/$(whoami); cp a $S/b'",
    "enable -f x mktemp; D=$(mktemp -d); cp a $D/x",
    "hash -p /x/y mktemp; D=$(mktemp -d); cp a $D/x",
    "printf -v USER %s x; S=/tmp/$USER; cp a $S/b",
    "read PWD; S=$PWD; cp x $S/y",
    "declare PWD=~/.claude/hooks; S=$PWD; cp x $S/y",
    # the body is more than one plain command, or not the system's mktemp
    "D=$(mktemp /tmp/a;pwd); cp x $D/y",
    "D=$(mktemp /tmp/a&&pwd); cp x $D/y",
    "D=$(./mktemp -d); cp a $D/x",
    "D=$(/opt/x/mktemp -d); cp a $D/x",
    "D=$(mktemp -d 2>/dev/null); cp a $D/x",
    "D=$(mktemp ~/x.XXXX); cp a $D",
    # names split by quotes or built at run time, still read by the shell as the name
    'export TMP""DIR=~/.claude/hooks; D=$(mktemp -d); cp a $D/x',
    "export TMP\\DIR=~/.claude/hooks; D=$(mktemp -d); cp a $D/x",
    'printf -v TMP""DIR %s ~/.claude/hooks; D=$(mktemp -d); cp a $D/x',
    'read P""WD; S=$PWD; cp x $S/y',
    'P""WD=~/.claude/hooks; S=$PWD; cp x $S/y',
    "export $'TMP\\x44IR'=~/.claude/hooks; D=$(mktemp -d); cp a $D/x",
    "export ${X}DIR=~/.claude/hooks; D=$(mktemp -d); cp a $D/x",
    "read $N; D=$(mktemp -d); cp a $D/x",
    "printf -v $N %s x; D=$(mktemp -d); cp a $D/x",
    'U""SER=x; S=/tmp/$USER; cp a $S/b',
    # zsh: the path array and the tables of aliases, functions and executables
    "path+=(/x)\nD=$(mktemp -d); cp a $D/x",
    "typeset -a path=(/x)\nD=$(mktemp -d); cp a $D/x",
    "aliases[mktemp]='echo ~/.claude/hooks/x'\nD=$(mktemp); cp a $D",
    "commands[mktemp]=/x/y\nD=$(mktemp -d); cp a $D/x",
    "disable pwd\nS=$(pwd); cp a $S/x",
    "unhash mktemp\nD=$(mktemp -d); cp a $D/x",
    "autoload -U mktemp\nD=$(mktemp -d); cp a $D/x",
    # the default of ${TMPDIR:-X} is used when the shell has no TMPDIR
    "D=$(mktemp -p ${TMPDIR:-~/.claude/hooks}); cp a $D",
]


class TestResolvedValuesReduceFriction:
    @pytest.mark.parametrize("command", RESOLVED_NOT_A_WRITE_CASES)
    def test_unprotected_resolved_values_are_not_asked(self, lab, command):
        assert _ask(command, cwd=str(lab)) == "", command

    @pytest.mark.parametrize("command", RESOLVED_STILL_ASKED_CASES)
    def test_protected_or_unresolvable_values_stay_asked(self, lab, command):
        assert _ask(command, cwd=str(lab)) != "", command

    def test_a_protected_tmpdir_is_still_a_protected_path(self, lab, monkeypatch):
        monkeypatch.setenv("TMPDIR", os.path.join(os.environ["HOME"], ".claude/hooks"))
        assert HARNESS_PHRASE in _ask("T=$(mktemp); echo x > $T", cwd=str(lab))

    def test_the_pwd_of_a_protected_cwd(self, lab):
        cwd = os.path.join(os.environ["HOME"], ".claude", "hooks")
        assert HARNESS_PHRASE in _ask("S=$(pwd); cp x $S/y", cwd=cwd)
        # an unknown cwd leaves $(pwd) unknown
        assert UNRESOLVED_PHRASE in _ask("S=$(pwd); cp x $S/y", cwd="rel")

    def test_the_user_name_is_the_hook_process_user(self, monkeypatch):
        user = _common._process_user()
        assert user
        monkeypatch.setenv("HOME", f"/Users/{user}")
        for spelling in ("$(whoami)", "`whoami`", "$(logname)", "$USER", "${USER}"):
            command = f"cp x /Users/{spelling}/.codex/config.toml"
            assert HARNESS_PHRASE in _ask(command), spelling
        assert _ask("cp x /Users/$(whoami)/notes.txt") == ""
        # `$(id -un)` has a space: through an assignment, read from the raw word
        cmd = "S=/Users/$(id -un)/.codex; cp x $S/config.toml"
        assert HARNESS_PHRASE in _ask(cmd, cwd="/")

    def test_the_user_name_through_a_variable(self, lab, monkeypatch):
        user = _common._process_user()
        home = os.environ["HOME"]
        cmd = f"S={os.path.dirname(home)}/$(whoami); cp x $S/y"
        if os.path.basename(home) != user:
            # the fixture HOME is not named after the user: only the shape matters
            assert _ask(cmd, cwd=str(lab)) == ""
        cmd = "S=$(whoami); cp x ~/.codex/$S"
        assert HARNESS_PHRASE in _ask(cmd, cwd=str(lab))

    def test_without_a_user_name_nothing_is_resolved(self, lab, monkeypatch):
        monkeypatch.setattr(_common, "_process_user", lambda: "")
        assert UNRESOLVED_PHRASE in _ask("S=/tmp/$(whoami); cp a $S/b", cwd=str(lab))
        assert UNRESOLVED_PHRASE in _ask("S=/tmp/$USER; cp a $S/b", cwd=str(lab))
        assert _ask("cp x /Users/$(whoami)/.codex/x") == ""

    def test_process_user_errors_give_an_empty_name(self, monkeypatch):
        import pwd

        def boom(_uid):
            raise KeyError(_uid)

        monkeypatch.setattr(pwd, "getpwuid", boom)
        assert _common._process_user.__wrapped__() == ""

    @pytest.mark.parametrize(
        "body,expected",
        [
            ("whoami x", None),
            ("id -u", None),
            ("date", None),
            ("", None),
            ("mktemp -t", "/mktemp."),
            # an unset TMPDIR gives another path: not decided
            ("mktemp $TMPDIR/x.XXXX", None),
            ("mktemp -p ${TMPDIR:-/tmp} y.XXXX", None),
            ("mktemp -d", "/mktemp."),
            ("/usr/bin/mktemp -d", "/mktemp."),
            ("/bin/mktemp -d", "/mktemp."),
            ("   ", None),
            ("./mktemp -d", None),
            ("mktemp /tmp/a;pwd", None),
            ("mktemp '/tmp/a'", None),
        ],
    )
    def test_known_substitutions(self, monkeypatch, tmp_path, body, expected):
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        value = _common._known_substitution(body, _common._CwdContext(""))
        if expected is None:
            assert value is None
        else:
            assert value is not None and expected in value

    @pytest.mark.parametrize(
        "code,body",
        [
            ("alias x=y", "mktemp -d"),
            ("TMPDIR=/x", "mktemp -d"),
            ("USER=x", "whoami"),
            ("f() { :; }", "whoami"),
            ("sudo -u x id -un", "id -un"),
        ],
    )
    def test_no_substitution_when_the_command_changes_its_facts(self, code, body):
        assert _common._known_substitution(body, _common._CwdContext(code)) is None

    def test_a_tmpdir_default_resolves_only_when_both_values_agree(self, monkeypatch):
        root = _common._temp_roots()[0]
        monkeypatch.setenv("TMPDIR", root)
        ctx = _common._CwdContext("")
        body = "mktemp -p ${TMPDIR:-%s} y.XXXX" % root
        assert _common._known_substitution(body, ctx) == f"{root}/y.XXXX"

    def test_a_protected_tmpdir_default_is_asked(self, lab, monkeypatch):
        hooks = os.path.join(os.environ["HOME"], ".claude", "hooks")
        cmd = f"D=$(mktemp -p ${{TMPDIR:-{hooks}}}); cp a $D"
        monkeypatch.delenv("TMPDIR", raising=False)
        assert _ask(cmd, cwd=str(lab)) != ""
        monkeypatch.setenv("TMPDIR", _common._temp_roots()[0])
        assert _ask(f"env -i sh -c '{cmd}'", cwd=str(lab)) != ""

    def test_an_unterminated_substitution_is_left_alone(self):
        ctx = _common._CwdContext("")
        assert _common._substitute_known("x$(mktemp", ctx) == "x$(mktemp"

    def test_cooking_keeps_expansions_and_drops_quotes(self):
        cook = _common._cook_word
        assert cook('"$(mktemp -d)"/x') == "$(mktemp -d)/x"
        assert cook("'a b'\\ c") == "a b c"
        assert cook('"a\\$b\\q"') == "a$b\\q"
        assert cook("${x:-y}z") == "${x:-y}z"


class TestNoFilesystemWithoutACwd:
    @pytest.mark.parametrize(
        "command",
        [
            "ln -s ~/.local d && cp X d/bin/csb",
            "mv ~/.local /tmp/o",
            "CP=cp; $CP X ~/.local/bin/csb",
            "$CP X csb",
            'cp X "$PWD/csb"',
            "cd ~/.local/bin && cp X .",
            "cp X /net/host/x",
            "eval cd x; cp a b",
        ],
    )
    def test_cwd_none_never_touches_the_filesystem(self, lab, monkeypatch, command):
        def boom(*_a, **_k):
            raise AssertionError("filesystem access with cwd=None")

        monkeypatch.setattr(_common.os.path, "realpath", boom)
        monkeypatch.setattr(_common.os, "lstat", boom)
        _ask(command)
