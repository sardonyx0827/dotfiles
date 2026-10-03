"""Tests for _hook_common.sh (hook_log / hook_notify).

These run against the .claude copy; .codex holds no copy or symlink of it and
instead resolves ../../.claude/hooks by physical path at runtime, which
test_hook_sync.py pins.
"""

import re
import subprocess

from conftest import REPO_ROOT

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
