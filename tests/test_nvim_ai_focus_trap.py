"""The focus trap on the AI floating windows (ai/focus_trap.lua, ui.lua, init.lua).

Every AI popup and the replace-prompt window trap focus: leave the window and
you are sent back. That was one WinEnter autocmd PER trap, each doing "if focus
is outside ME, vim.schedule() a jump back to me". With two traps alive -- two
`run_multi` clusters, or a cluster and the prompt window, which is one keypress
away because every AI mapping is global (`V` then `<C-c>` inside an open popup,
or `,cx`) -- each trap snaps focus to itself, the other trap sees focus outside
itself and snaps it back, and Neovim spins in vim.schedule at 100% CPU until it
is killed.

The fix is one registry of live clusters and one handler: focus inside ANY live
cluster is left alone, focus outside all of them goes to the cluster that had
focus most recently, on the tabpage you are in. These tests drive the real
windows, keymaps and autocmds under headless nvim (tests/lua/ai_focus_trap.lua)
and assert where focus ends up. They are the only thing that can: a ping-pong
exists only as the interaction of two live autocmds.

Freeze detection is two-layered, because a hang cannot report itself. The Lua
side counts WinEnter events and bails out with exit code 3 once a legitimate
scenario could not possibly have produced that many; the subprocess timeout
here is the backstop for a freeze that never reaches the count.

Which cases were observed RED against the unfixed code is recorded in the
class docstrings: the ping-pong and tabpage ones fail there, the single-trap
ones pass there by design (they pin behaviour that must not change).
"""

import json
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

NVIM = shutil.which("nvim")
pytestmark = pytest.mark.skipif(NVIM is None, reason="nvim not installed")

SCENARIO_LUA = REPO_ROOT / "tests" / "lua" / "ai_focus_trap.lua"
LUA_TREE = REPO_ROOT / ".config" / "nvim" / "lua"

# Backstop for a freeze that never reaches the in-script WinEnter cap. A healthy
# scenario takes about a second; this is long enough for a loaded CI runner and
# short enough that a real hang is a failure, not a stuck job.
FREEZE_TIMEOUT = 20

# A healthy scenario produces about ten WinEnter events (the Lua side stops at
# 400). Several times that and something is oscillating, even if it settled.
MAX_FLIPS = 40


def _run(home, name):
    """Run one scenario in a sealed nvim and return its decoded reply."""
    for sub in ("config", "data", "state", "cache", "tmp"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            # --clean: ~/.config/nvim (a symlink to the main checkout) would
            # otherwise be on the runtimepath, ahead of package.path.
            [NVIM, "--clean", "-l", str(SCENARIO_LUA), str(REPO_ROOT)],
            input=json.dumps({"scenario": name}),
            capture_output=True,
            text=True,
            timeout=FREEZE_TIMEOUT,
            # PATH is emptied so no AI CLI can be found, let alone run; every
            # location nvim writes to is under the test's own directory.
            env={
                "PATH": "",
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / "config"),
                "XDG_DATA_HOME": str(home / "data"),
                "XDG_STATE_HOME": str(home / "state"),
                "XDG_CACHE_HOME": str(home / "cache"),
                "TMPDIR": str(home / "tmp"),
            },
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"scenario {name!r} FROZE: no result within {FREEZE_TIMEOUT}s "
            "(focus traps ping-ponging in vim.schedule?)"
        )
    replies = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert replies, (
        f"scenario {name!r} exited {proc.returncode} with no reply\n"
        f"stderr: {proc.stderr}"
    )
    reply = json.loads(replies[-1])
    if reply.get("loop"):
        pytest.fail(
            f"scenario {name!r}: focus ping-pong, {reply['flips']} WinEnter "
            "events and still going"
        )
    assert proc.returncode == 0, (
        f"scenario {name!r} exited {proc.returncode}\n"
        f"reply: {reply}\nstderr: {proc.stderr}"
    )
    return reply


