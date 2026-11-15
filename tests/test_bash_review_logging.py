"""Log setup and logging I/O of the bash-review entry points must never decide a verdict.

The detail log lives under a fixed, world-writable-parent path (/tmp/claude_hooks,
/tmp/codex_hooks) and the summary log under the home directory. Before this was
pinned, setting them up ran ahead of every decision and was not failure-tolerant:

- a root created by another uid (`sudo claude`, a container) raised PermissionError,
  which the catch-all turned into "ask" (Claude) or exit 2 (Codex) for EVERY command,
  even `ls`;
- a symlink pre-planted at the root or at the leaf was followed, so pruning deleted
  the oldest files of an arbitrary directory and the writes landed there.

The contract these tests pin: the managed tree is verified from its root down to the
leaf (real directories, owned by the effective uid, tightened to 0700) and detail
logging is switched off for the run when that fails; every logging write is
best-effort; parse / analysis errors keep failing closed.

The hooks run through the `run_hook` fixture, which remaps /tmp/claude_hooks and
/tmp/codex_hooks into tmp_path (see conftest.py).
"""

import os
import re
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, fake_gemini, fake_run, hook_payload

sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
import _bash_review_common as _common  # noqa: E402

CLAUDE_HOOK = ".claude/hooks/bash-review.py"
CODEX_HOOK = ".codex/hooks/bash-review.py"

# (hook, managed root under the fake /tmp, summary log directory under the home)
ENTRIES = {
    "claude": (CLAUDE_HOOK, "claude_hooks", ".claude/logs"),
    "codex": (CODEX_HOOK, "codex_hooks", ".codex/logs"),
}
DETAIL_SUBPATH = "logs/PreToolUse/Bash/bash-review"

# The mod (bash-review-log) reads one decision per physical line in this shape.
SUMMARY_HEADER = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (\S+)\s*\| (\S+)\s*\| (.*)$"
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


@pytest.fixture(params=sorted(ENTRIES))
def entry(request):
    return request.param


def _outcome(res, name):
    """ "allow" when the command may run, otherwise the blocking verdict.

    Claude prints a permissionDecision (exit 0 either way); Codex has no ask, so a
    block is exit 2 and an allow is exit 0 with nothing on stdout.
    """
    if name == "claude":
        return res.decision
    return "allow" if res.exit_code == 0 else "block"


def _summary_lines(res, name):
    log = res.home / ENTRIES[name][2] / "bash-review.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _managed_root(res, name) -> Path:
    return res.fake_tmp / ENTRIES[name][1]


def _plant_victim(tmp_path: Path, count: int = 1100) -> Path:
    """A directory somebody else cares about, holding more files than the prune cap."""
    victim = tmp_path / "victim"
    victim.mkdir()
    for i in range(count):
        (victim / f"bash_cmd_{i:05d}_1.log").write_text("precious", encoding="utf-8")
    (victim / "notes.txt").write_text("precious", encoding="utf-8")
    victim.chmod(0o755)
    return victim


def _assert_untouched(victim: Path, count: int = 1100) -> None:
    names = sorted(p.name for p in victim.iterdir())
    assert len(names) == count + 1, "a file was added to or deleted from the victim"
    assert "bash_cmd_00000_1.log" in names
    assert (victim.stat().st_mode & 0o777) == 0o755, "the victim was chmod'ed"


