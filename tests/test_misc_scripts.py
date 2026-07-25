"""Tests for utility scripts and syntax checks for all shell configs."""

import os
import re
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

TMUX_SCRIPT = REPO_ROOT / "scripts/tmux_send_to_all_except_nvim.sh"
UPDATE_SCRIPT = REPO_ROOT / "scripts/update_ai_tools.sh"
ZSHRC = REPO_ROOT / ".zshrc"

# Every function .zshrc defines. Sourcing the whole file is not an option: it
# unconditionally sources oh-my-zsh.sh and would need a real Oh My Zsh install,
# so each function is extracted and eval'd on its own under a stubbed PATH.
ZSHRC_FUNCTIONS = [
    "sshs",
    "cf",
    "vf",
    "dwc",
    "precmd",
    "update_ai_tools",
    "claude-teammates",
    "translate",
    "mc",
    "_mc",
]


def extract_zsh_function(name: str) -> str:
    """Return the source text of one function defined in .zshrc.

    .zshrc spells definitions three ways -- `vf () {`, `function mc() {` and
    `_mc() {` -- so match the shapes rather than one literal prefix. Anchoring
    at line start keeps `mc` from matching `_mc`.
    """
    text = ZSHRC.read_text(encoding="utf-8")
    opener = re.compile(
        rf"^(?:function\s+)?{re.escape(name)}\s*\(\)\s*\{{", re.MULTILINE
    )
    match = opener.search(text)
    if match is None:
        raise AssertionError(f"no definition of {name}() found in .zshrc")

    depth = 0
    started = False
    for index, char in enumerate(text[match.start() :], start=match.start()):
        if char == "{":
            depth += 1
            started = True
        elif char == "}":
            depth -= 1
            if started and depth == 0:
                return text[match.start() : index + 1]
    raise AssertionError(f"could not find end of {name}() in .zshrc")


def run_zsh_function(name: str, call: str, *, cwd=None, env=None):
    """Eval one extracted .zshrc function and invoke it."""
    return subprocess.run(
        ["zsh", "-c", f"{extract_zsh_function(name)}\n{call}"],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=30,
    )


