"""Tests for install.sh (sourced; main() is guarded and never runs here)."""

import json
import subprocess

from conftest import REPO_ROOT

INSTALL = REPO_ROOT / "install.sh"


def run_sourced(snippet: str, env: dict, cwd=None):
    script = f'source "{INSTALL}"\n{snippet}\n'
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=120,
    )


class TestSourceGuard:
    def test_sourcing_does_not_run_main(self, shell_env):
        res = run_sourced("true", shell_env.env)
        assert res.returncode == 0
        assert res.stdout == ""

    def test_piped_execution_outside_checkout_fails_with_guidance(
        self, shell_env, tmp_path
    ):
        # `cat install.sh | bash` from a non-checkout dir must still reach
        # main() and fail fast with the clone-the-repo error (exit 1),
        # before any package installation is attempted.
        outside = tmp_path / "not-a-checkout"
        outside.mkdir()
        res = subprocess.run(
            ["bash"],
            input=INSTALL.read_text(encoding="utf-8"),
            capture_output=True,
            text=True,
            env=shell_env.env,
            cwd=outside,
            timeout=60,
        )
        assert res.returncode == 1
        assert "Dotfiles repository not found" in res.stdout


class TestDetectOs:
    def test_darwin_is_macos(self, shell_env):
        # OSTYPE を明示して host OS に依存しない（Linux CI 上でも成立させる）
        res = run_sourced('OSTYPE=darwin24 detect_os && echo "OS=$OS"', shell_env.env)
        assert "Detected OS: macos" in res.stdout
        assert "OS=macos" in res.stdout

    def test_linux_gnu_without_debian_marker_is_linux(self, shell_env):
        # debian マーカーを不在パスに差し替え、host に /etc/debian_version が
        # あっても linux 分岐を確定的に検証する
        res = run_sourced(
            "OSTYPE=linux-gnu DEBIAN_VERSION_FILE=/nonexistent "
            'detect_os && echo "OS=$OS"',
            shell_env.env,
        )
        assert "OS=linux" in res.stdout

    def test_linux_gnu_with_debian_marker_is_ubuntu(self, shell_env, tmp_path):
        marker = tmp_path / "debian_version"
        marker.write_text("13\n", encoding="utf-8")
        res = run_sourced(
            f'OSTYPE=linux-gnu DEBIAN_VERSION_FILE="{marker}" '
            'detect_os && echo "OS=$OS"',
            shell_env.env,
        )
        assert "OS=ubuntu" in res.stdout

    def test_msys_is_windows(self, shell_env):
        res = run_sourced('OSTYPE=msys detect_os && echo "OS=$OS"', shell_env.env)
        assert "OS=windows" in res.stdout

    def test_wsl_distro_name_does_not_override_ubuntu_detection(
        self, shell_env, tmp_path
    ):
        # WSL always reports OSTYPE=linux-gnu, so it is caught by the
        # linux-gnu branch (and classified ubuntu/linux there) before the
        # windows branch is ever reached. Setting WSL_DISTRO_NAME must not
        # flip detection to "windows" -- this locks in the longstanding
        # "WSL is treated as Ubuntu" behavior after removing the dead
        # WSL_DISTRO_NAME check from the windows branch.
        marker = tmp_path / "debian_version"
        marker.write_text("13\n", encoding="utf-8")
        res = run_sourced(
            f'OSTYPE=linux-gnu WSL_DISTRO_NAME=Ubuntu DEBIAN_VERSION_FILE="{marker}" '
            'detect_os && echo "OS=$OS"',
            shell_env.env,
        )
        assert "OS=ubuntu" in res.stdout

    def test_unknown_os_fails(self, shell_env):
        res = run_sourced("OSTYPE=solaris detect_os", shell_env.env)
        assert res.returncode == 1
        assert "Unsupported operating system" in res.stdout


