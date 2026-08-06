"""Tests for utility scripts and syntax checks for all shell configs."""

import os
import re
import shutil
import signal
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
            "npm update -g @openai/codex",
            "npm upgrade -g @google/gemini-cli",
            "copilot update",
            "codex --version",
            "gemini --version",
            "copilot --version",
        ):
            assert call in shell_env.calls, (
                f"{call!r} never ran after an earlier tool failed"
            )


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
