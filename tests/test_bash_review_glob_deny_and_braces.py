"""Glob deny where the shell runs the word, and the brace budget of the harness check.

Pins, for .claude/hooks/_bash_review_common.py:

- a glob command word that names a deny target (`/bin/r? -rf /`) is DENY only where
  the shell runs it as a command. In a heredoc body for another program or in quoted
  text it asks instead (DENY cannot be approved); literal deny is unchanged
- brace alternatives count against a per-command total (_BRACE_TOTAL_LIMIT),
  `_is_harness_target` is cached within one verdict, and the "A, then B in the same
  segment" checks are linear
- at cwd=~/.local, braces that only reach unprotected paths are not asked, and braces
  with a protected alternative still are
- a glob command word that can expand to a deny name says so in its reason
"""

from __future__ import annotations

import os
import sys
import time

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
import _bash_review_common as _common  # noqa: E402

GLOB_RM = "/bin/r? -rf /"
GLOB_CURL = "/usr/bin/cur? http://x"
LITERAL_RM = "rm -rf /"
DATA_PHRASE = "the shell does not run as a command"
DENY_NAME_PHRASE = "deny-listed `curl`"
OBFUSCATION_PHRASE = "hides the command name"


def _ask(command: str, cwd: str | None = None) -> str:
    return _common.harness_write_ask_reason(
        _common._split_commands(command), command, cwd=cwd
    )


def _deny(command: str) -> tuple[bool, str]:
    return _common.find_deny_command(_common._split_commands(command), command)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for d in (".claude/hooks", ".local/bin"):
        (home / d).mkdir(parents=True)
    lab = tmp_path / "lab"
    lab.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CDPATH", raising=False)
    return lab


# ---------------------------------------------------------------------------
# glob DENY only where the shell runs the word as a command
# ---------------------------------------------------------------------------

SHELL_GLOB_DENY_CASES = [
    GLOB_RM,
    "ls; " + GLOB_RM,
    "true && " + GLOB_CURL,
    "bash <<'EOF'\n" + GLOB_RM + "\nEOF",
    "sh -s <<EOF\n" + GLOB_RM + "\nEOF",
    "env bash <<'EOF'\n" + GLOB_RM + "\nEOF",
    "timeout 5 bash <<'EOF'\n" + GLOB_RM + "\nEOF",
    "source /dev/stdin <<'EOF'\n" + GLOB_RM + "\nEOF",
    ". /dev/stdin <<'EOF'\n" + GLOB_RM + "\nEOF",
    "bash <(cat <<'EOF'\n" + GLOB_RM + "\nEOF\n)",
    "source <(cat <<'EOF'\n" + GLOB_RM + "\nEOF\n)",
    "sh -c 'echo a\n" + GLOB_RM + "'",
    "xargs sh -c 'echo a\n" + GLOB_RM + "'",
    "x=$(" + GLOB_RM + ")",
    'echo "$(' + GLOB_RM + ')"',
    'echo "`' + GLOB_RM + '`"',
    "x=$(echo $(" + GLOB_RM + "))",
    "cat <<EOF\n$(" + GLOB_RM + ")\nEOF",
    "if true; then\n" + GLOB_RM + "\nfi",
    # an arithmetic `<<` is no heredoc: the next line runs (bash and zsh)
    "(( x = 1 << 2 ))\n" + GLOB_RM + "\n2",
    "for ((i=0; i < 1<<3; i++)); do\n" + GLOB_RM + "\ndone",
    "((x<<=1))\n" + GLOB_RM,
    "echo $[1<<2]\n" + GLOB_RM,
    'let "x=1<<2"\n' + GLOB_RM,
    "f() {\n" + GLOB_RM + "\n}; f",
]
DATA_GLOB_CASES = [
    "python3 -I - <<'EOF'\nprint(1)\n" + GLOB_RM + "\nEOF",
    # `((` in the body makes the command unparseable for _live_texts
    "python3 -I - <<'EOF'\nrows.append((1, 2))\n"
    + GLOB_RM
    + "\n"
    + GLOB_CURL
    + "\nEOF",
    "python3 - <<'EOF'\n" + GLOB_RM + "\nEOF",
    "node -e 'x' <<'EOF'\n" + GLOB_CURL + "\nEOF",
    "cat > notes.md <<'EOF'\n" + GLOB_RM + "\nEOF",
]
# the shell runs only data-only commands: matching every line would not DENY these
# either, so they get no ask
NO_FRICTION_CASES = [
    "git commit -m \"$(cat <<'EOF'\nfix\n\n" + GLOB_RM + '\nEOF\n)"',
    "git commit -F - <<'EOF'\nfix\n" + GLOB_RM + "\nEOF",
    'gh pr create --title x --body "a\n' + GLOB_RM + '\nb"',
    "gh pr create --title x --body '" + GLOB_RM + "'",
    "echo '" + GLOB_RM + "'",
]