class TestCreateSymlinks:
    def test_links_backups_and_copies(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        (home / ".zshrc").write_text("old content\n", encoding="utf-8")
        # A stale symlink must be replaced without being backed up.
        (home / ".vimrc").symlink_to("/nonexistent-target")

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0

        # Top-level dotfiles are symlinked into the repo.
        for name in (".zshrc", ".vimrc", ".tmux.conf", ".gitconfig", ".wezterm.lua"):
            link = home / name
            assert link.is_symlink(), f"{name} should be a symlink"
            assert link.resolve() == (REPO_ROOT / name).resolve()

        # Claude / Gemini entries are symlinked individually.
        assert (home / ".claude/settings.json").is_symlink()
        assert (home / ".claude/hooks").is_symlink()
        assert (home / ".gemini/settings.json").is_symlink()
        assert (home / ".config/nvim").resolve() == (
            REPO_ROOT / ".config/nvim"
        ).resolve()

        # Codex: exact-path entries are symlinked, scanned dirs are copied.
        assert (home / ".codex/AGENTS.md").is_symlink()
        assert (home / ".codex/hooks").is_symlink()
        assert not (home / ".codex/agents").is_symlink()
        assert (home / ".codex/agents").is_dir()
        skills = home / ".codex/skills/coding-standards"
        assert skills.is_dir() and not skills.is_symlink()

        # Oh My Zsh custom dir stays a REAL directory (install_oh_my_zsh
        # clones plugins into custom/plugins/); only the theme file(s)
        # tracked in the repo are symlinked in.
        assert not (home / ".oh-my-zsh/custom").is_symlink()
        assert (home / ".oh-my-zsh/custom").is_dir()
        theme_link = home / ".oh-my-zsh/custom/themes/px-rose-pine.zsh-theme"
        assert theme_link.is_symlink()
        assert (
            theme_link.resolve()
            == (REPO_ROOT / ".oh-my-zsh/custom/themes/px-rose-pine.zsh-theme").resolve()
        )
        assert (home / ".tmux/tmux_send_to_all_except_nvim.sh").is_symlink()

        # The pre-existing real .zshrc was backed up; the stale symlink was not.
        backups = list(home.glob(".dotfiles_backup_*"))
        assert len(backups) == 1
        assert (backups[0] / ".zshrc").read_text(encoding="utf-8") == "old content\n"
        assert not (backups[0] / ".vimrc").exists()

    def test_rerun_is_idempotent(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()

        first = run_sourced("create_symlinks", shell_env.env)
        assert first.returncode == 0
        second = run_sourced("create_symlinks", shell_env.env)
        assert second.returncode == 0

        assert (home / ".zshrc").is_symlink()
        assert (home / ".zshrc").resolve() == (REPO_ROOT / ".zshrc").resolve()
        assert (home / ".codex/skills/coding-standards").is_dir()

    def test_backup_preserves_files_sharing_a_basename(self, shell_env):
        # settings.json exists as a real file under BOTH ~/.claude and
        # ~/.config/Code/User. A flat, basename-only backup would move the
        # first into $backup_dir/settings.json and then overwrite it with the
        # second, destroying one of the user's real configs. Both must survive.
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        (home / ".claude").mkdir(parents=True)
        (home / ".claude/settings.json").write_text("CLAUDE-REAL\n", encoding="utf-8")
        (home / ".config/Code/User").mkdir(parents=True)
        (home / ".config/Code/User/settings.json").write_text(
            "CODE-REAL\n", encoding="utf-8"
        )

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr

        backups = list(home.glob(".dotfiles_backup_*"))
        assert len(backups) == 1
        recovered = sorted(
            p.read_text(encoding="utf-8") for p in backups[0].rglob("settings.json")
        )
        assert recovered == ["CLAUDE-REAL\n", "CODE-REAL\n"], recovered

    def test_rerun_does_not_accumulate_backup_dirs(self, shell_env):
        # copy_entry targets (Codex agents/skills) and the rendered hooks.json
        # are real files. A non-idempotent copy backs its own previous output
        # into a fresh timestamped dir on every run, so ~/.dotfiles_backup_*
        # piles up. A second no-change run must leave zero backup dirs.
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()

        first = run_sourced("create_symlinks", shell_env.env)
        assert first.returncode == 0, first.stderr
        second = run_sourced("create_symlinks", shell_env.env)
        assert second.returncode == 0, second.stderr

        assert list(home.glob(".dotfiles_backup_*")) == []
        # The copies and rendered file are still in place after the no-op rerun.
        assert (home / ".codex/skills/coding-standards").is_dir()
        assert (home / ".codex/hooks.json").is_file()

    def test_no_backup_dir_left_when_nothing_backed_up(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0
        # Fresh HOME: only copy_entry targets existed, and those are new too,
        # so the timestamped backup dir must have been removed as empty.
        assert list(home.glob(".dotfiles_backup_*")) == []

    def test_links_vscode_and_antigravity_configs_on_linux(self, shell_env):
        # No OS set (mirrors "no OS var in the ambient test env"): the
        # non-macos / Linux-style ~/.config destinations must be used.
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr

        vscode_user = home / ".config/Code/User"
        antigravity_user = home / ".config/Antigravity/User"
        for name in ("settings.json", "keybindings.json"):
            code_link = vscode_user / name
            assert code_link.is_symlink(), f"Code {name} should be linked"
            assert (
                code_link.resolve()
                == (REPO_ROOT / ".config/Code/User" / name).resolve()
            )

            ag_link = antigravity_user / name
            assert ag_link.is_symlink(), f"Antigravity {name} should be linked"
            assert (
                ag_link.resolve()
                == (REPO_ROOT / ".config/Antigravity" / name).resolve()
            )

    def test_links_vscode_and_antigravity_configs_on_macos(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        env = dict(shell_env.env)
        env["OS"] = "macos"

        res = run_sourced("create_symlinks", env)
        assert res.returncode == 0, res.stderr

        vscode_user = home / "Library/Application Support/Code/User"
        antigravity_user = home / "Library/Application Support/Antigravity/User"
        for name in ("settings.json", "keybindings.json"):
            assert (vscode_user / name).is_symlink()
            assert (antigravity_user / name).is_symlink()


class TestInstallAiTools:
    def test_skips_prompt_when_non_interactive(self, shell_env):
        # Piped/CI runs have no TTY on stdin. A bare `read` returns non-zero at
        # EOF and, under `set -e`, would abort the whole installer before the
        # later steps (MCP registration, shell change). The function must skip
        # cleanly and return 0 instead of aborting.
        res = run_sourced('install_ai_tools </dev/null; echo "RC=$?"', shell_env.env)
        assert "RC=0" in res.stdout, res.stdout + res.stderr
        assert "Non-interactive" in res.stdout
        assert "Do you want to install" not in res.stdout


class TestStrictMode:
    def test_pipefail_enabled(self, shell_env):
        # Pipelines like `curl ... | sudo tee` must not swallow curl's exit
        # status. Test the actual behavior (not `set -o` text) so it stays
        # meaningful even if the option is enabled a different way.
        res = run_sourced(
            "if false | true; then echo RESULT=swallowed; "
            "else echo RESULT=pipefail-detected; fi",
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "RESULT=pipefail-detected" in res.stdout


class TestInstallOhMyZsh:
    def _stub_git_clone(self, shell_env):
        # Simulate `git clone URL DEST` by creating an empty DEST dir.
        body = 'if [ "$1" = "clone" ]; then\n  mkdir -p "${@: -1}"\nfi'
        shell_env.stub("git", body=body)

    def test_heals_symlinked_custom_dir_before_cloning_plugins(
        self, shell_env, tmp_path
    ):
        # A previous buggy install symlinked $HOME/.oh-my-zsh/custom straight
        # into the dotfiles checkout, which only ships themes/ (no plugins/).
        # install_oh_my_zsh must convert it back to a real directory BEFORE
        # cloning plugins, or the clone lands inside the checkout.
        self._stub_git_clone(shell_env)
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        fake_dotfiles_custom = tmp_path / "fake-dotfiles" / ".oh-my-zsh" / "custom"
        (fake_dotfiles_custom / "themes").mkdir(parents=True)
        (home / ".oh-my-zsh/custom").symlink_to(fake_dotfiles_custom)

        res = run_sourced("install_oh_my_zsh", shell_env.env)
        assert res.returncode == 0, res.stderr

        custom = home / ".oh-my-zsh/custom"
        assert not custom.is_symlink()
        assert custom.is_dir()
        assert (custom / "plugins/zsh-autosuggestions").is_dir()
        assert (custom / "plugins/zsh-syntax-highlighting").is_dir()
        # The clone must never have landed inside the (simulated) checkout.
        assert not (fake_dotfiles_custom / "plugins").exists()

    def test_rerun_does_not_reclone_existing_plugins(self, shell_env):
        self._stub_git_clone(shell_env)
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()

        first = run_sourced("install_oh_my_zsh", shell_env.env)
        assert first.returncode == 0, first.stderr
        clone_calls_1 = [c for c in shell_env.calls if c.startswith("git clone")]

        second = run_sourced("install_oh_my_zsh", shell_env.env)
        assert second.returncode == 0, second.stderr
        clone_calls_2 = [c for c in shell_env.calls if c.startswith("git clone")]

        assert clone_calls_2 == clone_calls_1
        assert not (home / ".oh-my-zsh/custom").is_symlink()


class TestRegisterClaudeMcpServers:
    def test_gemini_consultant_uses_resolved_python3(self, shell_env):
        # `claude mcp get NAME` must fail so add_mcp proceeds to register.
        shell_env.stub("claude", body='[ "$1" = "mcp" ] && [ "$2" = "get" ] && exit 1')
        shell_env.stub("python3")

        res = run_sourced("register_claude_mcp_servers", shell_env.env)
        assert res.returncode == 0, res.stderr

        add_calls = [c for c in shell_env.calls if c.startswith("claude mcp add")]
        gemini_calls = [c for c in add_calls if "gemini-consultant" in c]
        assert len(gemini_calls) == 1, add_calls

        python3_path = str(shell_env.stub_bin / "python3")
        assert f"-- {python3_path} " in gemini_calls[0]


class TestChangeShell:
    def test_chsh_failure_does_not_abort_script(self, shell_env):
        shell_env.stub("zsh")
        shell_env.stub("chsh", exit_code=1)
        env = dict(shell_env.env)
        env["SHELL"] = "/bin/bash"

        res = run_sourced('change_shell; echo "AFTER_CHANGE_SHELL"', env)

        assert res.returncode == 0, res.stderr
        assert "AFTER_CHANGE_SHELL" in res.stdout
        assert "chsh failed" in res.stdout


class TestHooksJsonTemplate:
    def test_renders_hooks_json_with_resolved_home(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr

        rendered = home / ".codex/hooks.json"
        assert rendered.is_file()
        assert not rendered.is_symlink()
        assert not (home / ".codex/hooks.json.template").exists()

        content = rendered.read_text(encoding="utf-8")
        assert "__HOME__" not in content
        assert str(home) in content
        assert json.loads(content)  # must still be valid JSON
        # Hooks must invoke python3: bare `python` does not exist on stock
        # Ubuntu or Homebrew installs (same rationale as the MCP registration).
        assert "python3 '" in content
        assert "python '" not in content

    def test_rerun_regenerates_hooks_json(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()

        first = run_sourced("create_symlinks", shell_env.env)
        assert first.returncode == 0, first.stderr
        second = run_sourced("create_symlinks", shell_env.env)
        assert second.returncode == 0, second.stderr

        rendered = home / ".codex/hooks.json"
        assert rendered.is_file()
        assert not rendered.is_symlink()
        assert str(home) in rendered.read_text(encoding="utf-8")
