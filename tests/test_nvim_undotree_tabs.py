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

A third defect outlived that fix and is covered here too: the cleanup augroup
was a fixed literal created with `clear = true`, so opening a SECOND undo-diff
deleted the first tab's BufWipeout and TabClosed handlers. The first diff was
then unarmed -- wiping its scratch buffer closed that buffer's window and left
the tab standing, showing the real buffer still in diff mode, with nothing left
to close it. Every scenario predating this called open_vimdiff exactly once,
which is why a green suite never noticed.

A fourth is the same defect class as that third one -- per-invocation state
colliding on a shared handle -- reached through the keymap instead. The
`<C-w>q` on the user's real buffer is registered with { buf = target_buf }, and
target_buf is shared across invocations: a second undo-diff re-registers the
same {buf, mode, lhs} slot and vim.keymap.set silently REPLACES the first
mapping. Pressing the documented key in the FIRST diff tab then ran the SECOND
invocation's callback, whose tab-identity check failed, so it took the
"not in a diff tab" fallback and issued a bare `:quit` -- closing one window and
leaving that tab standing, still in diff mode, with its cleanup autocmds armed
for a diff nobody can reach any more. The mappings on the scratch buffer are not
affected: that buffer is fresh on every call, so only the shared one collides.

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


class TestASecondDiffDoesNotDisarmTheFirst:
    def test_the_first_diffs_cleanup_autocmds_survive_a_second_diff(self):
        # The defect itself, measured directly. Both handlers of call 1 have to
        # still be registered once call 2 has opened its own diff -- by autocmd
        # id, since the old fixed group name made the two calls' entries
        # indistinguishable by name (see cleanup_autocmd_ids in the harness).
        for name in (
            "close_first_diff_after_second_open",
            "wipe_first_scratch_after_second_open",
        ):
            res = scenario(name)
            # Shape check on the HARNESS, not on the fix: this reads 2 with the
            # bug present or absent, because the defect deletes call 1's entries
            # only once call 2 has registered its own two under the same name.
            # It is here so a harness that silently stopped seeing any cleanup
            # autocmds cannot make the real assertion below vacuously true.
            assert res["first_call_autocmd_count"] == 2, (
                f"{name}: the first open registered "
                f"{res['first_call_autocmd_count']} cleanup autocmds, expected 2"
            )
            assert res["first_call_autocmds_survived"] == 2, (
                f"{name}: opening a second diff deleted the first one's cleanup "
                f"autocmds ({res['first_call_autocmds_survived']}/2 survived)"
            )

    def test_wiping_the_first_scratch_buffer_still_closes_its_tab(self):
        # The harm, stated without reference to the mechanism: with its
        # BufWipeout handler erased, nothing closes the first diff tab when its
        # scratch buffer goes. The wipe takes the scratch window with it and
        # strands the tab -- one window, the user's real buffer, diff still on,
        # and no `<C-w>q` mapping left that knows how to unwind it.
        #
        # This is the load-bearing assertion of the pair. Its sibling below
        # turns on the diffoff sweep, which reaches windows in OTHER tabs;
        # `diff_tab_valid` does not, so it keeps pinning the augroup fix even if
        # that sweep is ever narrowed.
        res = scenario("wipe_first_scratch_after_second_open")
        assert not res["close_err"], f"the wipe raised: {res['close_err']}"
        assert not res["diff_tab_valid"], (
            "the first diff tab was left stranded after its scratch buffer was wiped"
        )
        assert res["second_diff_tab_valid"], (
            "wiping the first diff's scratch buffer closed the second diff's tab"
        )
        assert res["user_tab_valid"]
        assert res["target_buf_valid"]

    def test_closing_the_first_diff_tab_leaves_the_second_diff_intact(self):
        # The documented way out of the first diff, taken while the second is
        # open. Unwinding one diff must stop at its own tab.
        #
        # This assertion used to read `not any(target_still_in_diff_mode)`, from
        # back when one diff at a time was the only case that worked. Closing
        # the first tab destroys BOTH of its windows, so the only window that
        # list could still be reporting on is the SECOND diff's -- meaning the
        # old form required the first diff's cleanup to reach across tabs and
        # switch off a diff that is still live, leaving that tab with its
        # scratch side diffthis and its target side not: no highlighting on
        # either. The sweep is now scoped to the closing diff's own tab.
        #
        # 862244b's regression (a shared augroup, so the first diff's TabClosed
        # handler was deleted by the second open and its cleanup never ran) is
        # still pinned, by `diff_tab_valid` in the sibling test above -- which
        # that test's own comment calls out as independent of this sweep for
        # exactly this reason.
        res = scenario("close_first_diff_after_second_open")
        assert not res["close_err"], (
            f"closing the first diff tab raised: {res['close_err']}"
        )
        assert not res["diff_tab_valid"]
        assert res["user_tab_valid"]
        assert res["second_diff_tab_valid"], (
            "closing the first diff tab closed the second diff's tab"
        )
        assert res["second_diff_target_in_diff_mode"] is True, (
            "closing the first diff tab switched off diff mode in the SECOND "
            "diff's target window, leaving that tab half-diffed"
        )


