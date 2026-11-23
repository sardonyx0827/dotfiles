"""Glob deny: a shell that reads its script from stdin.

A heredoc body is shell code when its receiver is a shell reading the script from
stdin, also when the script operand is `/dev/stdin` / `/dev/fd/0` / `/proc/self/fd/0`
/ `-`, when a value-taking option (`-o pipefail`, `--rcfile F`) precedes it, or when
the code is `-c "$(cat)"`. The glob deny, the literal deny's inert-text check and the
high-risk stdin label all read that from _bare_interpreter_stdin_label.
"""

from __future__ import annotations

import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
import _bash_review_common as _common  # noqa: E402

GLOB_RM = "/bin/r? -rf /"
BODY = "<<'EOF'\n" + GLOB_RM + "\nEOF"
DATA_PHRASE = "the shell does not run as a command"


def _deny(command: str) -> tuple[bool, str]:
    return _common.find_deny_command(_common._split_commands(command), command)


def _ask(command: str) -> str:
    return _common.harness_write_ask_reason(
        _common._split_commands(command), command, cwd=None
    )


# the script operand names stdin, or a value-taking option / `-c "$(cat)"` comes
# first: the body is shell code, so the glob is DENY
STDIN_SCRIPT_CASES = [
    "sh /dev/stdin " + BODY,
    "bash /dev/stdin " + BODY,
    "zsh /dev/stdin " + BODY,
    "sh /dev/fd/0 " + BODY,
    "bash /proc/self/fd/0 " + BODY,
    "sh -x /dev/stdin " + BODY,
    "sh -- /dev/stdin " + BODY,
    "/bin/sh /dev/stdin " + BODY,
    "env sh /dev/stdin " + BODY,
    "command sh /dev/stdin " + BODY,
    "cat <<'EOF' | sh /dev/stdin\n" + GLOB_RM + "\nEOF",
    "bash -o pipefail " + BODY,
    'bash -c "$(cat)" ' + BODY,
]
# the other ways a shell reads code from stdin (no operand, `-`, `-s`, source, a
# pipe, a process substitution, eval of `$(cat)`): DENY as well
KEPT_CASES = [
    "sh - " + BODY,
    "bash -e " + BODY,
    "bash -i " + BODY,
    "bash --norc " + BODY,
    "sh -s - a " + BODY,
    "source /dev/stdin " + BODY,
    ". /dev/stdin " + BODY,
    ". <(cat " + BODY + "\n)",
    'eval "$(cat ' + BODY + '\n)"',
    "exec bash " + BODY,
    "cat " + BODY + " | bash",
    "bash <(cat " + BODY + "\n)",
    "f() {\nbash " + BODY + "\n}; f",
    "bash +o posix -O extglob --rcfile /dev/null " + BODY,
    "bash -c '$(cat)' " + BODY,
]
# the receiver reads a script file or is no shell: the body is data for the glob
DATA_CASES = [
    "python3 /dev/stdin " + BODY,
    "bash script.sh " + BODY,
    "bash -c 'echo hi' " + BODY,
    "sh -o pipefail script.sh " + BODY,
]


