"""Tests for utility scripts and syntax checks for all shell configs."""

import html
import os
import pty
import re
import select
import shutil
import signal
import socket
import subprocess
import time

import pytest
from conftest import REPO_ROOT, run_git

TMUX_SCRIPT = REPO_ROOT / "scripts/tmux_send_to_all_except_nvim.sh"
UPDATE_SCRIPT = REPO_ROOT / "scripts/update_ai_tools.sh"
NEW_PROJECT_SCRIPT = REPO_ROOT / "scripts/new_project.sh"
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
    "__dotfiles_script",
    "update_ai_tools",
    "np",
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


def extract_zsh_functions(*names: str) -> str:
    """Return several .zshrc functions joined, for callers that need helpers.

    `np` and `update_ai_tools` both delegate to `__dotfiles_script`, which has
    to be in scope for the extracted body to run at all.
    """
    return "\n".join(extract_zsh_function(name) for name in names)


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


def extract_zsh_secrets_guard() -> str:
    """Return .zshrc's trailing ~/.zsh_secrets block.

    Not a function, so extract_zsh_function() cannot reach it: take the marker
    comment through EOF. Sourcing the whole .zshrc is no more possible here
    than anywhere else in this file, and the block's *exit status* is the
    whole point -- it is the last thing .zshrc runs, so it becomes .zshrc's.
    """
    text = ZSHRC.read_text(encoding="utf-8")
    index = text.find("~/.zsh_secrets")
    if index == -1:
        raise AssertionError("no ~/.zsh_secrets block found in .zshrc")
    block = text[text.rfind("\n", 0, index) + 1 :]
    # Slicing to EOF only extracts the guard while the guard *is* the tail, and
    # "runs last" is the whole invariant under test. Pin it here so appending to
    # .zshrc fails loudly instead of quietly widening what these tests execute.
    last = [line.strip() for line in block.splitlines() if line.strip()][-1]
    if last != "fi":
        raise AssertionError(
            f"the ~/.zsh_secrets guard no longer ends .zshrc (tail is {last!r}); "
            "whatever now runs last owns .zshrc's exit status instead"
        )
    return block


def extract_uim_fep_block() -> str:
    """Return .zshrc's uim-fep autostart block.

    Not a function either, and unlike the secrets guard it is not anchored to
    EOF, so take the marker comment through the `fi` that closes the single if.
    """
    text = ZSHRC.read_text(encoding="utf-8")
    index = text.find("## uim-fep")
    if index == -1:
        raise AssertionError("no uim-fep block found in .zshrc")
    rest = text[index:]
    end = re.search(r"^fi$", rest, re.MULTILINE)
    if end is None:
        raise AssertionError("uim-fep block has no closing fi")
    return rest[: end.end()]


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
        REPO_ROOT / "scripts/new_project.sh",
        REPO_ROOT / "scripts/tmux_send_to_all_except_nvim.sh",
        REPO_ROOT / ".claude/statusline-command.sh",
        *(REPO_ROOT / ".claude/hooks").glob("*.sh"),
        *(REPO_ROOT / ".codex/hooks").glob("*.sh"),
    ]
)


class TestTmuxLoggingBind:
    """`.tmux.conf`'s C-p logging bind must create its own output directory.

    The bind inlines its own `pipe-pane` shell command instead of calling the
    tmux-logging plugin's script, so it never benefits from that script's `mkdir -p`.
    install.sh only creates `~/.tmux`, not `~/.tmux/log`, so on a freshly installed
    machine the redirect failed with "No such file or directory" -- and because
    `pipe-pane` is chained with `\\; display-message "Logging start."`, tmux reported
    success anyway. The bug self-heals the moment a user presses the plugin's own
    M-p/M-P once, which is why it never showed up on an established machine.

    Asserted at text level: exercising the real bind needs a live tmux pane, and
    executing a string extracted from a config file is a pattern this repo's own
    bash-review hook and bandit both refuse -- correctly.
    """

    def test_logging_bind_creates_its_log_directory(self):
        conf = (REPO_ROOT / ".tmux.conf").read_text(encoding="utf-8")
        bind = [
            line for line in conf.splitlines() if line.startswith("bind C-p pipe-pane")
        ]
        assert bind, "the C-p logging bind is gone"
        (line,) = bind
        assert ".tmux/log" in line, "the logging bind no longer writes to ~/.tmux/log"
        # Must create the LOG directory, not merely some directory: `mkdir -p
        # ${HOME}/.tmux` (the parent install.sh already makes) satisfies a bare
        # "mkdir -p appears somewhere" check while leaving the original bug live.
        assert re.search(r"mkdir -p [^;]*\.tmux/log\b", line), (
            "the C-p logging bind redirects into ~/.tmux/log without creating that "
            "directory; on a fresh install it does not exist, the redirect fails, and "
            f"the chained display-message still claims logging started: {line}"
        )


class TestTmuxSendToAllExceptNvimBind:
    """`.tmux.conf` の `bind S` は、打ち込んだ文字列をシェル語に埋め込まない。

    旧実装は `run-shell "~/.tmux/...sh '%%'"` だった。tmux の `%%` は
    「打った文字列をそのまま貼り付ける」だけの置換で、貼り付け先がシングル
    クォートの内側だったため二つ壊れていた:

    1. `git commit -m 'wip'` と打つとユーザのクォートが黙って剥がれ、
       各ペインには `git commit -m wip` が別々の語として届く。
    2. `a'; echo INJECTED; '` と打つとシングルクォートが途中で閉じ、
       `echo INJECTED` が tmux サーバのシェルで走る。自分で自分を撃つ形
       とはいえ、正真正銘のコマンドインジェクション。

    修正後はシェル語への埋め込みを一切やめ、tmux のユーザオプション
    (`@send_to_all_except_nvim`) を伝言板に使う。`run-shell` に渡る文字列は
    定数になり、打った文字列は tmux のパーサ内だけで完結する。

    `%%` ではなく `%%%` でなければならない。`%%%` は貼り付ける文字列中の
    `"` `\\` `$` `;` `~` をエスケープする版で、これを `%%` に落とすと今度は
    tmux のパース層でインジェクションが復活する: `x" ; kill-server ; set -g @y "`
    と打てば `set-option` の文字列を抜けて tmux が `kill-server` を実行する。

    テキストレベルで検査する理由は TestTmuxLoggingBind と同じ。実際の bind を
    動かすには生きた tmux ペインが要り、設定ファイルから取り出した文字列を
    実行するのは本リポジトリの bash-review フックも bandit も (正しく) 拒む。
    """

    def _bind_line(self) -> str:
        conf = (REPO_ROOT / ".tmux.conf").read_text(encoding="utf-8")
        binds = [line for line in conf.splitlines() if line.startswith("bind S ")]
        assert binds, "the `bind S` send-to-all-panes bind is gone"
        (line,) = binds
        assert "command-prompt" in line, (
            f"`bind S` no longer prompts for the command to send: {line}"
        )
        return line

    def test_prompt_response_is_never_spliced_into_the_shell_command(self):
        line = self._bind_line()
        # `run-shell` hands its argument to /bin/sh. tmux offers no way to
        # shell-quote a prompt response, so the ONLY safe shape is a run-shell
        # argument that contains no substitution at all.
        run_shell = line[line.index("run-shell") :]
        assert "%" not in run_shell, (
            "the typed text is still spliced into the `run-shell` shell command; "
            "tmux's %% is a raw-text paste, so a typed quote breaks out of the "
            f"shell word and executes in the tmux server's shell: {line}"
        )

    def test_response_is_handed_over_through_a_tmux_option(self):
        line = self._bind_line()
        # The hand-off has to happen before run-shell, or the script reads a
        # stale value from the previous invocation.
        assert re.search(
            r'set(?:-option)?\s+-g\s+@send_to_all_except_nvim\s+"%%%"', line
        ), (
            "the prompt response is no longer stashed in the "
            f"@send_to_all_except_nvim tmux option as a quoted `%%%`: {line}"
        )
        assert line.index("@send_to_all_except_nvim") < line.index("run-shell"), (
            "the option must be set before run-shell starts the script"
        )

    def test_the_escaping_form_of_the_substitution_is_pinned(self):
        line = self._bind_line()
        # `%%%` escapes " \ $ ; ~ for tmux's OWN parser; `%%` does not, and a
        # typed `"` would then close set-option's argument and let the rest of
        # the line run as tmux commands.
        assert not re.search(r"(?<!%)%%(?!%)", line), (
            "`bind S` uses the non-escaping `%%` substitution; a typed double "
            "quote closes the set-option argument and the remainder of the "
            f"input is executed as tmux commands: {line}"
        )


