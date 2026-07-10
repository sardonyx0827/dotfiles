"""Tests for utility scripts and syntax checks for all shell configs."""

import os
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

TMUX_SCRIPT = REPO_ROOT / "scripts/tmux_send_to_all_except_nvim.sh"
UPDATE_SCRIPT = REPO_ROOT / "scripts/update_ai_tools.sh"
ZSHRC = REPO_ROOT / ".zshrc"

OWN_BASH_SCRIPTS = sorted(
    [
        REPO_ROOT / "install.sh",
        REPO_ROOT / "scripts/update_ai_tools.sh",
        REPO_ROOT / "scripts/tmux_send_to_all_except_nvim.sh",
        REPO_ROOT / ".claude/statusline-command.sh",
        *(REPO_ROOT / ".claude/hooks").glob("*.sh"),
        *(REPO_ROOT / ".codex/hooks").glob("*.sh"),
    ]
)


class TestTmuxSendToAllExceptNvim:
    def _stub_tmux(self, shell_env, sync_state: str):
        body = (
            'case "$1" in\n'
            f'  show-window-option) echo "{sync_state}" ;;\n'
            "  list-panes) printf '%%1 zsh\\n%%2 nvim\\n%%3 vim\\n' ;;\n"
            "esac"
        )
        shell_env.stub("tmux", body=body)

    def test_sends_to_all_panes_except_nvim(self, shell_env):
        self._stub_tmux(shell_env, sync_state="off")
        res = shell_env.run(TMUX_SCRIPT, "echo", "hello")
        assert res.returncode == 0
        send_calls = [c for c in shell_env.calls if "send-keys" in c]
        assert "tmux send-keys -t %1 echo hello" in send_calls
        assert "tmux send-keys -t %3 echo hello" in send_calls
        assert not any("-t %2" in c for c in send_calls)

    def test_sync_off_state_is_not_toggled(self, shell_env):
        self._stub_tmux(shell_env, sync_state="off")
        shell_env.run(TMUX_SCRIPT, "ls")
        assert not any("set-window-option" in c for c in shell_env.calls)

    def test_sync_on_is_suspended_and_restored(self, shell_env):
        self._stub_tmux(shell_env, sync_state="on")
        shell_env.run(TMUX_SCRIPT, "ls")
        calls = shell_env.calls
        off_idx = calls.index("tmux set-window-option synchronize-panes off")
        on_idx = calls.index("tmux set-window-option synchronize-panes on")
        send_idx = [i for i, c in enumerate(calls) if "send-keys" in c]
        assert off_idx < min(send_idx)
        assert on_idx > max(send_idx)


class TestUpdateAiTools:
    def test_updates_every_tool(self, shell_env):
        for tool in ("claude", "codex", "gemini", "copilot", "npm"):
            shell_env.stub(tool)
        res = shell_env.run(UPDATE_SCRIPT)
        assert res.returncode == 0
        expected = [
            "claude update",
            "npm update -g @openai/codex",
            "npm upgrade -g @google/gemini-cli",
            "copilot update",
            "claude --version",
            "codex --version",
            "gemini --version",
            "copilot --version",
        ]
        for call in expected:
            assert call in shell_env.calls


class TestSyntax:
    @pytest.mark.parametrize(
        "script", OWN_BASH_SCRIPTS, ids=lambda p: str(p.relative_to(REPO_ROOT))
    )
    def test_bash_syntax(self, script):
        res = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True, timeout=30
        )
        assert res.returncode == 0, res.stderr

    def test_zshrc_syntax(self):
        if shutil.which("zsh") is None:
            pytest.skip("zsh not installed")
        res = subprocess.run(
            ["zsh", "-n", str(REPO_ROOT / ".zshrc")],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert res.returncode == 0, res.stderr


class TestUpdateAiToolsFunction:
    """Exercises .zshrc's update_ai_tools() in isolation.

    The function is extracted (not the whole .zshrc, which unconditionally
    sources oh-my-zsh.sh and would need a real Oh My Zsh install) and eval'd
    under zsh with a fake HOME so the ~/.zshrc :A resolution can be verified
    hermetically.
    """

    def _extract_function(self, name: str) -> str:
        text = ZSHRC.read_text(encoding="utf-8")
        start = text.index(f"function {name}()")
        depth = 0
        started = False
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth == 0:
                    return text[start : i + 1]
        raise AssertionError(f"could not find end of function {name}() in .zshrc")

    def test_resolves_dotfiles_dir_from_symlinked_zshrc(self, tmp_path):
        if shutil.which("zsh") is None:
            pytest.skip("zsh not installed")

        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (checkout / ".zshrc").write_text("# stub\n", encoding="utf-8")
        marker = tmp_path / "ran.marker"
        script = checkout / "scripts" / "update_ai_tools.sh"
        script.parent.mkdir()
        script.write_text(f'#!/bin/sh\necho ran >"{marker}"\n', encoding="utf-8")
        script.chmod(0o755)

        home = tmp_path / "home"
        home.mkdir()
        (home / ".zshrc").symlink_to(checkout / ".zshrc")

        func_src = self._extract_function("update_ai_tools")
        env = {**os.environ, "HOME": str(home)}
        res = subprocess.run(
            ["zsh", "-c", f"{func_src}\nupdate_ai_tools"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

        assert res.returncode == 0, res.stderr
        assert marker.exists()

    def test_reports_error_when_dotfiles_checkout_not_found(self, tmp_path):
        if shutil.which("zsh") is None:
            pytest.skip("zsh not installed")

        # ~/.zshrc points nowhere near a dotfiles checkout with the script.
        home = tmp_path / "home"
        home.mkdir()
        (home / ".zshrc").write_text("# not a symlink\n", encoding="utf-8")

        func_src = self._extract_function("update_ai_tools")
        env = {**os.environ, "HOME": str(home)}
        res = subprocess.run(
            ["zsh", "-c", f"{func_src}\nupdate_ai_tools"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

        assert res.returncode != 0
        assert res.stderr.strip() != ""
