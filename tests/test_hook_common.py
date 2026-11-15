"""Tests for _hook_common.sh (hook_log / hook_notify / hook_git_changed_files).

These run against the .claude copy; .codex holds no copy or symlink of it and
instead resolves ../../.claude/hooks by physical path at runtime, which
test_hook_sync.py pins.
"""

import os
import re
import subprocess

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