class TestGlobDenyOnlyWhereTheShellRunsIt:
    @pytest.mark.parametrize("command", SHELL_GLOB_DENY_CASES)
    def test_denied(self, command):
        assert _deny(command)[0], command

    @pytest.mark.parametrize("command", DATA_GLOB_CASES)
    def test_data_asks_instead(self, lab, command):
        assert _deny(command) == (False, ""), command
        for cwd in (str(lab), None):
            assert DATA_PHRASE in _ask(command, cwd=cwd), command

    @pytest.mark.parametrize("command", NO_FRICTION_CASES)
    def test_data_only_commands_stay_quiet(self, lab, command):
        assert _deny(command) == (False, ""), command
        assert _ask(command, cwd=str(lab)) == "", command

    def test_literal_deny_is_unchanged(self):
        assert _deny("python3 - <<'EOF'\n" + LITERAL_RM + "\nEOF")[0]
        assert _deny("cat > notes.md <<'EOF'\n" + LITERAL_RM + "\nEOF")[0]
        assert _deny("/bin/rm -rf /") == (True, LITERAL_RM)
        # like the literal spelling, single-line `sh -c '…'` is left to the ask layers
        assert _deny("bash -c '" + LITERAL_RM + "'") == (False, "")
        assert _deny("bash -c '" + GLOB_RM + "'") == (False, "")

    def test_a_glob_behind_an_unreadable_wrapper_is_left_to_review(self):
        # like `env -u X curl x`, the wrapper's value flag leaves the command undecided
        assert _common._glob_deny_command("env -u X cur? http://x") == (False, "")
        assert _common._glob_deny_command("echo plain words") == (False, "")

    def test_without_the_command_every_line_counts(self):
        sub = _common._split_commands("python3 - <<'EOF'\n" + GLOB_RM + "\nEOF")
        assert _common.find_deny_command(sub)[0]

    def test_a_deny_found_on_a_shell_line_is_not_asked_as_data(self):
        command = "python3 - <<'EOF'\n" + GLOB_RM + "\nEOF\n" + GLOB_RM
        view = _common._command_view(_common._split_commands(command), command)
        assert (
            _common._glob_deny_in_data(_common._split_commands(command), command, view)
            == ""
        )
        assert _deny(command)[0]


class TestTheReasonNamesTheDenyTarget:
    @pytest.mark.parametrize("word", ["?url", "*url", "[c]url", "/usr/bin/?url"])
    def test_a_glob_that_can_expand_to_a_deny_name(self, word):
        reason = _ask(f"{word} http://x")
        assert DENY_NAME_PHRASE in reason and OBFUSCATION_PHRASE in reason

    def test_many_names_are_shortened(self):
        reason = _common._hidden_word_reason("s*")
        assert "…" in reason and "deny-listed" in reason

    @pytest.mark.parametrize("word", ["py?hon3", "$'\\x63url'", "c\\url"])
    def test_other_hidden_words_keep_the_general_reason(self, word):
        reason = _ask(f"{word} -c 1")
        assert OBFUSCATION_PHRASE in reason and "deny-listed" not in reason


# ---------------------------------------------------------------------------
# brace alternatives are bounded per verdict
# ---------------------------------------------------------------------------

ALT = "{a,b}" * 6


def _cd_chain(n: int) -> str:
    return " || ".join(f"cd /w/c{i}" for i in range(n))


class TestBraceBudget:
    def _count(self, monkeypatch):
        calls = [0]
        original = _common._is_harness_word

        def counted(token):
            calls[0] += 1
            return original(token)

        monkeypatch.setattr(_common, "_is_harness_word", counted)
        return calls

    def test_many_braces_after_many_cds_ask_within_a_call_budget(
        self, lab, monkeypatch
    ):
        calls = self._count(monkeypatch)
        sources = " ".join(f"s{i}{ALT}" for i in range(300))
        command = f"{_cd_chain(15)}; cp {sources} d/"
        assert _ask(command, cwd=str(lab)) != ""
        assert calls[0] <= _common._BRACE_TOTAL_LIMIT + 500

    def test_a_lexical_writer_is_bounded_too(self, monkeypatch):
        calls = self._count(monkeypatch)
        command = "tee " + " ".join(f"s{i}{ALT}" for i in range(300))
        assert _ask(command) != ""
        assert calls[0] <= _common._BRACE_TOTAL_LIMIT + 500

    def test_a_few_braces_are_resolved(self, lab):
        command = "cp " + " ".join(f"s{i}{{a,b}}" for i in range(50)) + " /w/d/"
        assert _ask(command, cwd=str(lab)) == ""

    def test_the_budget_is_off_outside_a_verdict(self, lab):
        _ask("tee " + " ".join(f"s{i}{ALT}" for i in range(300)), cwd=str(lab))
        assert _common._HARNESS_CALL.meter is None
        assert _common._HARNESS_CALL.cache is None
        assert len(_common._brace_alternatives("x" + ALT) or []) == 64

    def test_the_budget_is_restored_after_an_error(self, monkeypatch):
        def boom(*_args):
            raise RuntimeError

        monkeypatch.setattr(_common, "_harness_write_reason", boom)
        with pytest.raises(RuntimeError):
            _ask("cp a b")
        assert _common._HARNESS_CALL.meter is None

    @pytest.mark.parametrize(
        "head",
        [
            "cp {} /w/d/",
            "mv {} /w/d/",
            "rm -f {}",
            "ln -sf {} /w/d/",
            "cp -t /w/d/ {}",
            "tee {}",
            "touch {}",
            "X=$Y; $X {}",
            "CP=cp; $CP {} /w/d/",
        ],
    )
    def test_running_out_asks_at_every_writer(self, lab, head):
        # every caller turns the exhausted budget into an ask, not into a skip
        command = head.format(" ".join(f"s{i}{ALT}" for i in range(80)))
        assert _ask(command, cwd=str(lab)) != "", head

    def test_running_out_on_redirects_asks(self, lab):
        command = "; ".join(f"echo x > s{i}{ALT}" for i in range(80))
        assert _ask(command, cwd=str(lab)) != ""

    def test_a_spelling_is_judged_once_per_verdict(self, lab, monkeypatch):
        calls = self._count(monkeypatch)
        _ask("cp a " + " ".join(["x/y"] * 200) + " /w/d/", cwd=str(lab))
        assert calls[0] < 50