class TestTheSharedTargetBufferKeymapActsOnTheDiffYouAreIn:
    """`<C-w>q` on the user's real buffer, with two diffs open on that buffer.

    One buffer-local mapping slot, two live diffs: the second registration wins
    and the first diff's documented way out ran the wrong invocation's callback.
    The existing two-diff scenarios leave via :tabclose or :bwipeout, so none of
    them touches the mapping -- which is why this survived a green suite.
    """

    def test_pressing_it_in_the_first_diff_tab_closes_that_diff(self):
        # The defect, stated as the harm: the tab the key was pressed in has to
        # be the tab that closes, whichever invocation registered the mapping
        # that is currently installed.
        res = scenario("close_first_diff_via_target_keymap_after_second_open")
        assert not res["diff_tab_valid"], (
            "<C-w>q in the first diff tab did not close it -- it was left "
            f"standing with {res['first_diff_win_count']} window(s)"
        )
        assert not res["first_scratch_valid"], (
            "the first diff's scratch buffer outlived its tab"
        )
        assert res["second_diff_tab_valid"], (
            "closing the first diff took the second diff's tab with it"
        )
        assert res["user_tab_valid"]
        assert res["target_buf_valid"]

    def test_the_second_diff_can_still_be_closed_the_same_way(self):
        # The other half, and the reason the cleanup cannot simply delete the
        # mapping: it lives on a buffer that may still be hosting another live
        # diff. Deleting it there leaves the second tab with no documented exit
        # -- <C-w>q would fall back to its builtin meaning and close one window
        # of a still-diffed tab.
        res = scenario("close_both_diffs_via_target_keymap")
        assert not res["diff_tab_valid"], "the first diff tab was left open"
        assert not res["second_diff_tab_valid"], (
            "<C-w>q did not close the second diff tab -- the first diff's "
            "cleanup removed the mapping they share"
        )
        assert res["tab_count"] == 1, (
            f"{res['tab_count']} tabs left; only the user's should remain"
        )
        assert res["user_tab_valid"]
        assert res["target_buf_valid"]
        assert not any(res["target_still_in_diff_mode"]), (
            "the target buffer stayed in diff mode after both diffs closed"
        )

    def test_outside_a_diff_tab_it_is_still_an_ordinary_quit(self):
        # The documented fallback, which per-diff dispatch must not eat: in the
        # user's own tab the key closes a window and nothing else. A dispatch
        # that guessed at "some live diff" instead of "the diff I am in" would
        # close a diff tab from here.
        res = scenario("target_keymap_outside_a_diff_tab")
        assert res["user_tab_valid"], "the fallback :quit took the user's tab"
        assert res["user_tab_win_count"] == 1, (
            "the fallback did not close exactly one window "
            f"({res['user_tab_win_count']} left of 2)"
        )
        assert res["diff_tab_valid"], (
            "pressing <C-w>q outside the diff tab closed the diff tab"
        )
        assert res["tab_count"] == 2


class TestTargetBufferSelection:
    """find_target_buf must pick the FILE, not whichever window comes first.

    Side panels -- nvim-tree, a terminal, quickfix -- sit in the first window
    of the tab far more often than not, and the scan excluded only the four
    undotree filetypes. So `<C-d>` with nvim-tree open diffed the tree buffer
    and failed with E830 instead of opening anything.
    """

    def test_a_nofile_side_panel_in_the_first_window_is_skipped(self):
        res = scenario("side_panel_first_in_tab")
        assert res["diff_tab_valid"], "no diff tab was opened at all"
        # The diff tab's right-hand window shows the real buffer in diff mode.
        assert True in res["target_still_in_diff_mode"], (
            "the diff was not opened on the target buffer"
        )


class TestBothChordSpellingsCloseTheDiff:
    def test_the_control_form_on_the_target_side_closes_the_diff_too(self):
        # cleanup_diff already deletes BOTH `<C-w>q` and `<C-w><C-q>` from the
        # target buffer; only the letter form was ever bound there.
        res = scenario("close_via_target_ctrl_chord")
        assert not res["diff_tab_valid"], "the diff tab was left standing"
        assert res["tab_count"] == 1
        assert not any(res["target_still_in_diff_mode"])