@pytest.fixture(scope="module")
def scenario(tmp_path_factory):
    """`scenario(name)` -> reply. Each scenario runs once per module.

    A scenario is hermetic, so sharing one run between the tests that assert
    different fields of it costs no independence. Failures are cached too: a
    freeze costs FREEZE_TIMEOUT seconds, and re-running it once per test would
    make a broken tree take minutes to report.
    """
    cache = {}

    def run(name):
        if name not in cache:
            try:
                cache[name] = ("ok", _run(tmp_path_factory.mktemp(name), name))
            # pytest.fail raises its own BaseException subclass; KeyboardInterrupt
            # must still stop the run rather than be cached as a failure.
            except (Exception, pytest.fail.Exception) as exc:
                cache[name] = ("fail", str(exc))
        status, payload = cache[name]
        if status == "fail":
            pytest.fail(payload, pytrace=False)
        # The scenarios exist to test THIS tree. Anything loaded from elsewhere
        # (the author's ~/.config/nvim is a symlink to the main checkout) would
        # make every assertion below about some other file.
        for module, src in payload["sources"].items():
            if src is not None:
                assert src.startswith(f"@{LUA_TREE}/"), (
                    f"{module} was loaded from {src}, not from {LUA_TREE}"
                )
        assert payload["sources"]["ui"] is not None
        assert payload["errmsg"] == "", f"an error was raised: {payload['errmsg']!r}"
        assert payload["flips"] < MAX_FLIPS, (
            f"{payload['flips']} WinEnter events: focus is oscillating"
        )
        return payload

    return run


class TestTwoClustersAtOnce:
    """RED on the unfixed code: opening the second popup starts the ping-pong.

    Reproduced there as 400 WinEnter events within a tick, and as a hang with
    no cap.
    """

    def test_a_second_popup_takes_focus_and_keeps_it(self, scenario):
        got = scenario("two_popups")
        assert got["after_open"] == got["b"]

    def test_leaving_to_the_editor_returns_to_the_newest_popup(self, scenario):
        got = scenario("two_popups")
        assert got["leave_to_editor"] == got["b"]

    def test_focus_may_move_between_two_live_popups(self, scenario):
        """Both are live traps; moving from one to the other is not "leaving"."""
        got = scenario("two_popups")
        assert got["enter_a"] == got["a"]

    def test_the_popup_focused_last_is_the_one_returned_to(self, scenario):
        got = scenario("two_popups")
        assert got["leave_from_a"] == got["a"]

    def test_a_two_pane_cluster_and_a_popup_coexist(self, scenario):
        got = scenario("diff_and_popup")
        assert got["after_open"] == got["popup"]
        assert got["leave_to_editor"] == got["popup"]

    def test_the_pane_that_last_had_focus_is_remembered_per_cluster(self, scenario):
        got = scenario("diff_and_popup")
        assert got["enter_original"] == got["original"]
        assert got["leave_from_original"] == got["original"]
        assert got["enter_response"] == got["response"]
        assert got["leave_from_response"] == got["response"]


class TestPromptWindowOverAPopup:
    """RED on the unfixed code: the user's own report.

    `V` then `<C-c>` inside an open popup opens the replace prompt (a second
    trap) over the popup (the first), through the real mapping.
    """

    def test_the_prompt_is_what_opened(self, scenario):
        got = scenario("prompt_over_popup")
        assert got["prompt_filetype"] == "markdown"
        assert got["prompt"] not in (got["main"], got["a"])

    def test_the_prompt_keeps_focus(self, scenario):
        got = scenario("prompt_over_popup")
        assert got["after_open"] == got["prompt"]
        assert got["leave_to_editor"] == got["prompt"]

    def test_focus_may_move_from_the_prompt_to_the_popup_and_stays(self, scenario):
        got = scenario("prompt_over_popup")
        assert got["enter_a"] == got["a"]
        assert got["leave_from_a"] == got["a"]

    def test_cancelling_the_prompt_leaves_the_popup_trapping(self, scenario):
        got = scenario("prompt_over_popup")
        assert got["prompt_valid_after_q"] is False
        assert got["after_q"] == got["a"]
        assert got["leave_after_q"] == got["a"]


class TestClosingOneOfTwo:
    """RED on the unfixed code (it loops while the second popup opens)."""

    def test_the_survivor_still_traps_and_the_closed_one_does_not(self, scenario):
        got = scenario("close_one_leave_other")
        assert got["b_valid_after_q"] is False
        assert got["leave_after_closing_b"] == got["a"]

    def test_with_everything_closed_focus_is_free_and_nothing_is_targeted(
        self, scenario
    ):
        got = scenario("close_one_leave_other")
        assert got["a_valid_after_q"] is False
        assert got["leave_after_closing_all"] == got["main"]
        assert got["window_count"] == 1

    def test_a_snap_never_targets_a_cluster_closed_before_it_ran(self, scenario):
        got = scenario("target_closed_before_callback")
        assert got["after_closing_newest"] == got["a"]
        assert got["after_closing_all"] == got["main"]