# ---------------------------------------------------------------------------
# Hook level: the verdict does not depend on the log tree
# ---------------------------------------------------------------------------
class TestVerdictIsIndependentOfLogSetup:
    def test_a_root_that_is_a_file_does_not_turn_ls_into_ask(
        self, run_hook, tmp_path, entry
    ):
        # Any OSError out of the setup (here FileExistsError; PermissionError for a
        # root owned by another uid) used to become ask / exit 2 for every command.
        hook, tmp_name, _ = ENTRIES[entry]
        fake_tmp = tmp_path / "fake-tmp"
        fake_tmp.mkdir(exist_ok=True)
        (fake_tmp / tmp_name).write_text("squatter", encoding="utf-8")
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) == "allow"
        assert "hook error" not in (res.reason if entry == "claude" else res.stderr)

    @posix_only
    def test_a_read_only_root_we_own_is_tightened_before_the_walk_descends(
        self, run_hook, tmp_path, entry
    ):
        # We own it, so the walk chmods it back to 0700 and goes on to log as usual
        # (a root another uid owns is the case above).
        hook, tmp_name, _ = ENTRIES[entry]
        root = tmp_path / "fake-tmp" / tmp_name
        root.mkdir(parents=True)
        root.chmod(0o500)
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) == "allow"
        assert (root.stat().st_mode & 0o777) == 0o700
        assert len(list((root / DETAIL_SUBPATH).iterdir())) == 1

    @posix_only
    def test_a_root_owned_by_another_uid_does_not_turn_ls_into_ask(
        self, run_hook, tmp_path, monkeypatch, entry
    ):
        hook, tmp_name, _ = ENTRIES[entry]
        root = tmp_path / "fake-tmp" / tmp_name
        root.mkdir(parents=True)
        real = os.geteuid()
        monkeypatch.setattr(os, "geteuid", lambda: real + 1)
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) == "allow"
        # Nothing was written below a root we do not own.
        assert not (root / DETAIL_SUBPATH).exists()

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("ls -la", "allow"),
            ("curl https://example.com", "deny"),
        ],
    )
    def test_deterministic_verdicts_survive_a_disabled_detail_log(
        self, run_hook, tmp_path, entry, command, expected
    ):
        hook, tmp_name, _ = ENTRIES[entry]
        fake_tmp = tmp_path / "fake-tmp"
        fake_tmp.mkdir(exist_ok=True)
        (fake_tmp / tmp_name).write_text("squatter", encoding="utf-8")
        res = run_hook(hook, hook_payload(command))
        if expected == "allow":
            assert _outcome(res, entry) == "allow"
        elif entry == "claude":
            assert res.decision == "deny"
        else:
            assert res.exit_code == 2
            assert "Blocked dangerous command" in res.stderr

    def test_a_model_verdict_survives_a_disabled_detail_log(
        self, run_hook, tmp_path, entry
    ):
        hook, tmp_name, _ = ENTRIES[entry]
        fake_tmp = tmp_path / "fake-tmp"
        fake_tmp.mkdir(exist_ok=True)
        (fake_tmp / tmp_name).write_text("squatter", encoding="utf-8")
        res = run_hook(hook, hook_payload("make deploy"), urlopen=fake_gemini("ALLOW"))
        assert _outcome(res, entry) == "allow"

    def test_a_summary_log_directory_that_cannot_be_made_does_not_decide(
        self, run_hook, tmp_path, entry
    ):
        # `~/.claude/logs` exists as a file: makedirs raises FileExistsError.
        hook, _, logs = ENTRIES[entry]
        parent = tmp_path / "home" / Path(logs).parent
        parent.mkdir(parents=True)
        (tmp_path / "home" / logs).write_text("not a directory", encoding="utf-8")
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) == "allow"

    def test_a_summary_log_that_cannot_be_appended_does_not_decide(
        self, run_hook, tmp_path, entry
    ):
        hook, _, logs = ENTRIES[entry]
        log_dir = tmp_path / "home" / logs
        (log_dir / "bash-review.log").mkdir(parents=True)  # a directory, not a file
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) == "allow"

    def test_a_failing_detail_write_does_not_stop_the_summary_or_the_verdict(
        self, run_hook, monkeypatch, entry
    ):
        hook, _, _ = ENTRIES[entry]

        def denied(path, flags):
            raise PermissionError("disk went read-only")

        monkeypatch.setattr(_common, "_open_private", denied)
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) == "allow"
        # The summary line is written even though the detail write raised before it.
        assert any("safe command" in line for line in _summary_lines(res, entry))

    def test_a_failing_prune_keeps_detail_logging_on(
        self, run_hook, monkeypatch, entry
    ):
        hook, tmp_name, _ = ENTRIES[entry]

        def broken(log_dir, keep=1000):
            raise PermissionError("cannot list")

        monkeypatch.setattr(_common, "prune_dir", broken)
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) == "allow"
        detail_dir = _managed_root(res, entry) / DETAIL_SUBPATH
        assert len(list(detail_dir.iterdir())) == 1

    def test_the_input_size_limit_still_blocks_when_every_log_fails(
        self, run_hook, tmp_path, entry
    ):
        # The size-limit branch writes the detail log directly, outside the
        # wrappers the other branches use.
        hook, tmp_name, logs = ENTRIES[entry]
        fake_tmp = tmp_path / "fake-tmp"
        fake_tmp.mkdir(exist_ok=True)
        (fake_tmp / tmp_name).write_text("squatter", encoding="utf-8")
        (tmp_path / "home" / Path(logs).parent).mkdir(parents=True)
        (tmp_path / "home" / logs).write_text("not a directory", encoding="utf-8")
        res = run_hook(hook, hook_payload("echo " + "x" * _common.MAX_INPUT_CHARS))
        if entry == "claude":
            assert res.decision == "ask"
            assert "too large" in res.reason
            assert "hook error" not in res.reason
        else:
            assert res.exit_code == 2
            assert "too large" in res.stderr
            assert "hook error" not in res.stderr

    def test_parse_errors_still_fail_closed(self, run_hook, monkeypatch, entry):
        # The suppression is limited to logging I/O.
        hook, _, _ = ENTRIES[entry]

        def boom(*args, **kwargs):
            raise RuntimeError("analysis blew up")

        monkeypatch.setattr(_common, "scan_secrets", boom)
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) != "allow"
        if entry == "claude":
            assert "hook error" in res.reason
        else:
            assert "hook error" in res.stderr


