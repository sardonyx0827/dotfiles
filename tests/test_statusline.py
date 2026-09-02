"""Tests for .claude/statusline-command.sh (Rose Pine status line)."""

import datetime
import json
import re

from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / ".claude/statusline-command.sh"

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def render(shell_env, data: dict) -> str:
    res = shell_env.run(SCRIPT, stdin=json.dumps(data))
    assert res.returncode == 0
    return ANSI.sub("", res.stdout.strip())


def base_input(shell_env, **overrides) -> dict:
    data = {
        "workspace": {"current_dir": str(shell_env.home)},
        "model": {"display_name": "claude-opus-4"},
        "context_window": {"used_percentage": 42.7},
        "cost": {"total_cost_usd": 1.5},
    }
    data.update(overrides)
    return data


class TestStatusline:
    def test_deep_directory_is_shortened(self, shell_env):
        deep = shell_env.home / "projects" / "deep" / "mydir"
        deep.mkdir(parents=True)
        out = render(
            shell_env, base_input(shell_env, workspace={"current_dir": str(deep)})
        )
        assert "~/../deep/mydir" in out

    def test_shallow_directory_is_kept(self, shell_env):
        target = shell_env.home / "projects"
        target.mkdir()
        out = render(
            shell_env, base_input(shell_env, workspace={"current_dir": str(target)})
        )
        assert "~/projects" in out

    def test_model_name_is_prettified(self, shell_env):
        out = render(shell_env, base_input(shell_env))
        assert "Opus 4" in out

    def test_missing_model_shows_placeholder(self, shell_env):
        out = render(shell_env, base_input(shell_env, model={}))
        assert "--" in out

    def test_context_percentage_is_truncated_to_integer(self, shell_env):
        out = render(shell_env, base_input(shell_env))
        assert "ctx 42%" in out

    def test_missing_context_is_omitted(self, shell_env):
        out = render(shell_env, base_input(shell_env, context_window={}))
        assert "ctx" not in out

    def test_cost_is_formatted_with_four_decimals(self, shell_env):
        out = render(shell_env, base_input(shell_env))
        assert "$1.5000" in out

    def test_git_branch_and_untracked_count(self, shell_env, git_repo):
        (git_repo / "wip.txt").write_text("wip\n", encoding="utf-8")
        out = render(
            shell_env, base_input(shell_env, workspace={"current_dir": str(git_repo)})
        )
        assert "main" in out
        assert "?1" in out

    def test_rate_limit_with_reset_time(self, shell_env):
        resets_at = 1780000000
        expected = datetime.datetime.fromtimestamp(resets_at).strftime("%H:%M")
        out = render(
            shell_env,
            base_input(
                shell_env,
                rate_limits={
                    "five_hour": {
                        "used_percentage": 85.2,
                        "resets_at": resets_at,
                    }
                },
            ),
        )
        assert "rl 85%" in out
        assert f"↺{expected}" in out

    def test_backslashes_in_the_directory_are_shown_literally(self, shell_env):
        """printf %b read the cwd's backslashes as escapes.

        macOS allows `\\` in a directory name; `\\c` told %b to stop printing,
        so everything after it -- model, ctx, git, cost and the trailing reset --
        vanished and the terminal was left coloured. `\\t` became a tab.
        """
        weird = shell_env.home / "src" / "c\\components"
        weird.mkdir(parents=True)
        out = render(
            shell_env, base_input(shell_env, workspace={"current_dir": str(weird)})
        )
        assert "c\\components" in out
        assert "Opus 4" in out, f"output truncated at the backslash: {out!r}"
        assert "$1.5000" in out

    def test_a_backslash_t_in_the_directory_is_not_a_tab(self, shell_env):
        weird = shell_env.home / "x\\ty"
        weird.mkdir(parents=True)
        out = render(
            shell_env, base_input(shell_env, workspace={"current_dir": str(weird)})
        )
        assert "x\\ty" in out
        assert "\t" not in out