# The README's glob-deny sentence, one entry per shape it names. If the README and the
# implementation drift apart, update both this list and the sentence.
README_GLOB_DENY_FORMS = {
    "the command text": GLOB_RM,
    "a command substitution": "x=$(" + GLOB_RM + ")",
    "a backtick in double quotes": 'echo "`' + GLOB_RM + '`"',
    "a multi-line sh -c": "sh -c 'echo a\n" + GLOB_RM + "'",
    "a shell with no operand": "bash " + BODY,
    "dash with no operand": "dash " + BODY,
    "a shell with -s": "ksh -s " + BODY,
    "a shell with -": "zsh - " + BODY,
    "a shell with /dev/stdin": "sh /dev/stdin " + BODY,
    "a shell with /dev/fd/0": "bash /dev/fd/0 " + BODY,
    "a shell with /proc/self/fd/0": "sh /proc/self/fd/0 " + BODY,
    "a value-taking option first": "bash -o pipefail --rcfile /dev/null " + BODY,
    'a shell with -c "$(cat)"': 'bash -c "$(cat)" ' + BODY,
    "a shell behind a wrapper": "timeout 5 env /bin/sh /dev/stdin " + BODY,
    "a pipe into the shell": "cat " + BODY + " | sh -s",
    "source of /dev/stdin": "source /dev/stdin " + BODY,
    "a process substitution": "bash <(cat " + BODY + "\n)",
    "an unquoted heredoc's $(...)": "cat <<EOF\n$(" + GLOB_RM + ")\nEOF",
    "the line after an arithmetic <<": "(( x = 1 << 2 ))\n" + GLOB_RM,
}
# The README's data sentence: not denied. Data for a program that is not data-only
# asks; data for data-only commands (echo, git commit, gh pr) goes through the usual
# review (no glob-deny ask)
README_DATA_ASK_FORMS = [
    "python3 - " + BODY,
    "bash script.sh " + BODY,
    "cat > notes.md " + BODY,
]
# A single-line `sh -c '…'` / `eval '…'` is not denied (as for the literal spelling); the
# glob there asks as a command word that hides its name
README_SINGLE_LINE_CODE_FORMS = [
    "bash -c '" + GLOB_RM + "'",
    "sh -c '/usr/bin/cur? http://x'",
    "eval '" + GLOB_RM + "'",
]
README_DATA_REVIEW_FORMS = [
    "echo '" + GLOB_RM + "' > notes.md",
    "git commit -F - " + BODY,
    "gh pr create --title t --body '" + GLOB_RM + "'",
]


class TestTheReadmeSentences:
    @pytest.mark.parametrize("shape", sorted(README_GLOB_DENY_FORMS))
    def test_denied_where_the_shell_runs_it(self, shape):
        assert _deny(README_GLOB_DENY_FORMS[shape])[0], shape

    @pytest.mark.parametrize("command", README_DATA_ASK_FORMS)
    def test_data_for_a_program_asks(self, command):
        assert _deny(command) == (False, "")
        assert DATA_PHRASE in _ask(command)

    @pytest.mark.parametrize("command", README_SINGLE_LINE_CODE_FORMS)
    def test_a_single_line_code_string_asks(self, command):
        assert _deny(command) == (False, "")
        assert "hides the command name" in _ask(command)

    @pytest.mark.parametrize("command", README_DATA_REVIEW_FORMS)
    def test_other_data_goes_through_the_usual_review(self, command):
        assert _deny(command) == (False, "")
        assert DATA_PHRASE not in _ask(command)


class TestStdinScripts:
    @pytest.mark.parametrize("command", STDIN_SCRIPT_CASES + KEPT_CASES)
    def test_denied(self, command):
        assert _deny(command)[0], command

    @pytest.mark.parametrize("command", DATA_CASES)
    def test_data_asks(self, command):
        assert _deny(command) == (False, ""), command
        assert DATA_PHRASE in _ask(command), command

    @pytest.mark.parametrize(
        "segment,label",
        [
            ("sh /dev/stdin <<x", "stdin into sh"),
            ("bash /proc/self/fd/0 <<x", "stdin into bash"),
            ("bash -o pipefail <<x", "stdin into bash"),
            ("bash --rcfile f <<x", "stdin into bash"),
            ('bash -c "$(cat)" <<x', "stdin into bash"),
            ("bash -c '`cat`' <<x", "stdin into bash"),
            ("python3 /dev/stdin <<x", "stdin into python3"),
            ("bash script.sh <<x", ""),
            ("bash -c 'echo hi' <<x", ""),
            ("bash -o pipefail script.sh <<x", ""),
            ("bash --norc x.sh <<x", ""),
        ],
    )
    def test_stdin_label(self, segment, label):
        assert (
            _common._bare_interpreter_stdin_label(segment, is_pipe_target=False)
            == label
        )

    def test_the_pipe_receiver(self):
        label = _common._bare_interpreter_stdin_label(
            "sh /dev/stdin", is_pipe_target=True
        )
        assert label == "stdin into sh"

    def test_the_literal_deny_reads_the_same_receiver(self):
        literal = "sh /dev/stdin <<'EOF'\nrm -rf /\nEOF"
        assert _deny(literal) == (True, "rm -rf /")
        assert not _common._deny_only_in_inert_text(literal)