class TestTmuxSendToAllExceptNvim:
    #: What `bind S` stashes the prompt response in. The script's only job on
    #: that path is to read it back out verbatim, so the payload below is
    #: deliberately built from every character that broke the old bind.
    NASTY = """git commit -m 'wip "x"' $HOME `id` ; echo hi \\ ~"""

    def _stub_tmux(self, shell_env, sync_state: str, option_value=None):
        """Stub tmux. `option_value=None` means @send_to_all_except_nvim is unset.

        The value is passed through a file rather than interpolated into the
        stub's source: the whole point of the payload is that it is full of
        shell metacharacters, and baking it into a generated `sh` script would
        re-introduce at test level exactly the quoting bug under test.
        """
        show_options = ""
        if option_value is not None:
            option_file = shell_env.stub_bin.parent / "tmux-option-value"
            option_file.write_text(option_value, encoding="utf-8")
            show_options = f'  show-options) cat "{option_file}" ;;\n'
        body = (
            'case "$1" in\n'
            f'  show-window-option) echo "{sync_state}" ;;\n'
            f"{show_options}"
            "  list-panes) printf '%%1 zsh\\n%%2 nvim\\n%%3 vim\\n' ;;\n"
            "esac"
        )
        shell_env.stub("tmux", body=body)

    def test_reads_the_command_from_the_tmux_option_when_given_no_arguments(
        self, shell_env
    ):
        # This is the path `bind S` uses. Nothing the user typed ever reaches a
        # shell command line, so quotes, `;`, `$` and backticks must arrive at
        # send-keys byte for byte.
        self._stub_tmux(shell_env, sync_state="off", option_value=self.NASTY)
        res = shell_env.run(TMUX_SCRIPT)
        assert res.returncode == 0, res.stderr
        send_calls = [c for c in shell_env.calls if "send-keys" in c]
        assert f"tmux send-keys -t %1 -l -- {self.NASTY}" in send_calls, (
            "the command stashed in @send_to_all_except_nvim did not reach the "
            f"panes intact: {send_calls}"
        )
        assert f"tmux send-keys -t %3 -l -- {self.NASTY}" in send_calls
        assert not any("-t %2" in c for c in send_calls), "nvim must be skipped"

    def test_the_option_is_cleared_once_it_has_been_read(self, shell_env):
        # The option is a one-shot mailbox from the prompt to the script. Left
        # set, a later argument-less run (or anything else calling run-shell)
        # would replay the previous command into every pane.
        self._stub_tmux(shell_env, sync_state="off", option_value="echo once")
        shell_env.run(TMUX_SCRIPT)
        calls = shell_env.calls
        unset = [
            i
            for i, c in enumerate(calls)
            if "@send_to_all_except_nvim" in c and ("-gu" in c or "-ug" in c)
        ]
        assert unset, f"@send_to_all_except_nvim is never unset: {calls}"
        send_idx = [i for i, c in enumerate(calls) if "send-keys" in c]
        assert min(unset) < min(send_idx), (
            "clear the mailbox before sending, so a failure mid-loop cannot "
            "leave a stale command armed for the next run"
        )

    def test_an_unset_option_sends_nothing_at_all(self, shell_env):
        # An empty prompt response, or a stray argument-less invocation, must
        # not fire a bare Enter at every pane -- nor toggle synchronize-panes.
        self._stub_tmux(shell_env, sync_state="on", option_value="")
        res = shell_env.run(TMUX_SCRIPT)
        assert res.returncode == 0, res.stderr
        assert not any("send-keys" in c for c in shell_env.calls), (
            f"an empty command must not be sent anywhere: {shell_env.calls}"
        )
        assert not any("synchronize-panes" in c for c in shell_env.calls), (
            "nothing was sent, so the sync state must not have been disturbed"
        )

    def test_sends_to_all_panes_except_nvim(self, shell_env):
        self._stub_tmux(shell_env, sync_state="off")
        res = shell_env.run(TMUX_SCRIPT, "echo", "hello")
        assert res.returncode == 0
        send_calls = [c for c in shell_env.calls if "send-keys" in c]
        # The text goes literally (-l) and behind `--`; Enter is a SEPARATE
        # send-keys so the text is actually executed, not just typed into the
        # pane's prompt -- under -l an Enter in the same call would be text.
        assert "tmux send-keys -t %1 -l -- echo hello" in send_calls
        assert "tmux send-keys -t %1 Enter" in send_calls
        assert "tmux send-keys -t %3 -l -- echo hello" in send_calls
        assert "tmux send-keys -t %3 Enter" in send_calls
        assert not any("-t %2" in c for c in send_calls)
        # ...and the Enter follows its text, per pane.
        assert send_calls.index("tmux send-keys -t %1 -l -- echo hello") < (
            send_calls.index("tmux send-keys -t %1 Enter")
        )

    @pytest.mark.parametrize("word", ["up", "tab", "enter", "-r"])
    def test_the_text_is_never_parsed_as_a_key_name_or_a_flag(self, shell_env, word):
        """`send-keys up` is the Up ARROW to tmux, not the word.

        Without -l a lone word is resolved as a key name, case-insensitively,
        so `up` re-ran the previous command in every non-nvim pane; a command
        starting with `-` was read as send-keys flags, failed, and the `|| true`
        swallowed it -- nothing sent, nothing reported.
        """
        self._stub_tmux(shell_env, sync_state="off")
        res = shell_env.run(TMUX_SCRIPT, word)
        assert res.returncode == 0, res.stderr
        send_calls = [c for c in shell_env.calls if "send-keys" in c]
        assert f"tmux send-keys -t %1 -l -- {word}" in send_calls, send_calls

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
            # `@latest`, not `npm update -g`: see the comment above the
            # `npm install -g ...@latest` calls in scripts/update_ai_tools.sh
            # for why.
            "npm install -g @openai/codex@latest",
            "npm install -g @google/gemini-cli@latest",
            "copilot update",
            "claude --version",
            "codex --version",
            "gemini --version",
            "copilot --version",
        ]
        for call in expected:
            assert call in shell_env.calls

    def test_one_tool_failing_does_not_abort_the_rest(self, shell_env):
        """run_if_installed's whole purpose is that one bad tool cannot stop the run.

        It only ever guarded against a tool being *absent*. An installed tool whose
        update exits nonzero propagated that status, and `set -euo pipefail` killed the
        script on the spot -- so a transient npm-registry blip while updating the first
        tool silently skipped every later update and the entire version report. That is
        both likelier and quieter than the missing-CLI case the guard was written for.
        """
        for tool in ("claude", "codex", "gemini", "copilot", "npm"):
            shell_env.stub(tool)
        shell_env.stub("claude", exit_code=1)

        res = shell_env.run(UPDATE_SCRIPT)

        assert res.returncode == 0, f"a failing tool aborted the script: {res.stderr}"
        for call in (
            "npm install -g @openai/codex@latest",
            "npm install -g @google/gemini-cli@latest",
            "copilot update",
            "codex --version",
            "gemini --version",
            "copilot --version",
        ):
            assert call in shell_env.calls, (
                f"{call!r} never ran after an earlier tool failed"
            )

    def test_skips_install_for_a_package_npm_does_not_manage(self, shell_env):
        """run_if_installed only ever checked that npm itself existed, so running the
        "update" script INSTALLED codex/gemini-cli for the first time on a machine
        that never had them, and added a duplicate npm copy on a machine that got
        them from Homebrew. "Installed" now means an npm-managed global
        (`npm ls -g --depth=0 <pkg>` succeeds); anything else is left untouched.
        """
        # Every CLI the script touches must be a stub: `codex`/`gemini` exist for
        # real on this host (e.g. installed for the Codex/Gemini MCP servers),
        # and the script's final version-report section runs `codex --version`
        # / `gemini --version` regardless of npm-managed status -- that check
        # is unrelated to the npm ls logic under test here.
        for tool in ("claude", "codex", "gemini", "copilot"):
            shell_env.stub(tool)
        # codex is npm-managed (`npm ls` succeeds); gemini-cli is not (came from
        # Homebrew, or was never installed at all) and must be left alone.
        shell_env.stub(
            "npm",
            body=(
                'case "$1" in\n'
                "  ls)\n"
                '    case "$*" in\n'
                "      *codex*) exit 0 ;;\n"
                "      *gemini*) exit 1 ;;\n"
                "    esac\n"
                "    ;;\n"
                "esac"
            ),
        )

        res = shell_env.run(UPDATE_SCRIPT)

        assert res.returncode == 0, res.stderr
        assert "npm install -g @openai/codex@latest" in shell_env.calls
        assert not any(
            "npm install -g @google/gemini-cli@latest" in c for c in shell_env.calls
        ), "gemini-cli was installed even though it is not npm-managed"
        assert "@google/gemini-cli" in res.stderr, (
            "no clear message explains why gemini-cli was skipped"
        )
        # Prove the stubs actually won the PATH lookup, not the real binaries.
        assert "codex --version" in shell_env.calls
        assert "gemini --version" in shell_env.calls

    def test_a_failed_npm_managed_install_does_not_abort_the_rest(self, shell_env):
        """The "one failure is reported, the rest continue" contract now lives in
        update_npm_managed (the ls-then-install check no longer goes through
        run_if_installed for codex/gemini-cli), so it needs its own coverage.
        """
        for tool in ("claude", "codex", "gemini", "copilot"):
            shell_env.stub(tool)
        shell_env.stub(
            "npm",
            body=(
                'case "$1" in\n'
                "  ls) exit 0 ;;\n"  # both packages are npm-managed
                '  install) case "$*" in *codex*) exit 1 ;; esac ;;\n'
                "esac"
            ),
        )

        res = shell_env.run(UPDATE_SCRIPT)

        assert res.returncode == 0, (
            f"a failed npm-managed install aborted the script: {res.stderr}"
        )
        assert "npm install -g @openai/codex@latest" in shell_env.calls
        assert "npm install -g @google/gemini-cli@latest" in shell_env.calls, (
            "gemini-cli's install never ran after codex's failed"
        )
        assert "copilot update" in shell_env.calls


