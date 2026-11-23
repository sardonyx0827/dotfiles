"""Tests for _hook_common.sh (hook_log / hook_notify / hook_git_changed_files).

These run against the .claude copy; .codex holds no copy or symlink of it and
instead resolves ../../.claude/hooks by physical path at runtime, which
test_hook_sync.py pins.
"""

import os
import re
import shutil
import subprocess
import time

import pytest
from conftest import REPO_ROOT, run_git

HOOK_COMMON = REPO_ROOT / ".claude/hooks/_hook_common.sh"

TIMESTAMP = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] ")


def _run(script: str, *args: str, env: dict | None = None):
    return subprocess.run(
        ["bash", "-c", f'. "{HOOK_COMMON}"\n{script}', "bash", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


class TestHookLog:
    def test_appends_with_a_timestamp(self, tmp_path):
        log = tmp_path / "x.log"
        res = _run(f'hook_log "{log}" "hello world"')
        assert res.returncode == 0
        line = log.read_text(encoding="utf-8").strip()
        assert TIMESTAMP.match(line)
        assert line.endswith("hello world")

    def test_rotates_at_the_cap(self, tmp_path):
        log = tmp_path / "x.log"
        log.write_text("".join(f"old {i}\n" for i in range(80)), encoding="utf-8")
        res = _run(
            f'hook_log "{log}" "newest"',
            env={"HOOK_LOG_MAX_LINES": "20", "PATH": "/usr/bin:/bin"},
        )
        assert res.returncode == 0
        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 20
        assert lines[-1].endswith("newest"), "the newest line must survive rotation"

    def test_leaves_no_temp_files_behind(self, tmp_path):
        log = tmp_path / "x.log"
        log.write_text("".join(f"old {i}\n" for i in range(30)), encoding="utf-8")
        _run(
            f'hook_log "{log}" "msg"',
            env={"HOOK_LOG_MAX_LINES": "10", "PATH": "/usr/bin:/bin"},
        )
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "x.log"]
        assert leftovers == [], f"rotation left temp files behind: {leftovers}"

    def test_unwritable_log_does_not_fail_the_caller(self, tmp_path):
        # Logging is incidental; it must never decide a hook's outcome.
        res = _run(f'hook_log "{tmp_path}/nope/x.log" "msg"; echo "rc=$?"')
        assert "rc=0" in res.stdout

    def test_concurrent_rotation_keeps_the_log_and_stays_quiet(self, tmp_path):
        """Rotation must not use a temp file shared by every process.

        Two hooks rotating at once (a Claude and a Codex session, or several
        files in one turn) would open the same temp file and clobber each
        other's copy, collapsing the log far below its cap. The loser of the
        `mv` race would also print "No such file or directory" to stderr --
        which for lint.sh is the channel its findings are fed back to the
        model on. See the comment on the rotation in `hook_log`
        (_hook_common.sh).
        """
        log = tmp_path / "race.log"
        cap = 50
        log.write_text("".join(f"seed {i}\n" for i in range(60)), encoding="utf-8")
        script = f"""
        for w in 1 2 3 4 5 6; do
          ( for i in $(seq 1 25); do hook_log "{log}" "worker$w msg$i" >/dev/null; done ) &
        done
        wait
        """
        res = _run(
            script, env={"HOOK_LOG_MAX_LINES": str(cap), "PATH": "/usr/bin:/bin"}
        )

        assert res.returncode == 0
        assert res.stderr == "", f"rotation leaked to stderr: {res.stderr!r}"

        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= cap
        # Concurrent rotations that truncate each other's snapshot collapse
        # the log far below the cap.
        assert len(lines) >= cap * 0.8, (
            f"log collapsed to {len(lines)} lines under a {cap}-line cap"
        )
        assert not any(p.name.startswith("race.log.") for p in tmp_path.iterdir()), (
            "concurrent rotation left temp files behind"
        )


def _init_unborn_repo(path):
    """`git init` with no commit yet: HEAD does not resolve."""
    path.mkdir()
    run_git(path, "init", "-q", "-b", "main")
    run_git(path, "config", "user.email", "test@example.com")
    run_git(path, "config", "user.name", "Test User")
    run_git(path, "config", "commit.gpgsign", "false")
    return path


def _changed_files(repo, *, cwd=None) -> list[str]:
    """What hook_git_changed_files prints, split on NUL.

    Read as bytes: the contract is NUL separation, which text mode with
    universal newlines and a command substitution would both blur. A correct
    run ends in a NUL, so the empty tail the split leaves is asserted, not
    silently dropped.
    """
    res = subprocess.run(
        [
            "bash",
            "-c",
            f'. "{HOOK_COMMON}"\nhook_git_changed_files "$1"; echo "rc=$?" >&2',
            "bash",
            str(repo),
        ],
        capture_output=True,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
        cwd=cwd,
        timeout=60,
    )
    assert res.stderr == b"rc=0\n", res.stderr
    if not res.stdout:
        return []
    assert res.stdout.endswith(b"\0"), f"not NUL-terminated: {res.stdout!r}"
    return [p.decode("utf-8") for p in res.stdout.split(b"\0")[:-1]]


class TestHookGitChangedFiles:
    """The "which files did this turn touch" selection the Stop hooks share.

    Repo-root-relative, NUL-separated: tracked changes against HEAD first, then
    untracked files. Before the first commit HEAD does not resolve, so the first
    half has to come from the index instead -- otherwise a file staged for that
    first commit is in neither half and every consumer silently skips it.
    """

    def test_born_repo_lists_modified_staged_and_untracked(self, git_repo):
        (git_repo / "README.md").write_text("changed\n", encoding="utf-8")
        (git_repo / "staged.sh").write_text("echo 1\n", encoding="utf-8")
        run_git(git_repo, "add", "staged.sh")
        (git_repo / "new.sh").write_text("echo 2\n", encoding="utf-8")

        assert _changed_files(git_repo) == ["README.md", "staged.sh", "new.sh"]

    def test_unborn_repo_lists_staged_and_untracked(self, tmp_path):
        repo = _init_unborn_repo(tmp_path / "repo")
        (repo / "staged.sh").write_text("echo 1\n", encoding="utf-8")
        run_git(repo, "add", "staged.sh")
        (repo / "untracked.sh").write_text("echo 2\n", encoding="utf-8")

        assert _changed_files(repo) == ["staged.sh", "untracked.sh"]

    def test_unborn_repo_without_changes_lists_nothing(self, tmp_path):
        repo = _init_unborn_repo(tmp_path / "repo")

        assert _changed_files(repo) == []

    def test_clean_born_repo_lists_nothing(self, git_repo):
        assert _changed_files(git_repo) == []

    def test_unborn_unstaged_edit_of_a_staged_file_is_listed_once(self, tmp_path):
        # Staged, then edited again: the index holds one version and the tree
        # another, but it is still one path. A duplicate would double-process it.
        repo = _init_unborn_repo(tmp_path / "repo")
        (repo / "a.sh").write_text("echo 1\n", encoding="utf-8")
        run_git(repo, "add", "a.sh")
        (repo / "a.sh").write_text("echo 2\n", encoding="utf-8")

        assert _changed_files(repo) == ["a.sh"]

    def test_paths_survive_newlines_quotes_and_non_ascii(self, tmp_path):
        # core.quotePath would hand back `"evil\".sh"` / `"\346..."`, which cannot
        # be opened; a newline would split one path in two. -z plus NUL output
        # keeps every one of them intact.
        repo = _init_unborn_repo(tmp_path / "repo")
        names = ["a\nb.sh", 'evil".sh', "日本語.sh"]
        for name in names:
            (repo / name).write_text("echo 1\n", encoding="utf-8")
        run_git(repo, "add", "-A")

        assert sorted(_changed_files(repo)) == sorted(names)

    def test_is_independent_of_the_callers_cwd(self, git_repo, tmp_path):
        # Paths are relative to the repo root however deep the hook was started.
        (git_repo / "sub").mkdir()
        (git_repo / "sub" / "x.sh").write_text("echo 1\n", encoding="utf-8")

        assert _changed_files(git_repo, cwd=git_repo / "sub") == ["sub/x.sh"]
        assert _changed_files(git_repo, cwd=tmp_path) == ["sub/x.sh"]

    def test_a_directory_that_is_not_a_repo_lists_nothing_and_succeeds(self, tmp_path):
        outside = tmp_path / "plain"
        outside.mkdir()
        (outside / "x.sh").write_text("echo 1\n", encoding="utf-8")

        assert _changed_files(outside) == []


# ---------------------------------------------------------------------------
# hook_run_timeout: portable timeout (macOS ships neither timeout nor gtimeout)
# ---------------------------------------------------------------------------


def _tool_dir(tmp_path, *, tools=("bash", "sleep", "mktemp", "rm", "pgrep", "cat")):
    """A PATH holding only symlinks to `tools`, so timeout/gtimeout are absent.

    Building the PATH up (rather than hiding entries from the real one) keeps
    the test independent of whether the host ships GNU timeout, and of what
    else lives next to it.
    """
    bindir = tmp_path / "toolbin"
    bindir.mkdir(exist_ok=True)
    for name in tools:
        real = shutil.which(name)
        if real and not (bindir / name).exists():
            (bindir / name).symlink_to(real)
    return bindir


def _real_timeout() -> str:
    real = shutil.which("timeout") or shutil.which("gtimeout")
    if real is None:
        pytest.skip("no GNU / BSD timeout on this host")
    return real


def _run_timeout(tmp_path, script: str, *, extra=None, real_timeout=False, args=()):
    """Run `script` after sourcing _hook_common.sh on a PATH with only `_tool_dir`.

    `args` become $0, $1, ... of the script. `real_timeout` adds the host's
    GNU / BSD timeout (the test is skipped when there is none).
    """
    bindir = _tool_dir(tmp_path)
    if real_timeout:
        (bindir / "timeout").symlink_to(_real_timeout())
    for name, body in (extra or {}).items():
        stub = bindir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    env = {"PATH": str(bindir), "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    started = time.monotonic()
    res = subprocess.run(
        [
            shutil.which("bash") or "/bin/bash",
            "-c",
            f'. "{HOOK_COMMON}"\n{script}',
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return res, time.monotonic() - started


# A stand-in `timeout` that records its argv, then drops the `-k <secs>` pair and the
# duration and runs the command.
_TIMEOUT_STUB = '#!/bin/bash\necho "{name} $*" >> "{log}"\n[ "$1" = -k ] && shift 2\nshift\nexec "$@"\n'


class TestHookRunTimeout:
    def test_the_command_runs_and_its_status_and_output_pass_through(self, tmp_path):
        res, _ = _run_timeout(
            tmp_path,
            'out=$(hook_run_timeout 5 bash -c "echo hello; exit 3" 2>&1); echo "rc=$? out=$out"',
        )
        assert "rc=3 out=hello" in res.stdout, res.stdout + res.stderr

    def test_the_fallback_cuts_a_slow_command_and_reports_124(self, tmp_path):
        # No timeout / gtimeout on this PATH: the pure-bash fallback runs.
        res, took = _run_timeout(
            tmp_path,
            'out=$(hook_run_timeout 1 bash -c "sleep 30; echo late" 2>&1); echo "rc=$? out=[$out]"',
        )
        assert "rc=124 out=[]" in res.stdout, res.stdout + res.stderr
        assert took < 10, f"took {took:.1f}s: the grandchild kept the pipe open"

    def test_the_fallback_is_quiet_on_stderr(self, tmp_path):
        # bash announces "Terminated" for a reaped signalled job; that text
        # would end up in the model-facing stderr of lint.sh.
        res, _ = _run_timeout(
            tmp_path, 'hook_run_timeout 1 bash -c "sleep 30" >/dev/null 2>&1; echo done'
        )
        res2 = subprocess.run(
            [
                shutil.which("bash") or "/bin/bash",
                "-c",
                f'. "{HOOK_COMMON}"\nhook_run_timeout 1 sleep 30',
            ],
            capture_output=True,
            text=True,
            env={"PATH": str(_tool_dir(tmp_path)), "TMPDIR": str(tmp_path)},
            timeout=60,
        )
        assert res.returncode == 0
        assert res2.stderr == "", res2.stderr
        assert res2.returncode == 124

    def test_the_fallback_returns_early_when_the_command_finishes_first(self, tmp_path):
        res, took = _run_timeout(
            tmp_path, 'hook_run_timeout 20 bash -c "exit 0"; echo "rc=$?"'
        )
        assert "rc=0" in res.stdout
        assert took < 10, "the watchdog's sleep must not be waited for"

    def test_the_fallback_leaves_no_temp_files(self, tmp_path):
        _run_timeout(tmp_path, 'hook_run_timeout 1 bash -c "sleep 30"')
        assert [p.name for p in tmp_path.glob("hook-timeout*")] == []

    def test_timeout_is_used_when_present(self, tmp_path):
        log = tmp_path / "args"
        res, _ = _run_timeout(
            tmp_path,
            'hook_run_timeout 7 bash -c "exit 5"; echo "rc=$?"',
            extra={"timeout": _TIMEOUT_STUB.format(name="timeout", log=log)},
        )
        assert "rc=5" in res.stdout
        assert log.read_text(encoding="utf-8").startswith("timeout -k 2 7 bash -c")

    def test_gtimeout_is_the_second_choice(self, tmp_path):
        log = tmp_path / "args"
        res, _ = _run_timeout(
            tmp_path,
            'hook_run_timeout 9 bash -c "exit 0"; echo "rc=$?"',
            extra={"gtimeout": _TIMEOUT_STUB.format(name="gtimeout", log=log)},
        )
        assert "rc=0" in res.stdout
        assert log.read_text(encoding="utf-8").startswith("gtimeout -k 2 9 bash -c")

    def test_timeout_wins_over_gtimeout(self, tmp_path):
        log = tmp_path / "args"
        body = '#!/bin/bash\necho "$0" >> "{log}"\n[ "$1" = -k ] && shift 2\nshift\nexec "$@"\n'
        _run_timeout(
            tmp_path,
            "hook_run_timeout 3 bash -c 'exit 0'",
            extra={
                "timeout": body.format(log=log),
                "gtimeout": body.format(log=log),
            },
        )
        assert log.read_text(encoding="utf-8").strip().endswith("/timeout")

    def test_a_real_gnu_timeout_status_is_passed_through(self, tmp_path):
        real = shutil.which("timeout") or shutil.which("gtimeout")
        if real is None:
            pytest.skip("no GNU timeout on this host")
        bindir = _tool_dir(tmp_path)
        (bindir / "timeout").symlink_to(real)
        res = subprocess.run(
            [
                shutil.which("bash") or "/bin/bash",
                "-c",
                f'. "{HOOK_COMMON}"\nout=$(hook_run_timeout 1 bash -c "sleep 30" 2>&1); echo "rc=$?"',
            ],
            capture_output=True,
            text=True,
            env={"PATH": str(bindir), "TMPDIR": str(tmp_path)},
            timeout=60,
        )
        assert "rc=124" in res.stdout, res.stdout + res.stderr


# Spellings that are not a positive duration. GNU / BSD `timeout` answers some of
# these with exit 125 (`.`, `1.2.3`) and treats `00` as "no limit"; the pure-bash
# fallback's `sleep` fails at once for them and the watchdog then kills the
# command at its first instant.
NOT_A_DURATION = ["00", ".", "1.2.3", "abc", "0", "0.0", "0.00", "", "-1", "5s", "1e3"]
A_DURATION = ["1", "2.5", "30", "0.5", "10.0", "007"]


class TestPositiveNumber:
    @pytest.mark.parametrize("value", NOT_A_DURATION)
    def test_rejects(self, value):
        assert _run('_hook_positive_number "$1"', value).returncode == 1

    @pytest.mark.parametrize("value", A_DURATION)
    def test_accepts(self, value):
        assert _run('_hook_positive_number "$1"', value).returncode == 0


class TestHookRunTimeoutIgnoresAMalformedDuration:
    """A duration that is not a positive number runs the command unbounded.

    `hook_run_timeout` has no default of its own to substitute, so it never hands
    the value to `timeout` / `sleep`. (Callers that do have a default, the lint
    layer, substitute it first: see TestLintTimeoutSetting in
    test_lint_hardening.py.)
    """

    @pytest.mark.parametrize("value", NOT_A_DURATION)
    def test_the_fallback_lets_the_command_finish(self, tmp_path, value):
        res, _ = _run_timeout(
            tmp_path,
            'hook_run_timeout "$0" bash -c "sleep 0.5; echo ran; exit 3"; echo "rc=$?"',
            args=[value],
        )
        assert "ran" in res.stdout and "rc=3" in res.stdout, res.stdout + res.stderr

    @pytest.mark.parametrize("value", NOT_A_DURATION)
    def test_a_timeout_binary_is_not_given_the_value(self, tmp_path, value):
        log = tmp_path / "args"
        res, _ = _run_timeout(
            tmp_path,
            'hook_run_timeout "$0" bash -c "echo ran; exit 3"; echo "rc=$?"',
            args=[value],
            extra={"timeout": _TIMEOUT_STUB.format(name="timeout", log=log)},
        )
        assert "ran" in res.stdout and "rc=3" in res.stdout, res.stdout + res.stderr
        assert not log.exists(), "timeout was started with " + log.read_text()


# The grandchild ignores TERM and keeps the pipe `$(...)` is waiting on; its
# parent obeys TERM, so by the time anything could look for the grandchild it
# has been re-parented and `pgrep -P` no longer leads to it.
_STUBBORN_GRANDCHILD_SCRIPT = """\
#!/bin/bash
# $1: file that receives the pid of the process holding the pipe.
( trap '' TERM; exec bash -c 'echo $$ > "$0"; exec sleep 40' "$1" ) &
wait
"""
# The command itself ignores TERM, and so does the sleep it starts.
_STUBBORN_COMMAND = "bash -c 'trap \"\" TERM; sleep 40'"


@pytest.mark.parametrize("impl", ["fallback", "binary"])
class TestHookRunTimeoutKillsWhatIgnoresTerm:
    # The bound only has to tell "cut off after the grace period" (about 3 s
    # here) from "waited for the 40 s sleeper".
    BOUND = 20

    def _run(self, tmp_path, impl, command):
        pidfile = tmp_path / "pid"
        script = (
            f'PIDFILE="{pidfile}"\n'
            f'out=$(hook_run_timeout 1 {command} 2>&1); echo "rc=$?"\n'
            'sleep 0.3; p=$(cat "$PIDFILE" 2>/dev/null)\n'
            'if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then echo SURVIVOR; fi'
        )
        res, took = _run_timeout(tmp_path, script, real_timeout=impl == "binary")
        return res, took, pidfile

    def test_a_grandchild_that_ignores_term_does_not_hold_the_caller(
        self, tmp_path, impl
    ):
        stubborn = tmp_path / "stubborn.sh"
        stubborn.write_text(_STUBBORN_GRANDCHILD_SCRIPT, encoding="utf-8")
        res, took, pidfile = self._run(
            tmp_path, impl, f'bash "{stubborn}" "{tmp_path / "pid"}"'
        )
        assert pidfile.exists(), "the stand-in never started its sleeper"
        assert "rc=124" in res.stdout, res.stdout + res.stderr
        assert took < self.BOUND, f"waited {took:.1f}s for the grandchild"
        assert "SURVIVOR" not in res.stdout, "the grandchild was left running"

    def test_a_command_that_ignores_term_is_killed_and_reported_as_a_timeout(
        self, tmp_path, impl
    ):
        # GNU / BSD timeout exit 137 (128 + SIGKILL) once `-k` had to fire; that
        # is still "cut off", and a 137 must not be mistaken for a finding.
        res, took, _ = self._run(tmp_path, impl, _STUBBORN_COMMAND)
        assert "rc=124" in res.stdout, res.stdout + res.stderr
        assert took < self.BOUND, f"waited {took:.1f}s for the command"


# ---------------------------------------------------------------------------
# hook_cap_output: bound what is handed back to the model
# ---------------------------------------------------------------------------


def _cap(text: str, max_lines=None, max_bytes=None, tmp_path=None):
    args = ""
    if max_lines is not None:
        args = f"{max_lines} {max_bytes}"
    return subprocess.run(
        ["bash", "-c", f'. "{HOOK_COMMON}"\nhook_cap_output {args}'],
        input=text.encode("utf-8"),
        capture_output=True,
        timeout=60,
    )


class TestHookCapOutput:
    def test_short_input_is_passed_through_untouched(self):
        text = "a\n\nb  c\n"
        res = _cap(text, 200, 16384)
        assert res.stdout.decode() == text

    def test_extra_lines_are_dropped_and_counted(self):
        text = "".join(f"line {i}\n" for i in range(1000))
        out = _cap(text, 10, 16384).stdout.decode()
        lines = out.splitlines()
        assert lines[:10] == [f"line {i}" for i in range(10)]
        assert "truncated 990 lines" in lines[-1]
        assert len(lines) == 11

    def test_the_byte_cap_applies_independently(self):
        text = "".join("x" * 99 + "\n" for _ in range(100))
        out = _cap(text, 1000, 500).stdout.decode()
        body, _, note = out.rpartition("…")
        assert len(body.encode()) <= 500 + 1
        assert "truncated" in note

    def test_one_huge_line_is_cut_and_reported(self):
        out = _cap("a" * 100_000 + "\n", 200, 1000).stdout
        assert len(out) < 1300
        assert b"truncated" in out

    def test_a_cut_through_a_multibyte_character_is_not_left_half_written(self):
        # 日 is three bytes; a 100-byte cut lands inside one.
        out = _cap("日" * 200 + "\n", 200, 100).stdout
        out.decode("utf-8")  # must not raise

    def test_defaults_are_about_200_lines_and_16kb(self):
        text = "".join(f"{i} " + "y" * 40 + "\n" for i in range(5000))
        out = _cap(text).stdout
        assert 150 <= out.count(b"\n") <= 260
        assert len(out) <= 17_500
        assert b"truncated" in out

    def test_empty_input_stays_empty(self):
        assert _cap("", 5, 100).stdout == b""

    def test_the_count_includes_blank_lines_at_the_cut(self):
        # `$(...)` drops trailing newlines, so a count taken from the captured
        # body is short by the blank lines the kept part ends with.
        text = "a\n" + "\n" * 4 + "".join(f"b{i}\n" for i in range(10))
        out = _cap(text, 5, 16384).stdout.decode()
        lines = out.splitlines()
        assert lines[:5] == ["a", "", "", "", ""], "the kept blank lines stay"
        assert lines[5].startswith("… truncated 10 lines"), lines[5]
        assert len(lines) == 6

    def test_the_count_is_exact_when_the_byte_cap_cuts_between_lines(self):
        text = "".join(f"{i:04d}\n" for i in range(100))  # 5 bytes a line
        out = _cap(text, 1000, 50).stdout.decode()
        lines = out.splitlines()
        assert lines[:10] == [f"{i:04d}" for i in range(10)]
        assert lines[10].startswith("… truncated 90 lines"), lines[10]


def _cap_with_env(text: str, env_extra: dict, args: str = ""):
    return subprocess.run(
        ["bash", "-c", f'. "{HOOK_COMMON}"\nhook_cap_output {args}'],
        input=text.encode("utf-8"),
        capture_output=True,
        env={**os.environ, **env_extra},
        timeout=60,
    )


# Values that are not a count of lines / bytes. `abc`, `1.5` and `10 20` made the
# numeric comparison fail, which dropped the whole report and left a bare
# "… truncated" line; `0` and the all-zero spellings would cut everything.
NOT_A_COUNT = ["abc", "", "0", "000", "-5", "1.5", "10 20", "99999999999999999999"]


class TestHookCapOutputLimits:
    @pytest.mark.parametrize("value", NOT_A_COUNT)
    def test_a_malformed_line_limit_falls_back_to_the_default(self, value):
        text = "".join(f"line {i}\n" for i in range(300))
        out = _cap_with_env(text, {"HOOK_LINT_MAX_LINES": value}).stdout.decode()
        lines = out.splitlines()
        assert lines[:200] == [f"line {i}" for i in range(200)], lines[:3]
        assert lines[200].startswith("… truncated 100 lines"), lines[200]
        assert len(lines) == 201

    @pytest.mark.parametrize("value", NOT_A_COUNT)
    def test_a_malformed_byte_limit_falls_back_to_the_default(self, value):
        text = "".join("x" * 99 + "\n" for _ in range(190))  # 19 KB in 190 lines
        out = _cap_with_env(text, {"HOOK_LINT_MAX_BYTES": value}).stdout
        assert out.startswith(b"x" * 99 + b"\n"), "the report body must survive"
        assert 15_000 < len(out) <= 16_384 + 200, len(out)
        assert b"truncated" in out

    @pytest.mark.parametrize("value", ["abc", "0", "1.5"])
    def test_malformed_arguments_fall_back_to_the_defaults_too(self, value):
        text = "".join(f"line {i}\n" for i in range(300))
        out = _cap_with_env(text, {}, f"{value} {value}").stdout.decode()
        assert "line 199" in out and "line 200" not in out
        assert "truncated 100 lines" in out

    def test_valid_settings_are_honoured(self):
        text = "".join(f"line {i}\n" for i in range(50))
        out = _cap_with_env(text, {"HOOK_LINT_MAX_LINES": "7"}).stdout.decode()
        assert out.splitlines()[:7] == [f"line {i}" for i in range(7)]
        assert "truncated 43 lines" in out

    def test_leading_zeros_are_read_as_decimal(self):
        # bash arithmetic would read 010 as octal 8.
        text = "".join(f"line {i}\n" for i in range(50))
        out = _cap_with_env(text, {"HOOK_LINT_MAX_LINES": "010"}).stdout.decode()
        assert "line 9" in out and "line 10" not in out
        assert "truncated 40 lines" in out


class TestHookPositiveIntOr:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("5", "5"),
            ("400", "400"),
            ("010", "10"),
            ("007", "7"),
            ("999999999", "999999999"),
        ],
    )
    def test_a_count_is_passed_through_as_decimal(self, value, expected):
        res = _run('hook_positive_int_or "$1" 77', value)
        assert res.returncode == 0 and res.stdout == expected, res.stderr

    @pytest.mark.parametrize("value", NOT_A_COUNT)
    def test_anything_else_gives_the_default(self, value):
        res = _run('hook_positive_int_or "$1" 77', value)
        assert res.returncode == 0 and res.stdout == "77", res.stderr


# ---------------------------------------------------------------------------
# hook_git_added_lines: only what the working tree ADDED relative to HEAD
# ---------------------------------------------------------------------------


def _added(repo, rel, *, env_extra=None):
    res = subprocess.run(
        [
            "bash",
            "-c",
            f'. "{HOOK_COMMON}"\nhook_git_added_lines "$1" "$2"',
            "bash",
            str(repo),
            rel,
        ],
        capture_output=True,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            **(env_extra or {}),
        },
        timeout=60,
    )
    assert res.returncode == 0, res.stderr
    return res.stdout.decode("utf-8").splitlines()


def _commit(repo, name, text):
    (repo / name).write_text(text, encoding="utf-8")
    run_git(repo, "add", name)
    run_git(repo, "commit", "-q", "-m", f"add {name}")


class TestHookGitAddedLines:
    def test_only_the_added_lines_come_back_with_their_line_numbers(self, git_repo):
        _commit(git_repo, "a.js", "one\ntwo\nthree\n")
        (git_repo / "a.js").write_text("one\nTWO\nthree\nfour\n", encoding="utf-8")
        assert _added(git_repo, "a.js") == ["2:TWO", "4:four"]

    def test_a_removal_only_change_adds_nothing(self, git_repo):
        _commit(git_repo, "a.js", "one\ntwo\n")
        (git_repo / "a.js").write_text("one\n", encoding="utf-8")
        assert _added(git_repo, "a.js") == []

    def test_staged_and_unstaged_edits_both_count(self, git_repo):
        _commit(git_repo, "a.js", "one\n")
        (git_repo / "a.js").write_text("one\nstaged\n", encoding="utf-8")
        run_git(git_repo, "add", "a.js")
        (git_repo / "a.js").write_text("one\nstaged\nunstaged\n", encoding="utf-8")
        assert _added(git_repo, "a.js") == ["2:staged", "3:unstaged"]

    def test_an_untracked_file_is_scanned_whole(self, git_repo):
        (git_repo / "new.js").write_text("a\nb\n", encoding="utf-8")
        assert _added(git_repo, "new.js") == ["1:a", "2:b"]

    def test_a_file_staged_but_new_since_head_is_scanned_whole(self, git_repo):
        (git_repo / "new.js").write_text("a\nb\n", encoding="utf-8")
        run_git(git_repo, "add", "new.js")
        assert _added(git_repo, "new.js") == ["1:a", "2:b"]

    def test_every_file_on_an_unborn_branch_is_scanned_whole(self, tmp_path):
        repo = _init_unborn_repo(tmp_path / "repo")
        (repo / "a.js").write_text("x\ny\n", encoding="utf-8")
        run_git(repo, "add", "a.js")
        assert _added(repo, "a.js") == ["1:x", "2:y"]

    def test_a_colour_always_user_config_does_not_leak_escape_codes(self, git_repo):
        _commit(git_repo, "a.js", "one\n")
        run_git(git_repo, "config", "color.diff", "always")
        run_git(git_repo, "config", "color.ui", "always")
        (git_repo / "a.js").write_text("one\nconsole.log(1)\n", encoding="utf-8")
        assert _added(git_repo, "a.js") == ["2:console.log(1)"]

    def test_a_line_starting_with_plus_signs_is_still_content(self, git_repo):
        _commit(git_repo, "a.js", "one\n")
        (git_repo / "a.js").write_text(
            "one\n+++ looks like a header\n", encoding="utf-8"
        )
        assert _added(git_repo, "a.js") == ["2:+++ looks like a header"]

    def test_a_missing_file_adds_nothing(self, git_repo):
        assert _added(git_repo, "ghost.js") == []


LEGIT = 'console.log("usage: cli")\n'


class TestHookGitAddedLinesAcrossARename:
    """`git mv` stages a deletion of the old path and an addition of the new one.
    The new path is not in HEAD, so without rename detection every line of it
    looks added and a console.log that was committed long ago blocks the turn."""

    def test_a_pure_rename_adds_nothing(self, git_repo):
        _commit(git_repo, "old.js", LEGIT + "one\ntwo\nthree\n")
        run_git(git_repo, "mv", "old.js", "new.js")
        assert _added(git_repo, "new.js") == []

    def test_only_the_lines_changed_in_the_new_file_come_back(self, git_repo):
        _commit(git_repo, "old.js", LEGIT + "one\ntwo\nthree\n")
        run_git(git_repo, "mv", "old.js", "new.js")
        (git_repo / "new.js").write_text(
            LEGIT + "one\nTWO\nthree\nconsole.log(1)\n", encoding="utf-8"
        )
        assert _added(git_repo, "new.js") == ["3:TWO", "5:console.log(1)"]

    def test_a_move_into_another_directory_is_a_rename_too(self, git_repo):
        _commit(git_repo, "old.js", LEGIT + "one\ntwo\nthree\n")
        (git_repo / "app").mkdir()
        run_git(git_repo, "mv", "old.js", "app/[id].js")
        assert _added(git_repo, "app/[id].js") == []

    def test_a_rename_is_found_whatever_the_user_diff_config_says(self, git_repo):
        _commit(git_repo, "old.js", LEGIT + "one\ntwo\nthree\n")
        run_git(git_repo, "config", "diff.renames", "false")
        run_git(git_repo, "mv", "old.js", "new.js")
        assert _added(git_repo, "new.js") == []

    def test_a_colour_always_user_config_does_not_hide_the_rename(self, git_repo):
        _commit(git_repo, "old.js", LEGIT + "one\ntwo\nthree\n")
        run_git(git_repo, "config", "color.diff", "always")
        run_git(git_repo, "config", "color.ui", "always")
        run_git(git_repo, "mv", "old.js", "new.js")
        assert _added(git_repo, "new.js") == []

    def test_a_rewrite_too_different_to_be_a_rename_is_scanned_whole(self, git_repo):
        # Pinned: below git's similarity threshold the pair is a deletion plus a
        # new file, so the new file's lines are all "added".
        _commit(git_repo, "old.js", "aaa\nbbb\nccc\nddd\n")
        run_git(git_repo, "mv", "old.js", "new.js")
        (git_repo / "new.js").write_text("w\nx\ny\nz\n", encoding="utf-8")
        assert _added(git_repo, "new.js") == ["1:w", "2:x", "3:y", "4:z"]

    def test_a_new_file_next_to_an_unrelated_deletion_is_scanned_whole(self, git_repo):
        _commit(git_repo, "gone.js", "aaa\nbbb\nccc\n")
        run_git(git_repo, "rm", "-q", "gone.js")
        (git_repo / "fresh.js").write_text("x\ny\n", encoding="utf-8")
        run_git(git_repo, "add", "fresh.js")
        assert _added(git_repo, "fresh.js") == ["1:x", "2:y"]

    def test_an_unstaged_move_is_still_scanned_whole(self, git_repo):
        # Pinned limitation: the destination of a plain `mv` is untracked, and
        # git only pairs paths it already tracks.
        _commit(git_repo, "old.js", LEGIT + "one\n")
        (git_repo / "old.js").rename(git_repo / "new.js")
        assert _added(git_repo, "new.js") == ["1:" + LEGIT.rstrip("\n"), "2:one"]


# ---------------------------------------------------------------------------
# hook_find_nearest_file: the config walk the formatters and linters share
# ---------------------------------------------------------------------------


def _nearest(start, root, *names):
    res = subprocess.run(
        [
            "bash",
            "-c",
            f'. "{HOOK_COMMON}"\nhook_find_nearest_file "$@"; echo "rc=$?" >&2',
            "bash",
            str(start),
            str(root),
            *names,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return res.stdout, res.stderr.strip()


class TestHookFindNearestFile:
    def test_the_nearest_directory_wins(self, tmp_path):
        (tmp_path / "a" / "b").mkdir(parents=True)
        (tmp_path / ".cfg").write_text("root\n", encoding="utf-8")
        (tmp_path / "a" / ".cfg").write_text("mid\n", encoding="utf-8")
        out, err = _nearest(tmp_path / "a" / "b", tmp_path, ".cfg")
        assert os.path.realpath(out) == os.path.realpath(tmp_path / "a" / ".cfg")
        assert err == "rc=0"

    def test_names_are_tried_in_the_order_given_within_one_directory(self, tmp_path):
        (tmp_path / "one").write_text("1\n", encoding="utf-8")
        (tmp_path / "two").write_text("2\n", encoding="utf-8")
        out, _ = _nearest(tmp_path, tmp_path, "two", "one")
        assert out.endswith("/two")

    def test_the_walk_stops_at_the_root(self, tmp_path):
        (tmp_path / "repo" / "sub").mkdir(parents=True)
        (tmp_path / ".cfg").write_text("above\n", encoding="utf-8")
        out, err = _nearest(tmp_path / "repo" / "sub", tmp_path / "repo", ".cfg")
        assert out == "" and err == "rc=1"

    def test_nothing_found_returns_one(self, tmp_path):
        out, err = _nearest(tmp_path, tmp_path, ".nope")
        assert out == "" and err == "rc=1"