def stub_bin(directory, name: str, body: str):
    """Drop an executable stub so the function under test cannot reach a real tool."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def path_env(bin_dir):
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


requires_zsh = pytest.mark.skipif(
    shutil.which("zsh") is None, reason="zsh not installed"
)

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
        # Enter is required so the sent text is actually executed, not just
        # typed into the pane's prompt.
        assert "tmux send-keys -t %1 echo hello Enter" in send_calls
        assert "tmux send-keys -t %3 echo hello Enter" in send_calls
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

    def test_sync_restored_even_if_a_send_keys_call_fails(self, shell_env):
        # Under `set -euo pipefail`, one failing send-keys inside the while
        # loop must not abort the script before the synchronize-panes
        # restore runs (and must not stop the remaining panes either).
        body = (
            'case "$1" in\n'
            '  show-window-option) echo "on" ;;\n'
            "  list-panes) printf '%%1 zsh\\n%%2 nvim\\n%%3 vim\\n' ;;\n"
            '  send-keys) [ "$3" = "%1" ] && exit 7 ;;\n'
            "esac"
        )
        shell_env.stub("tmux", body=body)
        res = shell_env.run(TMUX_SCRIPT, "echo", "hello")
        assert res.returncode == 0
        calls = shell_env.calls
        assert "tmux set-window-option synchronize-panes on" in calls
        assert any(c.startswith("tmux send-keys -t %3") for c in calls), (
            "a failed send-keys to one pane must not stop the remaining panes"
        )


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

        func_src = extract_zsh_function("update_ai_tools")
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

        func_src = extract_zsh_function("update_ai_tools")
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


@requires_zsh
@pytest.mark.parametrize("name", ZSHRC_FUNCTIONS)
def test_extracted_function_is_syntactically_complete(name):
    """Every extraction must be a complete, parseable function.

    The extractor counts braces and does not know about braces inside strings,
    comments or parameter expansions. Without this check, a mis-sliced body
    would surface as a confusing behavioural failure in the tests below
    instead of pointing at the extraction itself. It also fails loudly if a
    function is renamed or removed from .zshrc.
    """
    source = extract_zsh_function(name)
    res = subprocess.run(
        ["zsh", "-n"], input=source, capture_output=True, text=True, timeout=30
    )
    assert res.returncode == 0, f"{name}: extracted body does not parse: {res.stderr}"


@requires_zsh
class TestVf:
    """vf() picks a file with fzf, cd's to its directory, and opens it.

    Because it cd's first, the path handed to the editor has to be the
    basename. Reusing the original cwd-relative path made `src/foo` resolve to
    `src/src/foo` after the cd, opening an empty buffer for anything below the
    cwd (fixed in 11c35ac). These tests pin the composed result, not the
    argument shape, so they fail for any variant of that mistake.
    """

    def _run(self, tmp_path, selection):
        workdir = tmp_path / "work"
        (workdir / "src").mkdir(parents=True)
        (workdir / "src" / "foo.txt").write_text("content\n", encoding="utf-8")
        (workdir / "top.txt").write_text("content\n", encoding="utf-8")

        bin_dir = tmp_path / "bin"
        record = tmp_path / "opened"
        stub_bin(bin_dir, "fzf", f'printf "%s\\n" "{selection}"')
        stub_bin(bin_dir, "nvim", f'printf "%s\\n%s\\n" "$PWD" "$1" >"{record}"')

        res = run_zsh_function("vf", "vf", cwd=workdir, env=path_env(bin_dir))
        assert res.returncode == 0, res.stderr
        assert record.exists(), f"nvim was never invoked: {res.stderr}"
        cwd, arg = record.read_text(encoding="utf-8").splitlines()
        return workdir, cwd, arg

    def test_opens_a_file_below_the_cwd(self, tmp_path):
        workdir, cwd, arg = self._run(tmp_path, "src/foo.txt")

        assert os.path.realpath(cwd) == os.path.realpath(workdir / "src")
        assert arg == "foo.txt"
        # The assertion that actually encodes the bug: whatever cwd/arg pair
        # vf produces has to name a real file. The old code yielded
        # <work>/src + src/foo.txt, i.e. <work>/src/src/foo.txt -- absent.
        assert os.path.isfile(os.path.join(cwd, arg))

    def test_opens_a_file_in_the_cwd(self, tmp_path):
        workdir, cwd, arg = self._run(tmp_path, "top.txt")

        assert os.path.realpath(cwd) == os.path.realpath(workdir)
        assert arg == "top.txt"
        assert os.path.isfile(os.path.join(cwd, arg))

    def test_does_nothing_when_selection_is_empty(self, tmp_path):
        bin_dir = tmp_path / "bin"
        record = tmp_path / "opened"
        stub_bin(bin_dir, "fzf", "true")
        stub_bin(bin_dir, "nvim", f'echo ran >"{record}"')

        res = run_zsh_function("vf", "vf", cwd=tmp_path, env=path_env(bin_dir))

        assert res.returncode == 0, res.stderr
        assert not record.exists(), "aborting fzf must not open an editor"


@requires_zsh
class TestCf:
    """cf() picks a directory with fzf and cd's into it."""

    def test_changes_into_the_selected_directory(self, tmp_path):
        workdir = tmp_path / "work"
        (workdir / "nested" / "deep").mkdir(parents=True)
        bin_dir = tmp_path / "bin"
        stub_bin(bin_dir, "fzf", 'printf "%s\\n" "./nested/deep"')

        res = run_zsh_function("cf", "cf; pwd", cwd=workdir, env=path_env(bin_dir))

        assert res.returncode == 0, res.stderr
        assert os.path.realpath(res.stdout.strip()) == os.path.realpath(
            workdir / "nested" / "deep"
        )

    def test_stays_put_when_selection_is_empty(self, tmp_path):
        workdir = tmp_path / "work"
        workdir.mkdir()
        bin_dir = tmp_path / "bin"
        stub_bin(bin_dir, "fzf", "true")

        res = run_zsh_function("cf", "cf; pwd", cwd=workdir, env=path_env(bin_dir))

        assert res.returncode == 0, res.stderr
        assert os.path.realpath(res.stdout.strip()) == os.path.realpath(workdir)


@requires_zsh
class TestDwc:
    """dwc() wraps a recursive wget; the depth argument defaults to 5."""

    def _run(self, tmp_path, call):
        bin_dir = tmp_path / "bin"
        record = tmp_path / "wget-args"
        stub_bin(bin_dir, "wget", f'printf "%s\\n" "$*" >"{record}"')
        res = run_zsh_function("dwc", call, cwd=tmp_path, env=path_env(bin_dir))
        return res, record

    def test_rejects_a_missing_url_without_calling_wget(self, tmp_path):
        res, record = self._run(tmp_path, "dwc")

        assert res.returncode == 1
        assert "Usage:" in res.stderr, "usage must go to stderr, not stdout"
        assert res.stdout == ""
        assert not record.exists(), "no URL means wget must not run at all"

    def test_defaults_to_depth_5(self, tmp_path):
        res, record = self._run(tmp_path, "dwc https://example.com")

        assert res.returncode == 0, res.stderr
        assert "-l 5" in record.read_text(encoding="utf-8")

    def test_honours_an_explicit_depth(self, tmp_path):
        res, record = self._run(tmp_path, "dwc https://example.com 2")

        assert res.returncode == 0, res.stderr
        args = record.read_text(encoding="utf-8")
        assert "-l 2" in args
        assert "https://example.com" in args