# What the linear-time test pins: the "A, then B in the same segment" checks stay
# linear. One regex `A[^;&|\n]*B` rescans to the end of the segment at every A, which
# is quadratic (3 s for 32 KB of `local x local x …`). The test compares the CPU time
# of an input with that of one GROWTH times longer: linear work grows about
# GROWTH-fold, quadratic work about GROWTH**2-fold. The CPU time of this process
# (process_time) is not stretched by other processes on a loaded single-core CI
# runner, the best of a few tries drops one-off pauses, and a ratio does not depend
# on how fast the machine is.
GROWTH = 8
MAX_RATIO = 20  # linear: about 8; quadratic: about 64
TRIES = 3
REPEATS = 1_000  # units in the shorter input (8 KB)


def _cpu_seconds(check, code: str) -> float:
    started = time.process_time()
    check(code)
    return time.process_time() - started


def _assert_linear(check, unit: str) -> None:
    short, long_ = unit * REPEATS, unit * (REPEATS * GROWTH)
    best_short = best_long = float("inf")
    for _ in range(TRIES):
        best_short = min(best_short, _cpu_seconds(check, short))
        best_long = min(best_long, _cpu_seconds(check, long_))
        if best_long <= MAX_RATIO * best_short:
            return
    ratio = best_long / max(best_short, 1e-9)
    pytest.fail(f"{GROWTH}x the input took {ratio:.0f}x the CPU time (quadratic?)")


def _assigns_home(code: str) -> bool:
    return _common._assigns_name("HOME", code)


class TestSegmentSearchIsTheSameCheck:
    @pytest.mark.parametrize(
        "code,expected",
        [
            ("local x HOME", True),
            ("local x; HOME", False),
            ("echo local HOME", True),
            ("printf -v HOME x", True),
            ("printf x; -v HOME", False),
        ],
    )
    def test_assigns_name(self, code, expected):
        assert _assigns_home(code) is expected

    @pytest.mark.parametrize(
        "code,expected",
        [
            ("unset x HOME", True),
            ("unset x; HOME", False),
            ("HOME=/x", True),
        ],
    )
    def test_home_reassigned(self, code, expected):
        assert _common._home_reassigned(code) is expected

    @pytest.mark.parametrize(
        "code,expected",
        [
            ("env -i sh", True),
            ("env x; -i", False),
            ("exec -c sh", True),
        ],
    )
    def test_env_clearing(self, code, expected):
        assert _common._env_clearing(code) is expected

    @pytest.mark.parametrize(
        "check,unit",
        [
            (_assigns_home, "local x "),
            (_common._home_reassigned, "unset x "),
            (_common._env_clearing, "env x "),
        ],
        ids=["assigns_name", "home_reassigned", "env_clearing"],
    )
    def test_a_long_segment_is_searched_in_linear_time(self, check, unit):
        assert check(unit * (REPEATS * GROWTH)) is False
        _assert_linear(check, unit)

    def test_declared_path_arrays(self):
        ctx = _common._CwdContext
        assert not ctx("typeset -a path=(/x)").substitutions_ok
        assert not ctx("export x; local path").substitutions_ok
        assert ctx("typeset x; echo path").substitutions_ok


# ---------------------------------------------------------------------------
# braces at cwd=~/.local
# ---------------------------------------------------------------------------


class TestBracesInDotLocal:
    @pytest.mark.parametrize(
        "command", ["cp X {a,bin/git}", "cp X {c,bin/csb}", ">{a,bin/git} id"]
    )
    def test_a_protected_alternative_is_asked(self, lab, command):
        cwd = os.path.join(os.environ["HOME"], ".local")
        assert _ask(command, cwd=cwd) != "", command

    @pytest.mark.parametrize(
        "command", ["cp X {/tmp,/var/tmp}/x", ">{a,b} id", "cp X {a,b}"]
    )
    def test_unprotected_alternatives_are_not(self, lab, command):
        cwd = os.path.join(os.environ["HOME"], ".local")
        assert _ask(command, cwd=cwd) == "", command