# ---------------------------------------------------------------------------
# Hook level: a symlinked root or leaf is never followed
# ---------------------------------------------------------------------------
@posix_only
class TestSymlinkedLogTreeIsNotFollowed:
    def test_a_symlinked_root_is_not_followed(self, run_hook, tmp_path, entry):
        hook, tmp_name, _ = ENTRIES[entry]
        victim = _plant_victim(tmp_path)
        fake_tmp = tmp_path / "fake-tmp"
        fake_tmp.mkdir(exist_ok=True)
        (fake_tmp / tmp_name).symlink_to(victim)
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) == "allow"
        _assert_untouched(victim)
        assert not (victim / "logs").exists()

    def test_a_symlinked_leaf_is_not_pruned_or_written_through(
        self, run_hook, tmp_path, entry
    ):
        hook, tmp_name, _ = ENTRIES[entry]
        victim = _plant_victim(tmp_path)
        leaf = tmp_path / "fake-tmp" / tmp_name / DETAIL_SUBPATH
        leaf.parent.mkdir(parents=True)
        leaf.symlink_to(victim)
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) == "allow"
        _assert_untouched(victim)

    def test_a_symlinked_middle_level_is_not_followed(self, run_hook, tmp_path, entry):
        hook, tmp_name, _ = ENTRIES[entry]
        victim = _plant_victim(tmp_path)
        (victim / "PreToolUse" / "Bash").mkdir(parents=True)
        (victim / "PreToolUse" / "Bash" / "bash-review").mkdir()
        logs = tmp_path / "fake-tmp" / tmp_name / "logs"
        logs.parent.mkdir(parents=True)
        logs.symlink_to(victim)
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) == "allow"
        assert list((victim / "PreToolUse" / "Bash" / "bash-review").iterdir()) == []

    def test_the_summary_says_detail_logging_was_off(self, run_hook, tmp_path, entry):
        hook, tmp_name, _ = ENTRIES[entry]
        victim = _plant_victim(tmp_path, count=3)
        fake_tmp = tmp_path / "fake-tmp"
        fake_tmp.mkdir(exist_ok=True)
        (fake_tmp / tmp_name).symlink_to(victim)
        res = run_hook(hook, hook_payload("echo marker-9f3a"))
        notes = [ln for ln in _summary_lines(res, entry) if "detail logging" in ln]
        assert len(notes) == 1
        m = SUMMARY_HEADER.match(notes[0])
        assert m is not None, "the note must keep the one-decision-per-line shape"
        # A decision word the mod does not count as allow / ask / deny.
        assert m.group(2) not in {"ALLOW", "ASK", "DENY", "ERROR"}
        assert "disabled" in notes[0]
        # The command has not been secret-scanned at this point: it is not logged.
        assert "marker-9f3a" not in notes[0]
        # The decision itself is still logged on its own line.
        assert any(
            "marker-9f3a" in ln and "ALLOW" in ln for ln in _summary_lines(res, entry)
        )


class TestHealthyLogTreeStaysQuiet:
    def test_no_note_when_detail_logging_works(self, run_hook, entry):
        hook, _, _ = ENTRIES[entry]
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) == "allow"
        assert not any("detail logging" in ln for ln in _summary_lines(res, entry))

    @posix_only
    def test_the_whole_managed_tree_is_tightened_to_0700(
        self, run_hook, tmp_path, entry
    ):
        hook, tmp_name, _ = ENTRIES[entry]
        leaf = tmp_path / "fake-tmp" / tmp_name / DETAIL_SUBPATH
        leaf.mkdir(parents=True)
        for d in [leaf, *leaf.parents]:
            if d == tmp_path:
                break
            d.chmod(0o755)
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) == "allow"
        level = leaf
        while level != tmp_path / "fake-tmp":
            assert (level.stat().st_mode & 0o777) == 0o700, level
            level = level.parent
        assert len(list(leaf.iterdir())) == 1