class TestNewProject:
    """scripts/new_project.sh — 新規プロジェクトの雛形作成。

    設計上の要点が二つあり、テストもそこに寄せている:

    - **冪等**。既存プロジェクトで再実行しても、既にあるものには触らない。
      「上書きしない」は飾りではなく、このスクリプトを既存ディレクトリに
      向けて安全に叩けるかどうかそのもの。
    - **git は環境差が出る**。`git init -b` は 2.28 以降、作業ツリーの内側での
      入れ子 init はほぼ事故、$HOME での init は完全な事故。それぞれ分岐が
      あるので、それぞれに 1 本ずつ当てる。

    cwd を渡さない `shell_env.run` はリポジトリルートで走る。引数を取り違えた
    実装がこのツリーを汚さないよう、どのケースでも cwd は tmp_path に固定する。
    """

    def test_scaffolds_dirs_gitkeep_readme_and_repo(self, shell_env, tmp_path):
        target = tmp_path / "myproj"
        res = shell_env.run(NEW_PROJECT_SCRIPT, str(target), cwd=tmp_path)

        assert res.returncode == 0, res.stderr
        for name in ("docs", "assets"):
            assert (target / name).is_dir()
            assert (target / name / ".gitkeep").is_file()
        readme = (target / "README.md").read_text(encoding="utf-8")
        assert readme.startswith("# myproj\n")
        # 雛形の見出しは、作ったディレクトリと対応していないと意味がない
        assert "`docs/`" in readme and "`assets/`" in readme
        assert (target / ".git").is_dir()
        head = (target / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        assert head == "ref: refs/heads/main", (
            "既定ブランチは main。この .gitconfig に init.defaultBranch が無いので、"
            "素の `git init` に任せると master になり得る"
        )

    def test_defaults_to_the_current_directory(self, shell_env, tmp_path):
        work = tmp_path / "here"
        work.mkdir()
        res = shell_env.run(NEW_PROJECT_SCRIPT, cwd=work)

        assert res.returncode == 0, res.stderr
        assert (work / "docs" / ".gitkeep").is_file()
        assert (work / ".git").is_dir()

    def test_never_overwrites_existing_files(self, shell_env, tmp_path):
        target = tmp_path / "proj"
        (target / "docs").mkdir(parents=True)
        (target / "docs" / "note.md").write_text("keep me\n", encoding="utf-8")
        (target / "README.md").write_text("original\n", encoding="utf-8")

        res = shell_env.run(NEW_PROJECT_SCRIPT, str(target), cwd=tmp_path)

        assert res.returncode == 0, res.stderr
        assert (target / "README.md").read_text(encoding="utf-8") == "original\n"
        assert (target / "docs" / "note.md").read_text(encoding="utf-8") == "keep me\n"
        # 中身のあるディレクトリを git は既に追跡できる。.gitkeep はゴミになる
        assert not (target / "docs" / ".gitkeep").exists()
        assert (target / "assets" / ".gitkeep").is_file()

    def test_existing_repository_is_left_alone(self, shell_env, git_repo):
        res = shell_env.run(NEW_PROJECT_SCRIPT, str(git_repo), cwd=git_repo)

        assert res.returncode == 0, res.stderr
        assert (git_repo / "README.md").read_text(encoding="utf-8") == "init\n"
        assert "initial commit" in run_git(git_repo, "log", "--oneline")

    def test_skips_git_init_inside_an_existing_work_tree(self, shell_env, git_repo):
        # 既存リポジトリの中に入れ子のリポジトリを作るのは、まず事故。
        target = git_repo / "sub"
        res = shell_env.run(NEW_PROJECT_SCRIPT, str(target), cwd=git_repo)

        assert res.returncode == 0, res.stderr
        assert (target / "docs").is_dir()
        assert not (target / ".git").exists()
        assert "work tree" in res.stdout

    def test_falls_back_when_git_init_b_is_unsupported(self, shell_env, tmp_path):
        """`git init -b` は git 2.28 以降。それより古い環境でも main にする。

        本物の git に委譲しつつ `-b` だけを拒む薄いラッパを PATH の先頭に置いて、
        古い git を再現する。
        """
        real_git = shutil.which("git")
        assert real_git, "git が無い環境ではこのテストは書けない"
        shell_env.stub(
            "git",
            body=(
                'for a in "$@"; do\n'
                '  if [ "$a" = "-b" ]; then\n'
                '    echo "error: unknown switch \\`b\'" >&2\n'
                "    exit 129\n"
                "  fi\n"
                "done\n"
                f'exec {real_git} "$@"'
            ),
        )

        target = tmp_path / "oldgit"
        res = shell_env.run(NEW_PROJECT_SCRIPT, str(target), cwd=tmp_path)

        assert res.returncode == 0, res.stderr
        head = (target / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        assert head == "ref: refs/heads/main"

    def test_missing_git_is_a_warning_not_a_failure(self, shell_env, tmp_path):
        shell_env.hide("git")
        target = tmp_path / "proj"

        res = shell_env.run(NEW_PROJECT_SCRIPT, str(target), cwd=tmp_path)

        assert res.returncode == 0, res.stderr
        assert (target / "docs" / ".gitkeep").is_file()
        assert not (target / ".git").exists()
        assert "git" in res.stderr

    def test_refuses_to_initialize_a_repository_in_home(self, shell_env):
        # `np` を引数無しでうっかり $HOME で叩いたときに、ホーム全体が
        # リポジトリになるのだけは避ける。
        res = shell_env.run(NEW_PROJECT_SCRIPT, cwd=shell_env.home)

        assert res.returncode == 0, res.stderr
        assert not (shell_env.home / ".git").exists()
        assert (shell_env.home / "docs").is_dir()
        assert "HOME" in res.stderr

    def test_refuses_home_reached_through_a_case_differing_spelling(
        self, shell_env, tmp_path
    ):
        """A case-insensitive filesystem lets `$HOME` be reached under an alias.

        The guard used to compare `pwd -P` strings. bash's `pwd -P` resolves
        symlinks but never canonicalises case, so on a case-insensitive
        filesystem (APFS by default) `HOME` and `home` are two spellings of
        one directory and the string compare missed it -- `git init` ran in
        $HOME. Fixed the same way install.sh's checkout-alias guards were
        (d68b0f1): compare identity (`-ef`), not spelling. `alias.exists()`
        is itself the case-insensitivity probe: it is a distinct path from
        `shell_env.home` that resolves to the same directory only when the
        filesystem folds case, so the test is meaningless (and self-skips)
        anywhere else, e.g. Linux CI.
        """
        alias = tmp_path / "HOME"
        if not alias.exists():
            pytest.skip("filesystem is case-sensitive; HOME cannot be aliased by case")

        res = shell_env.run(NEW_PROJECT_SCRIPT, str(alias), cwd=tmp_path)

        assert res.returncode == 0, res.stderr
        assert not (shell_env.home / ".git").exists()
        assert "refusing to run git init" in res.stderr

    def test_refuses_home_reached_through_a_symlink_alias(self, shell_env, tmp_path):
        """A symlink pointing AT $HOME must resolve to the same guard, portably.

        Unlike the case-differing spelling above, bash's `pwd -P` already
        dereferences a real symlink correctly, so this passed even before the
        `-ef` fix -- it is a portable (works on case-sensitive filesystems,
        e.g. Linux CI too) regression guard for the new identity-based
        comparison, not a reproduction of the original bug.
        """
        alias = tmp_path / "home-link"
        alias.symlink_to(shell_env.home)

        res = shell_env.run(NEW_PROJECT_SCRIPT, str(alias), cwd=tmp_path)

        assert res.returncode == 0, res.stderr
        assert not (shell_env.home / ".git").exists()
        assert "refusing to run git init" in res.stderr

    def test_non_directory_in_the_way_is_skipped_not_clobbered(
        self, shell_env, tmp_path
    ):
        target = tmp_path / "proj"
        target.mkdir()
        (target / "assets").write_text("i am a file\n", encoding="utf-8")

        res = shell_env.run(NEW_PROJECT_SCRIPT, str(target), cwd=tmp_path)

        assert res.returncode == 0, res.stderr
        assert (target / "assets").read_text(encoding="utf-8") == "i am a file\n"
        assert (target / "docs").is_dir(), "1 つの衝突で残りの雛形まで止めない"
        assert res.stderr.strip() != ""

    def test_does_not_write_through_a_dangling_symlink(self, shell_env, tmp_path):
        # `-e` はリンク先を辿るので、リンク切れのシンボリックリンクは「無い」と
        # 判定される。そのまま書くとプロジェクトの外へ書き抜ける。
        target = tmp_path / "proj"
        target.mkdir()
        outside = tmp_path / "outside.md"
        (target / "README.md").symlink_to(outside)

        res = shell_env.run(NEW_PROJECT_SCRIPT, str(target), cwd=tmp_path)

        assert res.returncode == 0, res.stderr
        assert not outside.exists(), "リンクの先へ書き抜けてはいけない"

    def test_dangling_symlink_in_place_of_a_directory_is_skipped(
        self, shell_env, tmp_path
    ):
        target = tmp_path / "proj"
        target.mkdir()
        outside = tmp_path / "outside-dir"
        (target / "docs").symlink_to(outside)

        res = shell_env.run(NEW_PROJECT_SCRIPT, str(target), cwd=tmp_path)

        assert res.returncode == 0, res.stderr
        assert not outside.exists()
        assert (target / "assets" / ".gitkeep").is_file(), (
            "1 つのシンボリックリンクで残りの雛形まで止めない"
        )

    def test_handles_a_target_whose_name_starts_with_a_dash(self, shell_env, tmp_path):
        # `--` はこのスクリプト自身が用意したオプション終端。その先のパスを
        # dirname / cd / mkdir に渡すときも `--` で守らないと意味がない。
        work = tmp_path / "work"
        work.mkdir()

        res = shell_env.run(NEW_PROJECT_SCRIPT, "--", "-dashed", cwd=work)
        assert res.returncode == 0, res.stderr
        assert (work / "-dashed" / "docs" / ".gitkeep").is_file()

        # 既存になったあとの再実行 (冪等パス) も同じく壊れないこと
        again = shell_env.run(NEW_PROJECT_SCRIPT, "--", "-dashed", cwd=work)
        assert again.returncode == 0, again.stderr

    def test_accepts_dotdot_that_resolves_through_existing_directories(
        self, shell_env, tmp_path
    ):
        inner = tmp_path / "work" / "inner"
        inner.mkdir(parents=True)

        res = shell_env.run(NEW_PROJECT_SCRIPT, "../sibling", cwd=inner)

        assert res.returncode == 0, res.stderr
        assert (tmp_path / "work" / "sibling" / "docs").is_dir()

    def test_rejects_dotdot_below_a_directory_that_does_not_exist_yet(
        self, shell_env, tmp_path
    ):
        # `subdir/newdir/..` の `..` が何を指すかは、newdir を作るまで決まらない。
        # 黙って subdir を雛形化する (しかもゴミの newdir を残す) より、断る。
        work = tmp_path / "work"
        (work / "subdir").mkdir(parents=True)

        res = shell_env.run(NEW_PROJECT_SCRIPT, "subdir/newdir/..", cwd=work)

        assert res.returncode != 0
        assert res.stderr.strip() != ""
        assert not (work / "subdir" / "newdir").exists()
        assert not (work / "subdir" / "README.md").exists()

    def test_hidden_files_alone_still_count_as_content(self, shell_env, tmp_path):
        target = tmp_path / "proj"
        (target / "docs").mkdir(parents=True)
        (target / "docs" / ".keep-me").write_text("x\n", encoding="utf-8")

        res = shell_env.run(NEW_PROJECT_SCRIPT, str(target), cwd=tmp_path)

        assert res.returncode == 0, res.stderr
        assert not (target / "docs" / ".gitkeep").exists()

    @pytest.mark.parametrize(
        "name",
        [".hidden", ".a/b", "./.hidden", "hidden", "a/.b"],
    )
    def test_leading_dot_in_a_new_name_is_not_mistaken_for_the_cwd_marker(
        self, shell_env, tmp_path, name
    ):
        # dirname returns "." (the current directory) once it walks past the
        # first element that does not exist yet. For an unwritten dotted name
        # like ".hidden", that "." is indistinguishable from the leading dot
        # of the name itself, so a naive string-strip eats the dot along with
        # it (".hidden" -> "hidden"). Check that the absolute path np() gets
        # back via dir_file actually exists and matches the directory that
        # was really created -- if this breaks, np() cd's into a directory
        # that was never made.
        work = tmp_path / f"work-{name.replace('/', '_')}"
        work.mkdir()
        dir_file = tmp_path / f"dir-{name.replace('/', '_')}.txt"
        shell_env.env["NEW_PROJECT_DIR_FILE"] = str(dir_file)

        res = shell_env.run(NEW_PROJECT_SCRIPT, name, cwd=work)

        assert res.returncode == 0, res.stderr
        target_dir = work / name
        assert target_dir.is_dir(), f"'{name}' was not created where expected"
        reported = dir_file.read_text(encoding="utf-8").strip()
        assert os.path.isdir(reported), (
            f"np() would cd into a nonexistent directory: {reported!r} "
            f"(stdout: {res.stdout!r})"
        )
        assert os.path.realpath(reported) == os.path.realpath(target_dir)
        assert reported in res.stdout

    def test_leading_dot_in_an_already_existing_name_still_works(
        self, shell_env, tmp_path
    ):
        # `./.hidden` already worked before the fix (per the bug report); this
        # pins that an already-existing dotted directory works too.
        target = tmp_path / "proj" / ".hidden"
        target.mkdir(parents=True)

        res = shell_env.run(NEW_PROJECT_SCRIPT, ".hidden", cwd=tmp_path / "proj")

        assert res.returncode == 0, res.stderr
        assert f"project: {target}\n" == res.stdout.splitlines(keepends=True)[0], (
            res.stdout
        )

    def test_dry_run_touches_nothing(self, shell_env, tmp_path):
        target = tmp_path / "planned"
        res = shell_env.run(NEW_PROJECT_SCRIPT, "--dry-run", str(target), cwd=tmp_path)

        assert res.returncode == 0, res.stderr
        assert not target.exists()
        assert "dry-run" in res.stdout
        for planned in ("docs/", "assets/", "README.md"):
            assert planned in res.stdout

    def test_reports_the_project_dir_for_the_shell_wrapper(self, shell_env, tmp_path):
        # np() が cd するために必要な一本道。スクリプトは子プロセスなので、
        # 作成先はこのファイル経由でしか親シェルに戻せない。
        target = tmp_path / "proj"
        dir_file = tmp_path / "dir.txt"
        shell_env.env["NEW_PROJECT_DIR_FILE"] = str(dir_file)

        res = shell_env.run(NEW_PROJECT_SCRIPT, str(target), cwd=tmp_path)

        assert res.returncode == 0, res.stderr
        reported = dir_file.read_text(encoding="utf-8").strip()
        assert os.path.realpath(reported) == os.path.realpath(target)

    def test_dir_file_is_not_written_in_dry_run(self, shell_env, tmp_path):
        dir_file = tmp_path / "dir.txt"
        shell_env.env["NEW_PROJECT_DIR_FILE"] = str(dir_file)

        res = shell_env.run(
            NEW_PROJECT_SCRIPT, "-n", str(tmp_path / "proj"), cwd=tmp_path
        )

        assert res.returncode == 0, res.stderr
        assert not dir_file.exists(), "ドライランで cd してしまっては意味がない"

    def test_help_exits_zero(self, shell_env, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        res = shell_env.run(NEW_PROJECT_SCRIPT, "--help", cwd=work)
        assert res.returncode == 0
        assert "Usage:" in res.stdout
        assert list(work.iterdir()) == []

    @pytest.mark.parametrize(
        "args", [("--nope",), ("a", "b")], ids=["unknown-option", "too-many-args"]
    )
    def test_usage_errors_exit_2_without_scaffolding(self, shell_env, tmp_path, args):
        work = tmp_path / "work"
        work.mkdir()
        res = shell_env.run(NEW_PROJECT_SCRIPT, *args, cwd=work)
        assert res.returncode == 2
        assert "Usage:" in res.stderr
        # 使い方を間違えたときに、その辺りへ雛形を撒き散らしてはいけない
        assert list(work.iterdir()) == []


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


class TestZshSecretsGuard:
    """.zshrc's trailing ~/.zsh_secrets block.

    It is the last thing .zshrc runs, so its exit status becomes .zshrc's own.
    ~/.zsh_secrets is absent on any machine that has this .zshrc but has not
    run install.sh's seeding yet, and a bare `[ -f ... ] && source ...` leaves
    that status at 1 -- a fresh shell reporting failure for nothing.
    """

    def _run(self, home, trailer: str = ""):
        if shutil.which("zsh") is None:
            pytest.skip("zsh not installed")
        env = {**os.environ, "HOME": str(home)}
        return subprocess.run(
            ["zsh", "-c", f"{extract_zsh_secrets_guard()}\n{trailer}"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def test_exits_zero_when_the_file_is_absent(self, tmp_path):
        assert not (tmp_path / ".zsh_secrets").exists()
        res = self._run(tmp_path)
        assert res.returncode == 0, (
            "the guard is .zshrc's last line, so a non-zero status here makes a "
            f"fresh shell report failure: {res.stderr}"
        )

    def test_sources_the_file_when_present(self, tmp_path):
        (tmp_path / ".zsh_secrets").write_text(
            "export SEEDED_BY_TEST=yes\n", encoding="utf-8"
        )
        res = self._run(tmp_path, trailer='printf "%s" "$SEEDED_BY_TEST"')
        assert res.returncode == 0, res.stderr
        assert res.stdout == "yes", (
            "guarding the exit status must not stop the file from being sourced"
        )


class TestUimFepAutostart:
    """.zshrc's uim-fep block replaces the shell with a terminal IME.

    It runs `exec`, so every guard on it is load-bearing in a way an ordinary
    conditional is not: a shell that reaches the exec by mistake is gone, and
    whatever was driving it gets uim-fep's raw terminal output instead of the
    command it asked for. The tty test is the one that keeps this away from
    every non-interactive caller -- editors, hooks, `zsh -ic` from a tool --
    since those are interactive by flag but have no terminal behind them.
    """

    def _stub_dir(self, tmp_path, present: bool):
        """A uim-fep that reports being reached instead of seizing the tty."""
        bin_dir = tmp_path / "bin"
        if present:
            stub_bin(bin_dir, "uim-fep", 'echo "EXECED args=[$*]"')
        else:
            bin_dir.mkdir(parents=True, exist_ok=True)
        return bin_dir

    def _run(self, tmp_path, env_extra=None, *, on_a_tty=False, installed=True):
        zsh = shutil.which("zsh")
        if zsh is None:
            pytest.skip("zsh not installed")
        script = f"{extract_uim_fep_block()}\nprint REACHED-END"
        # An empty ZDOTDIR makes `zsh -i` launch zsh-newuser-install, which
        # blocks on a prompt; an existing (empty) .zshrc is what suppresses it.
        (tmp_path / ".zshrc").write_text("", encoding="utf-8")
        env = {
            **os.environ,
            # ZDOTDIR keeps `zsh -i` off the real ~/.zshrc, which would source
            # oh-my-zsh and re-run the very block under test.
            "ZDOTDIR": str(tmp_path),
            "HOME": str(tmp_path),
            # Absence has to be simulated by a PATH holding nothing else: this
            # machine really does have /usr/bin/uim-fep, and the block would
            # find it. The block itself needs no external command, and zsh is
            # invoked by absolute path, so a lone empty dir is enough.
            "PATH": (
                f"{self._stub_dir(tmp_path, installed)}:{os.environ['PATH']}"
                if installed
                else str(self._stub_dir(tmp_path, installed))
            ),
            "TERM": "xterm-256color",
        }
        env.pop("UIM_FEP_PID", None)
        env.pop("NO_UIM_FEP", None)
        env.update(env_extra or {})

        if not on_a_tty:
            return subprocess.run(
                [zsh, "-ic", script],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            ).stdout

        # The positive case needs a controlling terminal, which a pipe is not.
        pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover - replaced by execvpe
            os.execve(zsh, [zsh, "-ic", script], env)
        out = b""
        deadline = time.time() + 30
        while time.time() < deadline:
            if not select.select([fd], [], [], 5)[0]:
                break
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if b"EXECED" in out or b"REACHED-END" in out:
                break
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        os.close(fd)
        return out.decode("utf-8", "replace")

    def test_runs_uim_fep_on_a_real_terminal(self, tmp_path):
        out = self._run(tmp_path, on_a_tty=True)
        assert "EXECED" in out, (
            f"the block never reached uim-fep on a tty, so the IME is dead: {out!r}"
        )

    def test_passes_zsh_explicitly(self, tmp_path):
        out = self._run(tmp_path, on_a_tty=True)
        assert "args=[-e /usr/bin/zsh]" in out, (
            "uim-fep defaults its child to $SHELL, which is bash here -- dropping "
            f"-e lands the user in bash inside the FEP: {out!r}"
        )

    def test_does_nothing_without_a_terminal(self, tmp_path):
        assert "REACHED-END" in self._run(tmp_path), (
            "a shell driven through a pipe must not be replaced by uim-fep"
        )

    def test_does_not_re_enter_itself(self, tmp_path):
        out = self._run(tmp_path, {"UIM_FEP_PID": "9999"}, on_a_tty=True)
        assert "REACHED-END" in out and "EXECED" not in out, (
            "uim-fep exports UIM_FEP_PID into the shell it starts; ignoring it "
            f"makes each shell start another uim-fep forever: {out!r}"
        )

    def test_honours_the_opt_out(self, tmp_path):
        out = self._run(tmp_path, {"NO_UIM_FEP": "1"}, on_a_tty=True)
        assert "REACHED-END" in out and "EXECED" not in out, (
            f"NO_UIM_FEP is the way out that does not edit a tracked file: {out!r}"
        )

    def test_skips_dumb_terminals(self, tmp_path):
        out = self._run(tmp_path, {"TERM": "dumb"}, on_a_tty=True)
        assert "REACHED-END" in out and "EXECED" not in out, (
            f"a dumb terminal cannot render the FEP's status line: {out!r}"
        )

    def test_does_nothing_where_uim_fep_is_absent(self, tmp_path):
        # Nothing on PATH: the machines that share this .zshrc without uim-fep.
        out = self._run(tmp_path, on_a_tty=True, installed=False)
        assert "REACHED-END" in out and "EXECED" not in out, (
            f"the block must be inert where uim-fep is not installed: {out!r}"
        )


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

        func_src = extract_zsh_functions("__dotfiles_script", "update_ai_tools")
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

        func_src = extract_zsh_functions("__dotfiles_script", "update_ai_tools")
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
class TestNpFunction:
    """.zshrc の np() — scripts/new_project.sh に委譲し、成功時だけ cd する。

    スクリプトは子プロセスなので、親シェルの cwd を変えられない。np() が
    zsh 関数として存在する理由はその一点だけなので、テストも「cd したか」に
    絞る。雛形作成そのものの検証は TestNewProject の担当で、ここでは
    new_project.sh を偽物に差し替えて連携部分だけを見る。
    """

    def _fake_checkout(self, tmp_path, body: str):
        """~/.zshrc がチェックアウトを指す HOME を作り、偽スクリプトを置く。"""
        checkout = tmp_path / "checkout"
        (checkout / "scripts").mkdir(parents=True)
        (checkout / ".zshrc").write_text("# stub\n", encoding="utf-8")
        script = checkout / "scripts" / "new_project.sh"
        script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        script.chmod(0o755)

        home = tmp_path / "home"
        home.mkdir()
        (home / ".zshrc").symlink_to(checkout / ".zshrc")
        return home

    def _run_np(self, home, cwd, *args):
        """np を呼び、終了ステータスと最終的な cwd の両方を返す。"""
        src = extract_zsh_functions("__dotfiles_script", "np")
        quoted = " ".join(f"'{a}'" for a in args)
        program = f'{src}\nnp {quoted}\nst=$?\nprint -r -- "pwd=$PWD"\nexit $st\n'
        return subprocess.run(
            ["zsh", "-c", program],
            capture_output=True,
            text=True,
            cwd=cwd,
            env={**os.environ, "HOME": str(home)},
            timeout=30,
        )

    def test_cds_into_the_created_project(self, tmp_path):
        home = self._fake_checkout(
            tmp_path,
            'mkdir -p "$1"\nprintf \'%s\\n\' "$1" >"$NEW_PROJECT_DIR_FILE"',
        )
        target = tmp_path / "proj"

        res = self._run_np(home, tmp_path, str(target))

        assert res.returncode == 0, res.stderr
        assert f"pwd={target}" in res.stdout

    def test_stays_put_when_the_script_reports_no_directory(self, tmp_path):
        # --dry-run / --help のときスクリプトは何も書かない。cd する先が無い。
        home = self._fake_checkout(tmp_path, "exit 0")
        start = tmp_path / "start"
        start.mkdir()

        res = self._run_np(home, start, "--dry-run", str(tmp_path / "proj"))

        assert res.returncode == 0, res.stderr
        assert f"pwd={start}" in res.stdout

    def test_propagates_failure_and_does_not_cd(self, tmp_path):
        home = self._fake_checkout(
            tmp_path,
            'mkdir -p "$1"\nprintf \'%s\\n\' "$1" >"$NEW_PROJECT_DIR_FILE"\nexit 3',
        )
        start = tmp_path / "start"
        start.mkdir()

        res = self._run_np(home, start, str(tmp_path / "proj"))

        assert res.returncode == 3
        assert f"pwd={start}" in res.stdout, (
            "スクリプトが失敗したなら、作りかけの場所へ移動してはいけない"
        )

    def test_reports_a_missing_checkout(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        (home / ".zshrc").write_text("# not a symlink\n", encoding="utf-8")

        res = self._run_np(home, tmp_path)

        assert res.returncode != 0
        assert res.stderr.strip() != ""

    def test_leaves_no_temp_file_behind(self, tmp_path):
        # 受け渡し用の一時ファイルは np の実装詳細。呼ぶたびに TMPDIR へ
        # 溜まっていくようでは困る。
        home = self._fake_checkout(
            tmp_path,
            'mkdir -p "$1"\nprintf \'%s\\n\' "$1" >"$NEW_PROJECT_DIR_FILE"',
        )
        tmpdir = tmp_path / "tmp"
        tmpdir.mkdir()

        res = subprocess.run(
            [
                "zsh",
                "-c",
                f"{extract_zsh_functions('__dotfiles_script', 'np')}\n"
                f"np '{tmp_path / 'proj'}'",
            ],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "HOME": str(home), "TMPDIR": str(tmpdir)},
            timeout=30,
        )

        assert res.returncode == 0, res.stderr
        assert list(tmpdir.iterdir()) == []

    def test_cleans_up_the_temp_file_when_interrupted(self, tmp_path):
        # Ctrl-C は「雛形作成が長引いたとき」に実際に押される操作。関数の末尾に
        # rm を置くだけでは中断時に到達せず、TMPDIR に溜まっていく。
        home = self._fake_checkout(tmp_path, "sleep 30")
        tmpdir = tmp_path / "tmp"
        tmpdir.mkdir()

        proc = subprocess.Popen(
            [
                "zsh",
                "-c",
                f"{extract_zsh_functions('__dotfiles_script', 'np')}\n"
                f"np '{tmp_path / 'proj'}'",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "HOME": str(home), "TMPDIR": str(tmpdir)},
            # プロセスグループごと INT を送るため。zsh だけに送っても、実際の
            # Ctrl-C と違って子プロセスが生き残る。
            start_new_session=True,
        )
        try:
            # 固定 sleep ではなく、一時ファイルが実際に現れるまで待つ。作られる
            # 前に中断したのでは、後始末の検証にならない。
            deadline = time.monotonic() + 10
            while not list(tmpdir.iterdir()) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert list(tmpdir.iterdir()), "一時ファイルが作られる前に中断している"
            os.killpg(proc.pid, signal.SIGINT)
            proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

        assert list(tmpdir.iterdir()) == []


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


@requires_zsh
class TestMcCli:
    """cli) は `"$@"` で全引数を素通しし、translate)/execute) は `"$*"`
    のまま (意図的) という非対称を固定する。理由の全文は .zshrc の mc() 内
    cli) 直前のコメント (`# ここだけ "$@" なのは意図的...`) を正とする。
    """

    def _run(self, tmp_path, call):
        bin_dir = tmp_path / "bin"
        record = tmp_path / "claude-argv"
        stub_bin(
            bin_dir,
            "claude",
            '{ printf "argc=%s\\n" "$#"; for a; do printf "argv=[%s]\\n" "$a"; done; }'
            f' >"{record}"',
        )
        # HOME must be redirected, and it is not cosmetic. `zsh -c` still reads
        # ~/.zshenv, and a developer machine typically has one that does
        # `export PATH="$HOME/.local/bin:$PATH"` -- which lands AHEAD of the
        # stub dir path_env() prepended. Without this the real `claude` CLI
        # wins the lookup and every assertion below turns into a live API call
        # that writes nothing to `record`.
        #
        # That ~/.zshenv is NOT ours: uv's installer writes it. This comment
        # used to call it "this repo's own", which is what hid the fact that
        # nothing tracked here put ~/.local/bin on PATH at all -- install.sh
        # fills that directory (pip --user linters, bat/fd, uv) and its own
        # export dies with the script. .zshrc now adds it; see
        # TestZshrcPath::test_local_bin_is_on_the_path.
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        env = {**path_env(bin_dir), "HOME": str(home)}

        # Backstop: resolve `claude` in the very shell the test is about to use
        # and refuse to run mc() at all unless it is the stub. Reaching the
        # real CLI is not a test failure this file can tolerate quietly.
        probe = subprocess.run(
            ["zsh", "-c", "command -v claude"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert probe.stdout.strip() == str(bin_dir / "claude"), (
            "the claude stub lost the PATH lookup; mc() would have invoked the "
            f"real CLI: {probe.stdout.strip()!r}"
        )

        res = subprocess.run(
            ["zsh", "-c", f"{extract_zsh_function('mc')}\n{call}"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=env,
            timeout=30,
        )
        assert res.returncode == 0, res.stderr
        assert record.exists(), f"claude was never invoked: {res.stderr}"
        lines = record.read_text(encoding="utf-8").splitlines()
        argc = int(lines[0].removeprefix("argc="))
        argv = [line.removeprefix("argv=[").removesuffix("]") for line in lines[1:]]
        return argc, argv

    def test_cli_hands_every_word_to_claude_as_its_own_argument(self, tmp_path):
        argc, argv = self._run(tmp_path, "mc cli mcp list")

        assert (argc, argv) == (2, ["mcp", "list"]), (
            "cli) is documented as a pass-through, but the words arrived joined "
            "into one argument, which the claude CLI reads as a prompt string "
            f"instead of the `mcp list` subcommand: argc={argc} argv={argv}"
        )

    def test_cli_with_no_arguments_passes_no_arguments(self, tmp_path):
        argc, argv = self._run(tmp_path, "mc cli")

        assert (argc, argv) == (0, []), (
            "a bare `mc cli` must start claude interactively; passing one empty "
            f"string instead makes it a prompt: argc={argc} argv={argv}"
        )

    def test_cli_keeps_a_quoted_argument_in_one_piece(self, tmp_path):
        # The caller's own quoting has to survive too: "$*" flattened this to
        # the single argument `-p do the thing`.
        argc, argv = self._run(tmp_path, "mc cli -p 'do the thing'")

        assert (argc, argv) == (2, ["-p", "do the thing"])

    def test_translate_still_collapses_its_words_into_one_prompt(self, tmp_path):
        # NOT a bug: translate) interpolates the words into a Japanese prompt
        # sentence, so joining them is the whole point. Pinned so nobody
        # "fixes" it to "$@" for symmetry with cli).
        argc, argv = self._run(tmp_path, "mc translate hello world")

        assert argc == 4, f"translate) must still pass one -p prompt: {argv}"
        assert argv[:3] == ["--model", "haiku", "-p"]
        assert argv[3].endswith("hello world")

    def test_execute_still_collapses_its_words_into_one_prompt(self, tmp_path):
        argc, argv = self._run(tmp_path, "mc execute do the thing")

        assert (argc, argv) == (4, ["--model", "sonnet", "-p", "do the thing"])


class TestPromptThemeStatusSegment:
    """The theme's status segment must be able to see background jobs.

    `$(jobs -l | wc -l)` runs in a forked subshell whose job table is empty,
    and the segment is itself already inside `$(prompt_agnoster_main)`, so the
    GEAR the comment promises ("are there background jobs?") could never
    appear. The count has to be taken in precmd, in the main shell.
    """

    THEME = REPO_ROOT / ".oh-my-zsh/custom/themes/px-rose-pine.zsh-theme"

    def _render_prompt_with_a_background_job(self):
        program = (
            f'source "{self.THEME}"\n'
            "setopt promptsubst\n"
            "sleep 5 &\n"
            "prompt_agnoster_precmd\n"
            'print -P -- "$PROMPT"\n'
            "kill %1 2>/dev/null; wait 2>/dev/null\n"
        )
        res = subprocess.run(
            ["zsh", "-f", "-c", program],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(self.THEME.parent),
        )
        assert res.returncode == 0, res.stderr
        return res.stdout

    def test_a_background_job_lights_the_gear(self):
        if shutil.which("zsh") is None:
            pytest.skip("zsh not installed")
        out = self._render_prompt_with_a_background_job()
        assert "\u2699" in out or "\\u2699" in out, (
            f"no background-job indicator in the rendered prompt: {out!r}"
        )


# --------------------------------------------------------------------------
# scripts/pbcopy -- クリップボードの出口選び
# --------------------------------------------------------------------------

PBCOPY = REPO_ROOT / "scripts/pbcopy"
CLIP_TO_ANDROID = REPO_ROOT / "scripts/clip_to_android.sh"


def make_wayland_socket(directory):
    """`[ -S ... ]` が真になる本物の AF_UNIX ソケットノードを作る。

    通常ファイルや FIFO では判定が変わってしまうので、ここだけは実際に
    bind(2) する。ノードは bind した時点で出来るので、返した socket を
    閉じてもパスは残る (tmp_path ごと消える)。
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "wayland-0"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    return sock, path


class TestPbcopyBackendSelection:
    """`scripts/pbcopy` は「その環境で本当に届く出口」を上から順に選ぶ。

    Android の Linux ターミナル (AVF) では、ゲストエージェント
    `/usr/bin/linux_vm_manager` が readClipboard/updateClipboard で
    `XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0` の
    `wl-paste` / `wl-copy` を呼ぶ。つまり **Wayland のクリップボードだけ**が
    Android 本体と繋がっている。xsel が書く X のクリップボードは Xwayland
    経由でたまたま同期されるだけ、OSC 52 は ttyd の WebView が 52 番の OSC
    ハンドラを一切登録していないので届かない。順序はこの事実に従う。
    """

    def _stubs(self, tmp_path):
        bin_dir = tmp_path / "bin"
        log = tmp_path / "log"
        stub_bin(
            bin_dir,
            "wl-copy",
            'printf "backend=wl-copy WAYLAND_DISPLAY=%s\\n" "${WAYLAND_DISPLAY:-}"'
            f' > "{log}"\ncat >> "{log}"',
        )
        # xsel は選択ごとに別の器を持つ本物に寄せる。pbcopy は CLIPBOARD を
        # 直接書いてから PRIMARY へ写すので、-ob が何も返さないスタブだと
        # PRIMARY の検証が素通りしてしまう。
        primary = tmp_path / "primary"
        stub_bin(
            bin_dir,
            "xsel",
            'case "$*" in\n'
            f'  *-ib*) {{ echo "backend=xsel"; cat; }} > "{log}" ;;\n'
            f'  *-ob*) tail -n +2 "{log}" ;;\n'
            f'  *-ip*) cat > "{primary}" ;;\n'
            f'  *-op*) cat "{primary}" ;;\n'
            "esac",
        )
        return bin_dir, log

    def _run(self, tmp_path, env_extra, text="rect-copied"):
        bin_dir, log = self._stubs(tmp_path)
        runtime = tmp_path / "run"
        runtime.mkdir(exist_ok=True)
        env = {
            **path_env(bin_dir),
            "HOME": str(tmp_path),
            "XDG_RUNTIME_DIR": str(runtime),
        }
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "TMUX"):
            env.pop(key, None)
        env.update(env_extra)
        res = subprocess.run(
            [str(PBCOPY)],
            input=text,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        return res, (log.read_text(encoding="utf-8") if log.exists() else "")

    def test_wayland_wins_even_when_display_is_also_set(self, tmp_path):
        # ttyd が起こすシェルには DISPLAY=:0 が必ず入っている。X を先に見る実装
        # だと、Android へ届く唯一の出口を素通りして VM 内クリップボードで
        # 満足してしまう。
        sock, _ = make_wayland_socket(tmp_path / "run")
        try:
            res, log = self._run(tmp_path, {"DISPLAY": ":0"})
        finally:
            sock.close()

        assert res.returncode == 0, res.stderr
        assert "backend=wl-copy" in log, f"Wayland を選ばなかった: {log!r}"
        assert log.endswith("rect-copied")

    def test_wayland_display_is_defaulted_from_the_socket(self, tmp_path):
        # ttyd 経由のシェルには WAYLAND_DISPLAY が無い (DISPLAY だけ入る)。
        # 補わないと wl-copy が繋ぎ先を知らないまま落ちる。
        sock, _ = make_wayland_socket(tmp_path / "run")
        try:
            res, log = self._run(tmp_path, {"DISPLAY": ":0"})
        finally:
            sock.close()

        assert res.returncode == 0, res.stderr
        assert "WAYLAND_DISPLAY=wayland-0" in log, (
            f"wl-copy に WAYLAND_DISPLAY が渡っていない: {log!r}"
        )

    def test_falls_back_to_x11_without_a_wayland_socket(self, tmp_path):
        res, log = self._run(tmp_path, {"DISPLAY": ":0"})

        assert res.returncode == 0, res.stderr
        assert "backend=xsel" in log, f"X11 に落ちなかった: {log!r}"
        assert log.endswith("rect-copied")

    def test_x11_still_sets_primary_as_well_as_clipboard(self, tmp_path):
        # 旧 `.tmux.conf` の bind は `xsel -ip && xsel -op | xsel -ib` で
        # PRIMARY も立てていた。bind を pbcopy に寄せた時にここが落ちると、
        # X デスクトップでの中クリック貼り付けが黙って消える。
        bin_dir, log = self._stubs(tmp_path)
        runtime = tmp_path / "run"
        runtime.mkdir(exist_ok=True)
        env = {
            **path_env(bin_dir),
            "HOME": str(tmp_path),
            "XDG_RUNTIME_DIR": str(runtime),
            "DISPLAY": ":0",
        }
        env.pop("WAYLAND_DISPLAY", None)
        env.pop("TMUX", None)
        subprocess.run(
            [str(PBCOPY)], input="primary too", text=True, env=env, timeout=30
        )

        assert (tmp_path / "primary").read_text(encoding="utf-8") == "primary too"


class TestPbcopyOsc52Fallback:
    """X も Wayland も無い環境 (素の SSH 先) 向けの最後の出口。

    tmux の中では DCS パススルーで包む必要があり、その中の ESC は **二重に**
    しなければならない。旧実装は `\\033Ptmux;\\003\\033]52;...` と ETX を
    挟んでいて、tmux はこの DCS を外側の端末へ渡さず捨てていた -- 終了
    ステータスは 0 のままなので、tmux 内の pbcopy はずっと無言で死んでいた。
    """

    ENCODED = "aGk="  # base64 of "hi"

    def _env(self, tmp_path, extra):
        runtime = tmp_path / "run"
        runtime.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "HOME": str(tmp_path),
            "XDG_RUNTIME_DIR": str(runtime),
        }
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "TMUX"):
            env.pop(key, None)
        env.update(extra)
        return env

    def test_inside_a_pane_the_passthrough_doubles_the_escape(self, tmp_path):
        env = self._env(tmp_path, {"TMUX": f"{tmp_path}/tmux-socket,1,0"})
        pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover - replaced by execve
            os.execve("/bin/sh", ["/bin/sh", "-c", f'printf hi | "{PBCOPY}"'], env)
        out = b""
        deadline = time.time() + 20
        while time.time() < deadline:
            if not select.select([fd], [], [], 5)[0]:
                break
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if b"\x1b\\" in out:
                break
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        os.close(fd)

        expected = b"\x1bPtmux;\x1b\x1b]52;c;" + self.ENCODED.encode() + b"\x07\x1b\\"
        assert expected in out, (
            "tmux のパススルーが正しい形で出ていない。ESC を二重にしないと "
            f"tmux は DCS ごと捨てる: {out!r}"
        )

    def test_without_a_controlling_terminal_it_writes_to_the_client_tty(self, tmp_path):
        # copy-pipe の子は tmux サーバの子であって端末を持たない。/dev/tty は
        # ENXIO で開けないので、クライアントの端末を tmux に聞いて直接書く。
        # ここは tmux の外へ出た後なので、パススルーで包んではいけない。
        bin_dir = tmp_path / "bin"
        target = tmp_path / "client-tty"
        target.write_text("", encoding="utf-8")
        stub_bin(bin_dir, "tmux", f'printf "%s\\n" "{target}"')
        env = self._env(tmp_path, {"TMUX": f"{tmp_path}/tmux-socket,1,0"})
        # PATH は _env が消した DISPLAY を戻さない形で足す。os.environ を丸ごと
        # マージし直すと X の枝に落ちて、この経路を一度も通らない。
        env["PATH"] = f"{bin_dir}:{env['PATH']}"

        res = subprocess.run(
            [str(PBCOPY)],
            input="hi",
            text=True,
            capture_output=True,
            env=env,
            start_new_session=True,
            timeout=30,
        )

        assert res.returncode == 0, res.stderr
        written = target.read_bytes()
        assert written == b"\x1b]52;c;" + self.ENCODED.encode() + b"\x07", (
            f"クライアント端末へ素の OSC 52 が出ていない: {written!r}"
        )

    def test_no_reachable_clipboard_fails_loudly(self, tmp_path):
        # 出口が一つも無いときに 0 を返すのが一番たちが悪い。tmux の bind から
        # 呼ばれると失敗が画面に出ないので、せめて終了ステータスで分かるように。
        env = self._env(tmp_path, {})
        res = subprocess.run(
            [str(PBCOPY)],
            input="hi",
            text=True,
            capture_output=True,
            env=env,
            start_new_session=True,
            timeout=30,
        )

        assert res.returncode != 0, (
            "届く経路が無いのに成功を返した。呼び出し側は貼り付けられると 思い込む"
        )


class TestPosixShellScripts:
    @pytest.mark.skipif(shutil.which("dash") is None, reason="dash not installed")
    @pytest.mark.parametrize("script", [PBCOPY, CLIP_TO_ANDROID], ids=lambda p: p.name)
    def test_dash_can_parse_it(self, script):
        # install.sh が置く先は Debian で、そこの /bin/sh は dash。bash 前提の
        # 書き方が混ざると実機だけで落ちる。/bin/sh へフォールバックしないのは、
        # そこが bash の環境では bashism を通したまま緑になり、何も検査して
        # いないことが見えなくなるため。
        res = subprocess.run(
            [shutil.which("dash"), "-n", str(script)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert res.returncode == 0, res.stderr


class TestTmuxCopyBindsReachTheHostClipboard:
    """copy-mode の y/Enter は pbcopy を通す。

    Linux 側の bind は `xsel -ip && xsel -op | xsel -ib` のままだった。X の
    クリップボードは VM の中で閉じているので、Android の Linux ターミナルで
    矩形コピーしても母艦へは何も渡らない。届く先を一つに決める役目は
    scripts/pbcopy にあるので、bind はそれを呼ぶだけにする。
    """

    def _copy_binds(self):
        conf = (REPO_ROOT / ".tmux.conf").read_text(encoding="utf-8")
        return [
            line.strip()
            for line in conf.splitlines()
            if re.match(r"\s*bind -T copy-mode-vi (y|Enter)\b", line)
        ]

    def test_copy_binds_exist_for_both_keys(self):
        binds = self._copy_binds()
        assert len(binds) == 2, f"y と Enter の bind が揃っていない: {binds}"

    def test_no_copy_bind_pipes_into_xsel(self):
        for bind in self._copy_binds():
            assert "xsel" not in bind, (
                "y/Enter が xsel に流れている。X のクリップボードは VM 内で "
                f"閉じていて Android には届かない: {bind}"
            )

    def test_every_copy_bind_pipes_into_pbcopy(self):
        for bind in self._copy_binds():
            assert "copy-pipe-and-cancel" in bind and "pbcopy" in bind, (
                f"y/Enter が pbcopy を通っていない: {bind}"
            )

    def test_copy_binds_sit_outside_the_os_branch(self):
        """`if-shell` のブロックの中に戻っていないこと。

        中身だけ見ていると、片方の枝にだけ pbcopy の bind を置いた形
        (= もう片方の OS でコピーが死ぬ) を通してしまう。ブレースの数え上げは
        `#{mouse_x}` のようなフォーマット指定にも当たるので、`if-shell ... {`
        で開いて単独の `}` / `} {` で閉じるこのファイルの書き方だけを追う。
        """
        depth = 0
        for line in (REPO_ROOT / ".tmux.conf").read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if re.match(r"bind -T copy-mode-vi (y|Enter)\b", stripped):
                assert depth == 0, (
                    "コピーの bind が OS 分岐の中に入っている。片方の枝にしか "
                    f"置かれていないと、もう片方の OS で黙ってコピーが死ぬ: {stripped}"
                )
            if stripped in ("}", "} {"):
                depth -= 1
            if re.match(r"(if-shell|%if)\b.*\{$", stripped) or stripped == "} {":
                depth += 1


# --------------------------------------------------------------------------
# scripts/clip_to_android.sh -- Android の共有ストレージ経由の逃げ道
# --------------------------------------------------------------------------


def textarea_body(html_path) -> str:
    """生成された index.html の textarea の中身を、エスケープを戻して返す。

    `<textarea>` 直後の改行 1 個は HTML パーサが捨てる規則なので、比較する側
    でも同じように落とす。ここを合わせないと「原文どおりに貼れるか」ではなく
    「パーサの癖を再現できているか」を測るテストになってしまう。
    """
    text = html_path.read_text(encoding="utf-8")
    match = re.search(r"<textarea[^>]*>\n?(.*?)</textarea>", text, re.S)
    assert match, "index.html に textarea が無い"
    return html.unescape(match.group(1))


class TestClipToAndroid:
    """tmux の選択範囲を Android から読める場所へ置く。

    Android の Linux ターミナル (AVF) では、端末アプリが readClipboard を
    呼ばない限り VM のクリップボードは本体へ渡らない。ゲスト側から通知する
    口が無いので、届く保証があるのは共有ストレージ (/mnt/shared =
    /storage/emulated/0) にファイルを置く経路だけになる。

    このスクリプトの核心は「原文をバイト単位で保つ」こと。ブラウザで開く
    textarea の中身がそのままコピーされるので、1 バイトでも増減すると
    貼り付けた結果が変わる。
    """

    def _run(self, tmp_path, payload: bytes, args=(), with_pbcopy=False):
        share = tmp_path / "share"
        bin_dir = tmp_path / "bin"
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "CLIP_TO_ANDROID_DIR": str(share),
        }
        if with_pbcopy:
            stub_bin(bin_dir, "pbcopy", f'cat > "{tmp_path}/pbcopy-saw"')
        else:
            bin_dir.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            [str(CLIP_TO_ANDROID), *args],
            input=payload,
            capture_output=True,
            env=env,
            timeout=30,
        )
        return res, share

    def test_payload_survives_byte_for_byte(self, tmp_path):
        # 矩形コピーは末尾に改行が付かない。ここで 1 バイト増えると、貼り付けた
        # 側に余計な改行が入る。
        payload = b"BLK01\nBLK02\nBLK03"
        res, share = self._run(tmp_path, payload)

        assert res.returncode == 0, res.stderr
        assert (share / "latest.txt").read_bytes() == payload
        assert textarea_body(share / "index.html") == payload.decode()

    def test_a_trailing_newline_is_not_eaten(self, tmp_path):
        # 逆向きの取りこぼし。sed の実装差を決め打ちして無条件に 1 バイト削ると、
        # GNU sed の環境では最後の 1 文字が消える。
        payload = b"line-copy\n"
        res, share = self._run(tmp_path, payload)

        assert res.returncode == 0, res.stderr
        assert (share / "latest.txt").read_bytes() == payload
        assert textarea_body(share / "index.html") == payload.decode()

    def test_html_metacharacters_are_escaped_and_restore(self, tmp_path):
        # エスケープ漏れは表示が壊れるだけでなく、textarea が途中で閉じて
        # 貼り付け内容が欠ける。
        payload = "if a < b && c > d\n</textarea>".encode()
        res, share = self._run(tmp_path, payload)

        assert res.returncode == 0, res.stderr
        raw = (share / "index.html").read_text(encoding="utf-8")
        assert "&lt;/textarea&gt;" in raw, "textarea を閉じるタグが素通りしている"
        assert textarea_body(share / "index.html") == payload.decode()

    def test_it_also_feeds_pbcopy(self, tmp_path):
        # Y は y の上位互換であってほしい: 共有ストレージへ出すついでに VM の
        # クリップボードにも入れる。
        payload = b"both-places"
        res, _ = self._run(tmp_path, payload, with_pbcopy=True)

        assert res.returncode == 0, res.stderr
        assert (tmp_path / "pbcopy-saw").read_bytes() == payload

    def test_clear_empties_the_share(self, tmp_path):
        # パスワードを流してしまったときの後始末。消せないと平文が残り続ける。
        self._run(tmp_path, b"secret-token")
        res, share = self._run(tmp_path, b"", args=("--clear",))

        assert res.returncode == 0, res.stderr
        assert (share / "latest.txt").read_bytes() == b""
        assert textarea_body(share / "index.html") == ""

    def test_unwritable_share_fails_loudly(self, tmp_path):
        # マウントされていない環境で 0 を返すと、Android 側に置けたと思い込む。
        share = tmp_path / "blocked"
        share.write_text("not a directory", encoding="utf-8")
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "CLIP_TO_ANDROID_DIR": str(share / "under-a-file"),
        }
        res = subprocess.run(
            [str(CLIP_TO_ANDROID)],
            input=b"x",
            capture_output=True,
            env=env,
            timeout=30,
        )

        assert res.returncode != 0, "書けなかったのに成功を返した"
        # スクリプト名だけの照合では足りない: リダイレクト失敗時にシェル自身が
        # 出すエラー文にもスクリプト名が入るので、die を通ったことにならない。
        assert "作れません".encode() in res.stderr, res.stderr

    def test_a_read_only_share_fails_loudly_too(self, tmp_path):
        """ディレクトリはあるのに書けない場合 (読み取り専用マウント、容量不足)。

        `set -e` に任せると die を通らずに落ちる。copy-pipe から呼ばれた子には
        端末が無く stderr もどこにも出ないので、tmux のステータス行に出す die を
        必ず経由させないと「成功した」ようにしか見えない。
        """
        share = tmp_path / "readonly"
        share.mkdir()
        share.chmod(0o500)
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "CLIP_TO_ANDROID_DIR": str(share),
        }
        try:
            res = subprocess.run(
                [str(CLIP_TO_ANDROID)],
                input=b"x",
                capture_output=True,
                env=env,
                timeout=30,
            )
        finally:
            share.chmod(0o700)

        assert res.returncode != 0, "書けなかったのに成功を返した"
        assert "書き込めません".encode() in res.stderr, (
            f"die を通っていないので利用者に何も伝わらない: {res.stderr!r}"
        )
        assert not list(share.iterdir()), "壊れかけのファイルが残っている"


class TestTmuxSharesTheSelectionWithAndroid:
    def test_Y_is_bound_to_the_share_helper(self):
        conf = (REPO_ROOT / ".tmux.conf").read_text(encoding="utf-8")
        binds = [
            line.strip()
            for line in conf.splitlines()
            if line.strip().startswith("bind -T copy-mode-vi Y ")
        ]
        assert len(binds) == 1, f"copy-mode の Y バインドが一意でない: {binds}"
        assert "clip_to_android.sh" in binds[0], binds[0]

    def test_install_links_the_helper_the_bind_names(self):
        # bind が呼ぶのは ~/.tmux/clip_to_android.sh。install.sh がそこへ張って
        # いなければ、キーを押しても "not found" で黙って終わる。
        install = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        assert '"$HOME/.tmux/clip_to_android.sh"' in install


class TestZshrcPath:
    """PATH entries .zshrc must own, because install.sh's artifacts land there."""

    def test_local_bin_is_on_the_path(self):
        """~/.local/bin holds install.sh's own output, so .zshrc must add it.

        install.sh fills it and never makes it reachable from a login shell:
        uv/uvx on every platform, the Debian `bat`/`fd` aliases from
        `link_debian_alias`, and `pip_install_user` output *where pip's user
        scheme is posix_user*. Its own
        `export PATH="$HOME/.local/bin:$PATH"` is scoped to the running
        script, so nothing survives the install.

        macOS is the exception and has its own test below: there pip uses the
        osx_framework_user scheme and installs to ~/Library/Python/<X.Y>/bin
        instead, so this entry alone does NOT make ruff reachable there.

        It worked on the author's machine only through an untracked
        ~/.zshenv that uv's installer happens to write -- a side effect of a
        third-party tool, not something this repo ships. When that is absent
        (uv skipped, or uv changes its installer), `command -v ruff` in
        .claude/hooks/_format_common.sh goes false and Python formatting is
        skipped in silence, and .zshrc's own fzf preview loses `bat`.
        """
        text = ZSHRC.read_text(encoding="utf-8")
        assert re.search(r"^export PATH=.*\.local/bin", text, re.M), (
            "no PATH entry for ~/.local/bin in .zshrc; install.sh's "
            "pip --user / uv / bat / fd artifacts are unreachable"
        )

    def test_macos_pip_user_bin_is_on_the_path(self):
        """macOS pip --user does NOT use ~/.local/bin, so cover its real dir.

        Both python3 builds a fresh Mac can offer install_linters_formatters
        report `osx_framework_user` as their user scheme -- Homebrew's
        (~/Library/Python/3.14/bin) and Apple's (~/Library/Python/3.9/bin) --
        not `posix_user`. ~/.local/bin is only pip's answer under pyenv, and
        install_pyenv is ubuntu-only. So on macOS the ruff/bandit/mypy that
        install.sh installs land in ~/Library/Python/<X.Y>/bin, and adding
        ~/.local/bin alone leaves `command -v ruff` false -- the hooks stay
        silently unformatted, which is the whole bug this was meant to close.

        The (N) glob qualifier collapses to nothing when the directory does
        not exist, so this line is inert on Linux and needs no OS guard, and
        the * picks up every interpreter version left on the machine.
        """
        text = ZSHRC.read_text(encoding="utf-8")
        assert re.search(r"^path=\(~/Library/Python/\*/bin\(N\)", text, re.M), (
            "no PATH entry for ~/Library/Python/*/bin in .zshrc; on macOS "
            "install.sh's pip --user linters are unreachable"
        )


class TestZshrcAliases:
    """Aliases .zshrc must not silently take away from a loaded plugin."""

    def test_g_is_left_to_the_oh_my_zsh_git_plugin(self):
        """`g` belongs to oh-my-zsh's git plugin; .zshrc must not shadow it.

        .zshrc enables the `git` plugin and then sources oh-my-zsh.sh, which
        defines `alias g='git'`. A later `alias g='gemini'` in this file wins
        silently, so the reflex `g status` runs `gemini status` -- handing the
        word "status" to an LLM CLI instead of running git. `ge` is defined
        immediately above for that purpose, so the shadow bought nothing.
        """
        text = ZSHRC.read_text(encoding="utf-8")
        # Anchored to the plugins=( ... ) block itself. A bare `"git" in text`
        # passes on .gitconfig mentions and on any comment, so it would stay
        # green after `git` left the plugin list -- leaving `alias g=` banned
        # for a reason that no longer exists.
        block = re.search(r"^plugins=\((.*?)^\)", text, re.M | re.S)
        assert block, "precondition: no plugins=( ... ) block found in .zshrc"
        assert "git" in block.group(1).split(), (
            "precondition: .zshrc is expected to load the oh-my-zsh git plugin"
        )
        assert not re.search(r"^\s*alias g=", text, re.M), (
            "alias g= in .zshrc shadows the oh-my-zsh git plugin's g='git'"
        )


def extract_zsh_env_prologue() -> str:
    """Return .zshrc's environment prologue: everything before oh-my-zsh.

    The prologue is the OS guard plus every PATH / compiler-flag export, and it
    is the one part of .zshrc that can be sourced on its own -- `export ZSH=`
    is immediately followed by the oh-my-zsh machinery the module docstring
    explains cannot run here.
    """
    text = ZSHRC.read_text(encoding="utf-8")
    index = text.find('export ZSH="$HOME/.oh-my-zsh"')
    if index == -1:
        raise AssertionError("no `export ZSH=` marker found in .zshrc")
    prologue = text[:index]
    # An empty or truncated prologue would let every assertion below pass
    # vacuously, which is exactly the failure this class exists to prevent.
    assert "_os" in prologue and "PATH" in prologue, (
        f"extracted prologue looks wrong ({len(prologue)} chars)"
    )
    return prologue


# Real `brew shellenv zsh` output, reduced to the parts .zshrc depends on.
# INFOPATH/MANPATH deliberately keep Homebrew's own append-to-self shape --
# that is what makes them grow without the typeset -U tie.
_FAKE_BREW = """#!/bin/sh
p={prefix}
cat <<EOS
export HOMEBREW_PREFIX="$p";
export HOMEBREW_CELLAR="$p/Cellar";
fpath[1,0]="$p/share/zsh/site-functions";
export PATH="$p/bin:$p/sbin${{PATH+:$PATH}}";
export MANPATH="$p/share/man${{MANPATH+:$MANPATH}}:";
export INFOPATH="$p/share/info:${{INFOPATH:-}}";
EOS
"""


class ZshPrologueHarness:
    """Run .zshrc's macOS branch on any host, against a fake Homebrew prefix.

    Without this the branch is unreachable off macOS, and *on* macOS the php
    block is skipped because the keg is not installed -- so the assertions
    below would pass no matter what the code said. A code review caught
    exactly that: reverting the append back to an assignment left every test
    green. Rewriting the absolute prefixes into tmp_path is what gives these
    tests teeth on the ubuntu CI leg.
    """

    def __init__(self, tmp_path, *, with_php_keg: bool):
        if shutil.which("zsh") is None:
            pytest.skip("zsh not installed")
        self.prefix = tmp_path / "hb"
        (self.prefix / "bin").mkdir(parents=True)
        (self.prefix / "sbin").mkdir()
        if with_php_keg:
            (self.prefix / "opt/php@8.4/lib").mkdir(parents=True)
        brew = self.prefix / "bin/brew"
        brew.write_text(_FAKE_BREW.format(prefix=self.prefix), encoding="utf-8")
        brew.chmod(0o755)

        # `uname -s` must say Darwin or the whole macOS branch is skipped.
        self.stub_bin = tmp_path / "stub"
        self.stub_bin.mkdir()
        uname = self.stub_bin / "uname"
        uname.write_text("#!/bin/sh\necho Darwin\n", encoding="utf-8")
        uname.chmod(0o755)

        self.home = tmp_path / "home"
        self.home.mkdir()
        script = extract_zsh_env_prologue()
        assert "/opt/homebrew" in script, "precondition: prologue names /opt/homebrew"
        self.script = script.replace("/opt/homebrew", str(self.prefix))

    def run(self, *, repeats: int = 1, env: dict | None = None) -> dict:
        """Source the prologue `repeats` times, then report the environment."""
        report = (
            'print -r -- "PATH=$PATH"\nprint -r -- "INFOPATH=$INFOPATH"\n'
            'print -r -- "LDFLAGS=$LDFLAGS"\nprint -r -- "CPPFLAGS=$CPPFLAGS"\n'
            'print -r -- "NFPATH=${#fpath}"'
        )
        res = subprocess.run(
            ["zsh", "-f", "-c", f"{self.script * repeats}\n{report}"],
            capture_output=True,
            text=True,
            env={
                "HOME": str(self.home),
                "PATH": f"{self.stub_bin}:/usr/bin:/bin",
                **(env or {}),
            },
            timeout=30,
        )
        assert res.returncode == 0, res.stderr
        out = dict(line.split("=", 1) for line in res.stdout.splitlines())
        out["path_entries"] = out["PATH"].split(":")
        return out


class TestZshHomebrewOnPath:
    """Homebrew must be on PATH from .zshrc, not only inside install.sh.

    install_homebrew evals `brew shellenv`, but that is a process-local export
    that dies with the script, install.sh appends to no shell rc, and the repo
    ships no .zprofile. On Apple Silicon /opt/homebrew is not in /etc/paths
    either, so following install.sh's own closing advice ("Restart your
    terminal") dropped brew and every brew-installed package off PATH. .zshrc
    already states this principle for ~/.local/bin and friends (see the
    comment above the `export PATH=~/.local/bin` line) -- Homebrew's own bin
    was the one omission.
    """

    def test_brew_lands_on_path(self, tmp_path):
        out = ZshPrologueHarness(tmp_path, with_php_keg=False).run()
        assert f"{tmp_path}/hb/bin" in out["path_entries"], out["PATH"]

    def test_user_bin_dirs_outrank_homebrew(self, tmp_path):
        """shellenv prepends, so the eval has to run *before* the user dirs.

        Moving the block below them would silently put Homebrew's copy of a
        tool ahead of the user's own.
        """
        harness = ZshPrologueHarness(tmp_path, with_php_keg=False)
        entries = harness.run()["path_entries"]
        brew_bin = f"{tmp_path}/hb/bin"
        local_bin = f"{harness.home}/.local/bin"
        for name in (brew_bin, local_bin, "/usr/bin"):
            assert name in entries, f"{name} missing from PATH: {entries}"
        assert entries.index(local_bin) < entries.index(brew_bin), (
            f"Homebrew now outranks the user's own bin dirs: {entries}"
        )
        assert entries.index(brew_bin) < entries.index("/usr/bin"), (
            f"Homebrew must still come before the system dirs: {entries}"
        )

    def test_re_sourcing_grows_nothing(self, tmp_path):
        """Idempotence is carried by `typeset -U` / `typeset -xTU`, not by a
        "skip if HOMEBREW_PREFIX is set" guard.

        That guard was tried and reverted: in a nested *login* shell
        /etc/zprofile's path_helper rewrites the inherited PATH and pushes
        /opt/homebrew/bin to the end, so skipping the re-prepend left Homebrew
        behind /usr/bin (measured: `git` resolved to /usr/bin/git). Re-running
        shellenv every time is what keeps the order right, which is only safe
        because the duplicates are removed by type.
        """
        harness = ZshPrologueHarness(tmp_path, with_php_keg=False)
        once, thrice = harness.run(repeats=1), harness.run(repeats=3)
        # Without this the comparison holds vacuously if the brew block never
        # ran at all: "" == "" would pass while pinning nothing.
        assert once["INFOPATH"], "the fake shellenv never ran; nothing is pinned"
        for key in ("PATH", "INFOPATH", "NFPATH"):
            assert once[key] == thrice[key], (
                f"{key} grew across re-sources: {once[key]!r} -> {thrice[key]!r}"
            )

    def test_inherited_prefix_still_re_prepends(self, tmp_path):
        """The regression that killed the `HOMEBREW_PREFIX` guard.

        A nested *login* shell inherits HOMEBREW_PREFIX, and /etc/zprofile's
        path_helper rewrites the inherited PATH so /opt/homebrew/bin lands at
        the end. Re-running shellenv is what pulls it back in front of
        /usr/bin; a "skip if HOMEBREW_PREFIX is set" guard leaves it behind
        (measured on a real nested `zsh -l -i`: `git` resolved to
        /usr/bin/git). The other tests here all start from a clean env, so the
        guard slipped past every one of them -- this is the only one that
        reproduces the inherited shape.
        """
        harness = ZshPrologueHarness(tmp_path, with_php_keg=False)
        prefix = str(harness.prefix)
        entries = harness.run(
            env={
                "HOMEBREW_PREFIX": prefix,
                # path_helper's output shape: system dirs first, the inherited
                # Homebrew entries demoted to the tail.
                "PATH": f"{harness.stub_bin}:/usr/bin:/bin:{prefix}/bin:{prefix}/sbin",
            }
        )["path_entries"]
        assert entries.index(f"{prefix}/bin") < entries.index("/usr/bin"), (
            f"Homebrew was left behind the system dirs: {entries}"
        )


class TestZshCompilerFlags:
    """The prologue must not touch LDFLAGS / CPPFLAGS at all.

    It used to, for a `php@8.4` keg: assigned rather than appended, and guarded
    on the OS but not on the formula, so on macOS every interactive shell threw
    away whatever ~/.zshenv, direnv or a parent shell had set and replaced it
    with flags for a keg that is not installed -- exactly the hazard .zshrc's
    opening comment describes. The block was then deleted rather than hardened:
    install.sh pulls plain `php` in as a php-cs-fixer dependency and never
    `php@8.4`, so on a machine this repo built the block could not run at all.

    Both tests stand up a fake keg on purpose. With the keg absent, deleting
    the block has no observable signature -- these would pass either way.
    """

    INHERITED = {
        "LDFLAGS": "-L/somewhere/openssl@3/lib",
        "CPPFLAGS": "-I/somewhere/openssl@3/include",
    }

    def test_sets_no_flags_even_with_the_keg_present(self, tmp_path):
        out = ZshPrologueHarness(tmp_path, with_php_keg=True).run()
        assert out["LDFLAGS"] == "", (
            f"the prologue is setting compiler flags again: {out['LDFLAGS']!r}"
        )
        assert out["CPPFLAGS"] == "", out["CPPFLAGS"]

    def test_inherited_flags_pass_through_untouched(self, tmp_path):
        out = ZshPrologueHarness(tmp_path, with_php_keg=True).run(env=self.INHERITED)
        assert out["LDFLAGS"] == self.INHERITED["LDFLAGS"], (
            f"inherited LDFLAGS was modified: {out['LDFLAGS']!r}"
        )
        assert out["CPPFLAGS"] == self.INHERITED["CPPFLAGS"], out["CPPFLAGS"]
