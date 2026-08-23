"""Tab-lifecycle tests for `.config/nvim/lua/setup/functions/undotree_vimdiff.lua`.

`M.open_vimdiff()` opens a scratch-vs-current diff in its OWN tab and registers
cleanup so the diff can be unwound again. Two independent defects made that
cleanup destroy tabs it was never asked to touch:

- `close_diff_tab` ran a bare `:tabclose`, which closes whatever tab is CURRENT.
  Its only guard was that `diff_tab` is still *valid* -- never that it is the
  tab being closed.
- the `TabClosed` autocmd carried no `pattern`, so it fired on EVERY tab close
  in the session.

Together: closing an unrelated tab ran the callback, which then closed the
current tab. With the user sitting in their own working tab, that tab is what
disappeared. Buffer contents survive (nothing is unsaved-lost), but the window
layout the user built is gone and there is no undo for that.

These run the module for real under `nvim -l` (no init.lua, so no plugin
manager) via tests/lua/undotree_tabs.lua, because the behaviour under test is a
side effect on other tabs rather than a return value.
"""

import json
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

HARNESS = REPO_ROOT / "tests/lua/undotree_tabs.lua"

pytestmark = pytest.mark.skipif(
    shutil.which("nvim") is None, reason="nvim not installed"
)


def scenario(name: str) -> dict:
    proc = subprocess.run(
        ["nvim", "-l", str(HARNESS), str(REPO_ROOT), name],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"harness failed: {proc.stderr}"
    # open_vimdiff prints a notification banner; the JSON reply is the last line.
    line = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")][-1]
    result = json.loads(line)
    assert result["ok"], result.get("err")
    return result


class TestUnrelatedTabsAreLeftAlone:
    def test_closing_an_unrelated_tab_keeps_the_users_tab(self):
        # Tabs {user, diff, unrelated}; user is in their own tab and closes the
        # unrelated one. Before the fix this left {diff} -- the user's working
        # tab was the collateral, because `:tabclose` closed the CURRENT tab.
        res = scenario("close_unrelated_from_user_tab")
        assert res["user_tab_valid"], (
            "closing an unrelated tab destroyed the user's working tab"
        )
        assert res["diff_tab_valid"], "the diff tab was closed without being asked"
        assert not res["other_tab_valid"], "the tab the user closed is still open"
        assert res["tab_count"] == 2
        assert not res["close_err"], (
            f"the user's own :tabclose raised: {res['close_err']}"
        )

    def test_closing_an_unrelated_tab_keeps_the_diff_tab(self):
        # Same layout, closed from inside the unrelated tab. Before the fix
        # nvim landed on the diff tab and the callback closed that instead.
        res = scenario("close_unrelated_from_that_tab")
        assert res["user_tab_valid"]
        assert res["diff_tab_valid"], (
            "the diff tab was closed as collateral of an unrelated tab close"
        )
        assert res["tab_count"] == 2

    def test_target_buffer_is_never_lost(self):
        # The bug costs a window layout, not file contents. Pin that, so a
        # future change cannot quietly turn it into data loss.
        for name in (
            "close_unrelated_from_user_tab",
            "close_unrelated_from_that_tab",
            "close_the_diff_tab",
            "wipe_the_scratch_buffer",
            "wipe_scratch_from_user_tab",
        ):
            assert scenario(name)["target_buf_valid"], name


class TestClosingTheDiffTabStillCleansUp:
    def test_diff_tab_closes_and_diff_mode_is_unwound(self):
        # The guard must not be so tight that the supported exit stops working:
        # closing the diff tab has to leave the target buffer out of diff mode,
        # otherwise the user is left with a permanently diffed window.
        res = scenario("close_the_diff_tab")
        assert not res["diff_tab_valid"]
        assert res["user_tab_valid"]
        assert res["tab_count"] == 1
        assert not any(res["target_still_in_diff_mode"]), (
            "target buffer stayed in diff mode after the diff tab closed"
        )
        # This assertion is the point of the scenario, not decoration. The
        # scratch buffer is bufhidden=wipe, so closing the tab wipes it, which
        # fires BufWipeout, which re-entered `:tabclose` inside the close that
        # was still unwinding -- surfacing E937 to the user on the exit path the
        # code comment names. Carrying the same assertion its two siblings carry
        # is what keeps that from coming back.
        assert not res["close_err"], f"closing the diff tab raised: {res['close_err']}"

    def test_wiping_the_scratch_buffer_closes_the_diff_tab(self):
        # The case the BufWipeout autocmd exists for, and the only one where
        # cleanup runs with from_wipeout set AND still has a tab to close. The
        # `not from_wipeout` guard must not turn this into a no-op that strands
        # a half-diffed tab.
        res = scenario("wipe_the_scratch_buffer")
        assert not res["close_err"], (
            f"wiping the scratch buffer raised: {res['close_err']}"
        )
        assert not res["diff_tab_valid"], "the diff tab was left open"
        assert res["user_tab_valid"]
        assert res["target_buf_valid"]
        assert not any(res["target_still_in_diff_mode"]), (
            "target buffer stayed in diff mode after the scratch buffer was wiped"
        )

    def test_wiping_the_scratch_buffer_from_the_users_tab_closes_the_right_tab(self):
        # The only scenario where the tab that needs closing is NOT the current
        # one, which makes it the only one that can tell `tabclose <n>` apart
        # from a bare `tabclose` -- the exact distinction the headline fix turns
        # on. Every other scenario passes either way, so without this the
        # original bug (the user's working tab closing instead) could come back
        # unnoticed. `:bwipeout <n>` / `:%bwipeout` from elsewhere is the normal
        # way a buffer gets wiped; nothing requires looking at it.
        res = scenario("wipe_scratch_from_user_tab")
        assert not res["close_err"], f"the wipe raised: {res['close_err']}"
        assert res["user_tab_valid"], (
            "the user's working tab was closed instead of the diff tab"
        )
        assert not res["diff_tab_valid"], "the diff tab was left open"
        assert res["target_buf_valid"]