class TestPruneOnlyTouchesItsOwnLogs:
    def test_other_files_in_the_log_directory_are_never_pruned(
        self, run_hook, tmp_path, entry
    ):
        hook, tmp_name, _ = ENTRIES[entry]
        detail_dir = tmp_path / "fake-tmp" / tmp_name / DETAIL_SUBPATH
        detail_dir.mkdir(parents=True)
        for i in range(1002):
            (detail_dir / f"bash_cmd_{i:05d}_1.log").write_text("x", encoding="utf-8")
        # Names sorting before the hook's own, none of them its own pattern.
        for name in (
            "a_00000.log",
            "0.txt",
            "bash_cmd_notes.txt",
            "bash_cmd_x.log.bak",
        ):
            (detail_dir / name).write_text("keep", encoding="utf-8")
        run_hook(hook, hook_payload("ls -la"))
        assert not (detail_dir / "bash_cmd_00000_1.log").exists()
        assert (detail_dir / "bash_cmd_00003_1.log").exists()
        for name in (
            "a_00000.log",
            "0.txt",
            "bash_cmd_notes.txt",
            "bash_cmd_x.log.bak",
        ):
            assert (detail_dir / name).read_text(encoding="utf-8") == "keep", name

    def test_an_undeletable_entry_sorting_first_does_not_halt_pruning(
        self, run_hook, tmp_path, entry
    ):
        # A directory named like a log cannot be os.remove()d. It sorts before every
        # real name, so pruning used to stop on it on every run, forever.
        hook, tmp_name, _ = ENTRIES[entry]
        detail_dir = tmp_path / "fake-tmp" / tmp_name / DETAIL_SUBPATH
        detail_dir.mkdir(parents=True)
        trap = detail_dir / "bash_cmd_0.log"
        trap.mkdir()
        (trap / "inside").write_text("x", encoding="utf-8")
        for i in range(1002):
            (detail_dir / f"bash_cmd_{i:05d}_1.log").write_text("x", encoding="utf-8")
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) == "allow"
        assert trap.is_dir()
        # 1003 candidates (the trap and 1002 logs), cap 1000: three deletions, none of
        # them the trap, so the oldest three real logs go.
        for i in range(3):
            assert not (detail_dir / f"bash_cmd_{i:05d}_1.log").exists()
        assert (detail_dir / "bash_cmd_00003_1.log").exists()


# ---------------------------------------------------------------------------
# The "detail logging disabled" note is written once per ~100 summary lines
# ---------------------------------------------------------------------------
def _disable_detail_logging(tmp_path: Path, tmp_name: str) -> None:
    """Plant a symlink at the managed root: every run then verifies and disables."""
    victim = tmp_path / "victim"
    victim.mkdir(exist_ok=True)
    fake_tmp = tmp_path / "fake-tmp"
    fake_tmp.mkdir(exist_ok=True)
    (fake_tmp / tmp_name).symlink_to(victim)


def _notes(res, name):
    return [ln for ln in _summary_lines(res, name) if "| logging" in ln]


@posix_only
class TestDisabledNoteIsRateLimited:
    """While the detail log stays off, every run would append a NOTE and the
    500-line summary would keep about half as many decisions. One NOTE per ~100
    lines is enough for the mod's lookup to tell why full commands are missing."""

    def test_the_first_disabled_run_writes_one_note_and_later_runs_add_none(
        self, run_hook, tmp_path, entry
    ):
        hook, tmp_name, _ = ENTRIES[entry]
        _disable_detail_logging(tmp_path, tmp_name)
        res = run_hook(hook, hook_payload("ls -la"))
        assert len(_notes(res, entry)) == 1
        for _ in range(50):
            res = run_hook(hook, hook_payload("ls -la"))
            assert _outcome(res, entry) == "allow"
        assert len(_notes(res, entry)) == 1
        # Each of the 51 runs still logged its own decision.
        decisions = [ln for ln in _summary_lines(res, entry) if "| pre " in ln]
        assert len(decisions) == 51

    def test_a_new_note_appears_once_the_old_one_is_past_the_last_100_lines(
        self, run_hook, tmp_path, entry
    ):
        hook, tmp_name, logs = ENTRIES[entry]
        _disable_detail_logging(tmp_path, tmp_name)
        res = run_hook(hook, hook_payload("ls -la"))
        assert len(_notes(res, entry)) == 1
        log = res.home / logs / "bash-review.log"
        filler = "[2024-01-01 00:00:00] ALLOW | pre      | ls | safe command\n"
        with open(log, "a", encoding="utf-8") as f:
            f.write(filler * 101)
        res = run_hook(hook, hook_payload("ls -la"))
        assert len(_notes(res, entry)) == 2

    def test_a_note_still_inside_the_window_is_not_repeated(
        self, run_hook, tmp_path, entry
    ):
        hook, tmp_name, logs = ENTRIES[entry]
        _disable_detail_logging(tmp_path, tmp_name)
        res = run_hook(hook, hook_payload("ls -la"))
        log = res.home / logs / "bash-review.log"
        filler = "[2024-01-01 00:00:00] ALLOW | pre      | ls | safe command\n"
        with open(log, "a", encoding="utf-8") as f:
            f.write(filler * 90)
        res = run_hook(hook, hook_payload("ls -la"))
        assert len(_notes(res, entry)) == 1

    def test_an_unreadable_summary_log_still_leaves_the_verdict_alone(
        self, run_hook, tmp_path, entry
    ):
        # The log path is a directory: reading it for the last lines fails, writing
        # the note fails, and none of it matters to the verdict.
        hook, tmp_name, logs = ENTRIES[entry]
        _disable_detail_logging(tmp_path, tmp_name)
        (tmp_path / "home" / logs / "bash-review.log").mkdir(parents=True)
        res = run_hook(hook, hook_payload("ls -la"))
        assert _outcome(res, entry) == "allow"