class TestPendingSnapBacks:
    def test_focus_back_inside_a_cluster_before_the_snap_runs_is_left_alone(
        self, scenario
    ):
        """RED on the unfixed code (two clusters).

        Without the re-check at callback time the pending snap would drag focus
        to the newest cluster, undoing a move the user already made.
        """
        got = scenario("refocus_before_callback")
        assert got["after_refocus"] == got["a"]
        assert got["leave_from_a"] == got["a"]

    def test_the_recheck_does_not_depend_on_a_win_enter_having_fired(self, scenario):
        """Entering a cluster normally also makes it the newest, which would
        hide a missing re-check; a `:noautocmd` focus change is not announced."""
        got = scenario("refocus_before_callback")
        assert got["after_unannounced_refocus"] == got["a"]

    def test_a_burst_of_focus_changes_schedules_and_costs_one_snap(self, scenario):
        """RED on the unfixed code: one scheduled callback per WinEnter."""
        got = scenario("burst_is_one_snap")
        assert got["scheduled"] == 1
        assert got["snaps"] == 1
        assert got["after"] == got["a"]

    def test_a_snap_that_fails_is_silent_and_the_trap_still_works_after(self, scenario):
        got = scenario("failed_snap_does_not_disarm")
        assert got["after_failed_snap"] == got["main"]
        assert got["after_second_try"] == got["a"]


class TestOneTrapIsUnchanged:
    """Characterization: passes on the unfixed code, and must keep passing."""

    def test_a_popup_sends_focus_back_until_it_is_closed(self, scenario):
        got = scenario("single_popup")
        assert got["after_open"] == got["a"]
        assert got["leave"] == got["a"]
        assert got["a_valid_after_q"] is False
        assert got["leave_after_close"] == got["main"]

    def test_a_two_pane_cluster_returns_to_the_pane_last_focused(self, scenario):
        got = scenario("single_diff")
        assert got["after_open"] == got["response"]
        assert got["leave_from_response"] == got["response"]
        assert got["enter_original"] == got["original"]
        assert got["leave_from_original"] == got["original"]

    def test_closing_a_two_pane_cluster_closes_both_and_frees_focus(self, scenario):
        got = scenario("single_diff")
        assert got["response_valid_after_q"] is False
        assert got["original_valid_after_q"] is False
        assert got["leave_after_close"] == got["main"]

    def test_the_prompt_window_traps_until_it_is_cancelled(self, scenario):
        got = scenario("prompt_alone")
        assert got["after_open"] == got["prompt"]
        assert got["leave_to_editor"] == got["prompt"]
        assert got["prompt_valid_after_q"] is False
        assert got["leave_after_q"] == got["main"]


class TestTabpages:
    """Focus is never pulled across tabpages.

    The unfixed code yanked you back to the popup's tab the moment you changed
    tab (nvim_set_current_win on a window of another tab switches tab), so a
    popup left open in one tab made every other tab unreachable.
    """

    def test_changing_tab_is_not_undone(self, scenario):
        """RED on the unfixed code."""
        got = scenario("tab_single")
        assert got["stayed_in_tab2"] is True
        assert got["cur_is_a"] is False

    def test_the_trap_still_holds_in_its_own_tab(self, scenario):
        got = scenario("tab_single")
        assert got["back_in_tab1"] is True
        assert got["cur_after_return"] == got["a"]
        assert got["leave_in_tab1"] == got["a"]

    def test_a_trap_snaps_within_the_current_tab_not_to_the_newest_cluster(
        self, scenario
    ):
        """RED on the unfixed code.

        b (tab 2) is the most recently focused cluster overall, but a user
        sitting in tab 1's editor belongs to a, which is in the same tab.
        """
        got = scenario("tabs_two_clusters")
        assert got["leave_b"] == got["b"]
        assert got["in_tab1"] is True
        assert got["in_tab2"] is False
        assert got["cur_after_tab1"] == got["a"]


class TestRegistry:
    """The module's contract, with neither ui.lua nor init.lua in the way.

    RED on the unfixed code: there is no ai/focus_trap.lua to load.
    """

    def test_nothing_to_trap_registers_nothing(self, scenario):
        got = scenario("registry")
        assert got["register_empty_is_nil"] is True
        assert got["register_invalid_is_nil"] is True

    def test_unregistering_nothing_or_twice_is_harmless(self, scenario):
        got = scenario("registry")
        assert got["unregister_nil_ok"] is True
        assert got["unregister_twice_ok"] is True

    def test_the_newest_registered_cluster_wins_then_the_older(self, scenario):
        got = scenario("registry")
        assert got["leave_both"] == got["w2"]
        assert got["leave_after_unregistering_newest"] == got["w1"]

    def test_with_every_cluster_unregistered_focus_is_free(self, scenario):
        got = scenario("registry")
        assert got["leave_after_unregistering_all"] == got["main"]

    def test_a_window_closed_without_unregistering_is_not_targeted(self, scenario):
        got = scenario("registry")
        assert got["leave_after_closing_registered"] == got["main"]