# ---------------------------------------------------------------------------
# BR-5: the Codex high-risk branch settled the decision AFTER its logging
# ---------------------------------------------------------------------------
class TestDecisionIsSettledBeforeLogging:
    """A logging failure that is not an OSError (here a bug raising RuntimeError)
    must not flip an allow into the catch-all's block, nor replace a block's reason
    with "hook error". Every other branch, and the Claude entry, settle first."""

    def _break_summary(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("summary writer bug")

        monkeypatch.setattr(_common, "log_summary", boom)

    def test_a_unanimous_high_risk_allow_stays_allowed(
        self, run_hook, monkeypatch, entry
    ):
        hook, _, _ = ENTRIES[entry]
        self._break_summary(monkeypatch)
        res = run_hook(
            hook,
            hook_payload("npm install left-pad"),
            urlopen=fake_gemini("ALLOW"),
            run=fake_run(stdout="ALLOW"),
        )
        assert _outcome(res, entry) == "allow"

    def test_a_high_risk_block_keeps_its_reason(self, run_hook, monkeypatch, entry):
        hook, _, _ = ENTRIES[entry]
        self._break_summary(monkeypatch)
        res = run_hook(
            hook,
            hook_payload("npm install left-pad"),
            urlopen=fake_gemini("ALLOW"),
            run=fake_run(stdout="ASK"),
        )
        assert _outcome(res, entry) != "allow"
        text = res.reason if entry == "claude" else res.stderr
        assert "High-risk command" in text
        assert "hook error" not in text


# ---------------------------------------------------------------------------
# Unit level: the shared helpers
# ---------------------------------------------------------------------------
@posix_only
class TestEnsurePrivateDir:
    def test_returns_none_and_builds_the_chain_at_0700(self, tmp_path):
        target = tmp_path / "root" / "a" / "b"
        assert (
            _common.ensure_private_dir(str(target), root=str(tmp_path / "root")) is None
        )
        for d in (tmp_path / "root", tmp_path / "root" / "a", target):
            assert (d.stat().st_mode & 0o777) == 0o700, d

    def test_tightens_existing_levels_but_never_the_parent_of_the_root(self, tmp_path):
        root = tmp_path / "root"
        target = root / "a"
        target.mkdir(parents=True)
        for d in (root, target, tmp_path):
            d.chmod(0o755)
        assert _common.ensure_private_dir(str(target), root=str(root)) is None
        assert (root.stat().st_mode & 0o777) == 0o700
        assert (target.stat().st_mode & 0o777) == 0o700
        assert (tmp_path.stat().st_mode & 0o777) == 0o755

    def test_a_symlinked_leaf_is_refused_and_left_alone(self, tmp_path):
        victim = tmp_path / "victim"
        victim.mkdir()
        victim.chmod(0o755)
        root = tmp_path / "root"
        root.mkdir()
        (root / "leaf").symlink_to(victim)
        problem = _common.ensure_private_dir(str(root / "leaf"), root=str(root))
        assert problem and "leaf" in problem
        assert (victim.stat().st_mode & 0o777) == 0o755
        assert list(victim.iterdir()) == []

    def test_a_symlinked_root_is_refused_before_anything_is_created_in_it(
        self, tmp_path
    ):
        victim = tmp_path / "victim"
        victim.mkdir()
        root = tmp_path / "root"
        root.symlink_to(victim)
        problem = _common.ensure_private_dir(str(root / "a" / "b"), root=str(root))
        assert problem
        assert list(victim.iterdir()) == []

    def test_a_symlink_above_the_root_is_not_inspected(self, tmp_path):
        # macOS: /tmp is a symlink to /private/tmp. Only root and below are verified.
        real = tmp_path / "real-tmp"
        real.mkdir()
        (tmp_path / "tmp").symlink_to(real)
        root = tmp_path / "tmp" / "claude_hooks"
        leaf = root / "logs"
        assert _common.ensure_private_dir(str(leaf), root=str(root)) is None
        assert (real / "claude_hooks" / "logs").is_dir()

    def test_a_dangling_symlink_is_refused(self, tmp_path):
        root = tmp_path / "root"
        root.symlink_to(tmp_path / "nowhere")
        assert _common.ensure_private_dir(str(root), root=str(root))
        assert not (tmp_path / "nowhere").exists()

    def test_a_file_in_the_way_is_refused(self, tmp_path):
        (tmp_path / "root").write_text("x", encoding="utf-8")
        assert _common.ensure_private_dir(
            str(tmp_path / "root" / "a"), root=str(tmp_path / "root")
        )

    def test_a_directory_owned_by_another_user_is_refused_and_not_chmoded(
        self, tmp_path, monkeypatch
    ):
        root = tmp_path / "root"
        root.mkdir()
        root.chmod(0o755)
        real = os.geteuid()
        monkeypatch.setattr(os, "geteuid", lambda: real + 1)
        problem = _common.ensure_private_dir(str(root), root=str(root))
        assert problem and "owned" in problem
        assert (root.stat().st_mode & 0o777) == 0o755

    def test_a_mkdir_failure_is_reported_not_raised(self, tmp_path, monkeypatch):
        def denied(path, mode=0o777, **kwargs):
            raise PermissionError("created by another uid")

        monkeypatch.setattr(os, "mkdir", denied)
        problem = _common.ensure_private_dir(
            str(tmp_path / "root" / "a"), root=str(tmp_path / "root")
        )
        assert problem and "PermissionError" in problem

    def test_a_chmod_failure_is_reported_not_raised(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        root.mkdir()
        root.chmod(0o755)

        def denied(*args, **kwargs):
            raise PermissionError("not ours")

        monkeypatch.setattr(os, "chmod", denied)
        problem = _common.ensure_private_dir(str(root), root=str(root))
        assert problem and "PermissionError" in problem

    def test_a_chmod_that_does_not_stick_is_reported(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        root.mkdir()
        root.chmod(0o755)
        monkeypatch.setattr(os, "chmod", lambda *a, **k: None)
        problem = _common.ensure_private_dir(str(root), root=str(root))
        assert problem and "0700" in problem

    def test_an_lstat_failure_is_reported_not_raised(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        root.mkdir()

        def broken(path, **kwargs):
            raise PermissionError("cannot stat")

        monkeypatch.setattr(os, "lstat", broken)
        assert _common.ensure_private_dir(str(root), root=str(root))

    def test_without_a_root_only_the_leaf_is_verified(self, tmp_path):
        target = tmp_path / "x" / "y"
        assert _common.ensure_private_dir(str(target)) is None
        assert (target.stat().st_mode & 0o777) == 0o700

    def test_a_root_that_is_not_an_ancestor_only_verifies_the_leaf(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        other.chmod(0o755)
        target = tmp_path / "work" / "logs"
        assert _common.ensure_private_dir(str(target), root=str(other)) is None
        assert (target.stat().st_mode & 0o777) == 0o700
        assert (other.stat().st_mode & 0o777) == 0o755

    def test_a_platform_without_owners_only_requires_a_real_directory(
        self, tmp_path, monkeypatch
    ):
        root = tmp_path / "root"
        root.mkdir()
        root.chmod(0o755)
        monkeypatch.delattr(os, "geteuid")
        assert _common.ensure_private_dir(str(root), root=str(root)) is None
        # Not a POSIX permission model: nothing was tightened.
        assert (root.stat().st_mode & 0o777) == 0o755


class TestPruneDirKeepsToItsOwnNames:
    def _fill(self, directory: Path, own: int, other: int) -> None:
        directory.mkdir(exist_ok=True)
        for i in range(own):
            (directory / f"bash_cmd_{i:05d}_1.log").write_text("x", encoding="utf-8")
        for i in range(other):
            (directory / f"other_{i:05d}.log").write_text("x", encoding="utf-8")

    def test_only_the_hooks_own_files_are_deleted(self, tmp_path):
        self._fill(tmp_path / "d", own=8, other=5)
        _common.prune_dir(str(tmp_path / "d"), keep=5)
        names = sorted(p.name for p in (tmp_path / "d").iterdir())
        assert [n for n in names if n.startswith("other_")] == [
            f"other_{i:05d}.log" for i in range(5)
        ]
        assert [n for n in names if n.startswith("bash_cmd_")] == [
            f"bash_cmd_{i:05d}_1.log" for i in range(3, 8)
        ]

    def test_foreign_files_do_not_count_toward_the_cap(self, tmp_path):
        self._fill(tmp_path / "d", own=4, other=50)
        _common.prune_dir(str(tmp_path / "d"), keep=5)
        assert len(list((tmp_path / "d").iterdir())) == 54

    @pytest.mark.parametrize(
        "name",
        [
            "bash_cmd_1.log.bak",
            "bash_cmd_",
            "xbash_cmd_1.log",
            "bash_cmd_1.txt",
            "bash_cmd.log",
        ],
    )
    def test_look_alike_names_are_not_the_hooks_own(self, tmp_path, name):
        directory = tmp_path / "d"
        self._fill(directory, own=3, other=0)
        (directory / name).write_text("keep", encoding="utf-8")
        _common.prune_dir(str(directory), keep=1)
        assert (directory / name).read_text(encoding="utf-8") == "keep"

    @posix_only
    def test_a_symlink_named_like_a_log_is_removed_not_followed(self, tmp_path):
        directory = tmp_path / "d"
        self._fill(directory, own=3, other=0)
        victim = tmp_path / "victim.txt"
        victim.write_text("keep", encoding="utf-8")
        (directory / "bash_cmd_00000_0.log").symlink_to(victim)
        _common.prune_dir(str(directory), keep=1)
        assert victim.read_text(encoding="utf-8") == "keep"


class TestPrepareDetailLogDir:
    @posix_only
    def test_returns_none_and_prunes_when_the_tree_is_sound(self, tmp_path):
        root = tmp_path / "root"
        leaf = root / "logs"
        leaf.mkdir(parents=True)
        for i in range(6):
            (leaf / f"bash_cmd_{i:05d}_1.log").write_text("x", encoding="utf-8")
        assert _common.prepare_detail_log_dir(str(leaf), str(root)) is None
        assert (leaf / "bash_cmd_00000_1.log").exists()  # default cap is 1000

    @posix_only
    def test_reports_why_when_the_tree_is_unsound(self, tmp_path):
        victim = tmp_path / "victim"
        victim.mkdir()
        root = tmp_path / "root"
        root.symlink_to(victim)
        problem = _common.prepare_detail_log_dir(str(root / "logs"), str(root))
        assert problem
        assert str(root) in problem

    @posix_only
    def test_a_prune_failure_does_not_disable_logging(self, tmp_path, monkeypatch):
        root = tmp_path / "root"

        def broken(log_dir, keep=1000):
            raise PermissionError("cannot list")

        monkeypatch.setattr(_common, "prune_dir", broken)
        assert _common.prepare_detail_log_dir(str(root / "logs"), str(root)) is None


class TestBestEffortWriters:
    def test_a_missing_detail_target_returns_false_without_raising(self, tmp_path):
        log = tmp_path / "missing" / "d.log"
        assert _common.try_write_detail_log(str(log), "Bash", {}, {}) is False

    def test_no_detail_target_means_no_write(self):
        assert _common.try_write_detail_log(None, "Bash", {}, {}) is False

    def test_a_detail_log_is_written_and_reported(self, tmp_path):
        log = tmp_path / "d.log"
        assert _common.try_write_detail_log(
            str(log), "Bash", {"command": "ls"}, {"Result": "ALLOW"}
        )
        assert "Result: ALLOW" in log.read_text(encoding="utf-8")

    @posix_only
    def test_a_symlinked_detail_file_is_not_written_through(self, tmp_path):
        victim = tmp_path / "victim.txt"
        victim.write_text("keep", encoding="utf-8")
        log = tmp_path / "d.log"
        log.symlink_to(victim)
        assert _common.try_write_detail_log(str(log), "Bash", {}, {}) is False
        assert victim.read_text(encoding="utf-8") == "keep"

    def test_an_unencodable_command_is_a_logging_failure_not_a_crash(self, tmp_path):
        log = tmp_path / "d.log"
        assert (
            _common.try_write_detail_log(str(log), "Bash", {"command": "\ud800"}, {})
            is False
        )

    def test_a_non_logging_error_is_not_swallowed(self, tmp_path, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("bug")

        monkeypatch.setattr(_common, "write_detail_log", boom)
        with pytest.raises(RuntimeError):
            _common.try_write_detail_log(str(tmp_path / "d.log"), "Bash", {}, {})

    def test_the_summary_line_is_written_and_its_directory_made(self, tmp_path):
        log = tmp_path / "a" / "b" / "s.log"
        assert _common.try_log_summary(str(log), "ls", "ALLOW", "pre", "safe command")
        assert "safe command" in log.read_text(encoding="utf-8")

    def test_a_summary_directory_that_is_a_file_returns_false(self, tmp_path):
        (tmp_path / "a").write_text("x", encoding="utf-8")
        log = tmp_path / "a" / "s.log"
        assert _common.try_log_summary(str(log), "ls", "ALLOW", "pre", "r") is False

    def test_a_summary_log_that_is_a_directory_returns_false(self, tmp_path):
        log = tmp_path / "s.log"
        log.mkdir()
        assert _common.try_log_summary(str(log), "ls", "ALLOW", "pre", "r") is False

    def test_an_unencodable_summary_command_returns_false(self, tmp_path):
        log = tmp_path / "s.log"
        assert _common.try_log_summary(str(log), "\ud800", "ALLOW", "pre", "r") is False

    def test_a_non_logging_error_in_the_summary_is_not_swallowed(
        self, tmp_path, monkeypatch
    ):
        def boom(*args, **kwargs):
            raise RuntimeError("bug")

        monkeypatch.setattr(_common, "log_summary", boom)
        with pytest.raises(RuntimeError):
            _common.try_log_summary(str(tmp_path / "s.log"), "ls", "ALLOW", "pre", "r")


class TestNoteDetailLogDisabled:
    FILLER = "[2024-01-01 00:00:00] ALLOW | pre      | ls | safe command\n"

    def _note(self, log: Path) -> bool:
        return _common.note_detail_log_disabled(
            str(log), "root-dir: owned by another user"
        )

    def _count(self, log: Path) -> int:
        return sum(
            "| logging" in ln for ln in log.read_text(encoding="utf-8").splitlines()
        )

    def test_it_writes_a_note_in_the_log_line_shape_when_there_is_none(self, tmp_path):
        log = tmp_path / "s.log"
        assert self._note(log) is True
        (line,) = log.read_text(encoding="utf-8").splitlines()
        m = SUMMARY_HEADER.match(line)
        assert m is not None
        assert m.group(2) == "NOTE"
        assert m.group(3) == "logging"
        assert "detail logging disabled" in line
        assert "owned by another user" in line

    def test_it_does_not_repeat_a_recent_note(self, tmp_path):
        log = tmp_path / "s.log"
        assert self._note(log) is True
        assert self._note(log) is False
        assert self._count(log) == 1

    def test_the_window_is_the_last_100_lines(self, tmp_path):
        log = tmp_path / "s.log"
        self._note(log)
        # 99 lines after the note: it is still the 100th line from the end.
        with open(log, "a", encoding="utf-8") as f:
            f.write(self.FILLER * 99)
        assert self._note(log) is False
        # One more and it has slid out of the window.
        with open(log, "a", encoding="utf-8") as f:
            f.write(self.FILLER)
        assert self._note(log) is True
        assert self._count(log) == 2

    def test_a_decision_line_that_merely_mentions_logging_is_not_a_note(self, tmp_path):
        log = tmp_path / "s.log"
        log.write_text(
            "[2024-01-01 00:00:00] ASK   | pre      | echo | NOTE | logging\n",
            encoding="utf-8",
        )
        assert self._note(log) is True

    def test_only_the_tail_of_a_large_log_is_read(self, tmp_path):
        # Long lines: the last 100 of them do not fit the tail window. The check is
        # bounded rather than exact, so an old note may be written again (never missed
        # in the other direction, and never an unbounded read).
        log = tmp_path / "s.log"
        self._note(log)
        long_line = "[2024-01-01 00:00:00] ALLOW | pre      | ls | " + "x" * 4000 + "\n"
        with open(log, "a", encoding="utf-8") as f:
            f.write(long_line * 40)  # 160 KB after the note, 40 lines
        assert self._note(log) is True

    def test_an_unreadable_log_is_not_an_error(self, tmp_path):
        log = tmp_path / "s.log"
        log.mkdir()  # reading fails, and so does writing
        assert self._note(log) is False

    def test_a_non_ascii_byte_in_the_window_is_not_an_error(self, tmp_path):
        log = tmp_path / "s.log"
        log.write_bytes(b"\xff\xfe garbage\n")
        assert self._note(log) is True


@posix_only
class TestDetailLogOpensWithoutFollowingSymlinks:
    def test_write_detail_log_refuses_a_symlinked_file(self, tmp_path):
        victim = tmp_path / "victim.txt"
        victim.write_text("keep", encoding="utf-8")
        log = tmp_path / "d.log"
        log.symlink_to(victim)
        with pytest.raises(OSError):
            _common.write_detail_log(str(log), "Bash", {}, {})
        assert victim.read_text(encoding="utf-8") == "keep"
