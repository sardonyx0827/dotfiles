"""Tests for install.sh (sourced; main() is guarded and never runs here)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

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


# Never stripped by _without_commands: these carry core utilities
# (dirname, touch, sudo, curl, sed, cmp, mktemp, ...) and, for /bin on this
# platform, bash itself. Some dev-machine tools (pip3, vim, zsh) have a
# second copy living in one of these -- e.g. Apple ships /bin/zsh and
# /usr/bin/vim/pip3 alongside the homebrew/pyenv ones -- so blindly
# removing the owning directory for every match can silently take PATH
# lookups (or the bash subprocess itself) down with it.
_PROTECTED_PATH_DIRS = {"/bin", "/usr/bin", "/sbin", "/usr/sbin"}


def _without_commands(env: dict, *names: str) -> dict:
    """Strip real PATH directories that would resolve any of `names`.

    Several tools under test (go, glow, staticcheck, ...) are genuinely
    installed on a developer workstation, so a bare `command -v` check
    would find the real one and mask the very "not installed" branch a
    test wants to exercise. Removing the owning directory makes absence
    real rather than a stubbed override. Stops at a protected system
    directory instead of removing it (see _PROTECTED_PATH_DIRS) -- a
    command with a surviving copy there needs a function-shadow instead
    (see TestChangeShell / TestInstallVimPlugins for zsh/vim).
    """
    env = dict(env)
    for name in names:
        while True:
            found = shutil.which(name, path=env["PATH"])
            if not found:
                break
            parent = Path(found).parent
            if parent.name == "stub-bin":
                # The shared stub dir (which now carries backstop stubs for
                # system-mutating tools) must not be dropped wholesale — that
                # would silently discard every other stub the test set up.
                # Delete just this stub to make the tool absent.
                Path(found).unlink()
                continue
            drop = str(parent)
            if drop in _PROTECTED_PATH_DIRS:
                break
            env["PATH"] = ":".join(p for p in env["PATH"].split(":") if p != drop)
    return env


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

        # link_oh_my_zsh_theme is split out of create_symlinks (see
        # TestOhMyZshFreshInstallOrdering) and is only called after
        # install_oh_my_zsh in main(); call it explicitly here since this
        # test asserts on the theme symlink it produces.
        res = run_sourced("create_symlinks && link_oh_my_zsh_theme", shell_env.env)
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

        # Codex resolves symlinks when scanning agents/ and skills/, so both
        # are linked as whole directories rather than copied.
        assert (home / ".codex/AGENTS.md").is_symlink()
        assert (home / ".codex/hooks").is_symlink()
        for name in ("agents", "skills"):
            link = home / ".codex" / name
            assert link.is_symlink(), f".codex/{name} should be a symlink"
            assert link.resolve() == (REPO_ROOT / ".codex" / name).resolve()

        # A shared skill resolves through BOTH hops -- the linked skills dir and
        # the repo's own .codex/skills/<name> -> ../../.claude/skills/<name>
        # link -- landing on the single source of truth under .claude/skills.
        skill = home / ".codex/skills/backend-patterns"
        assert skill.is_dir()
        assert (
            skill.resolve() == (REPO_ROOT / ".claude/skills/backend-patterns").resolve()
        )
        assert (skill / "SKILL.md").is_file()

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

    # --- Codex config.toml: seeded, never linked, never clobbered -----------
    # Codex owns this file at runtime: `codex mcp add` writes mcp_servers into
    # it, Authorization headers included, plus projects/ and plugin state. The
    # old symlink pointed it straight at the checkout, so everything Codex
    # wrote landed in the working tree, one `git add` from committing a token.

    def test_codex_config_is_seeded_as_a_real_file_not_a_symlink(self, shell_env):
        home = shell_env.home
        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0

        config = home / ".codex/config.toml"
        assert config.is_file()
        assert not config.is_symlink(), (
            "config.toml must never be a symlink: Codex would write secrets "
            "straight into the checkout"
        )
        assert config.read_text(encoding="utf-8") == (
            REPO_ROOT / ".codex/config.toml.template"
        ).read_text(encoding="utf-8")

    def test_codex_config_written_by_codex_survives_a_rerun(self, shell_env):
        """The whole point: re-rendering would delete the user's MCP servers."""
        home = shell_env.home
        (home / ".codex").mkdir(parents=True)
        live = home / ".codex/config.toml"
        live.write_text(
            '[mcp_servers.github.http_headers]\nAuthorization = "secret"\n',
            encoding="utf-8",
        )

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0
        assert 'Authorization = "secret"' in live.read_text(encoding="utf-8")

    def test_codex_config_symlink_is_replaced_with_a_real_file(self, shell_env):
        """Self-heal the older install that linked it into the checkout."""
        home = shell_env.home
        (home / ".codex").mkdir(parents=True)
        (home / ".codex/config.toml").symlink_to(
            REPO_ROOT / ".codex/config.toml.template"
        )

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0

        config = home / ".codex/config.toml"
        assert config.is_file()
        assert not config.is_symlink()

    # --- Git identity: rendered per machine, never tracked ------------------

    def test_git_identity_is_inherited_from_the_previous_config(
        self, shell_env, tmp_path
    ):
        """Upgrading from a ~/.gitconfig that carried [user] keeps it."""
        prior = tmp_path / "prior-gitconfig"
        prior.write_text(
            "[user]\n\tname = Prior Person\n\temail = prior@example.com\n",
            encoding="utf-8",
        )
        env = {**shell_env.env, "GIT_CONFIG_GLOBAL": str(prior)}

        res = run_sourced("create_symlinks", env)
        assert res.returncode == 0

        rendered = (shell_env.home / ".config/git/user.gitconfig").read_text(
            encoding="utf-8"
        )
        assert "name = Prior Person" in rendered
        assert "email = prior@example.com" in rendered

    def test_git_identity_is_never_overwritten(self, shell_env):
        home = shell_env.home
        (home / ".config/git").mkdir(parents=True)
        existing = home / ".config/git/user.gitconfig"
        existing.write_text("[user]\n\tname = Do Not Touch\n", encoding="utf-8")

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0
        assert existing.read_text(encoding="utf-8") == "[user]\n\tname = Do Not Touch\n"

    def test_git_identity_placeholder_is_inert_when_unknown(self, shell_env):
        """Non-interactive with nothing to inherit: warn, never guess.

        The keys stay commented out -- an empty `name =` would make git report a
        configured-but-blank identity rather than telling the user to set one.
        """
        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0

        rendered = (shell_env.home / ".config/git/user.gitconfig").read_text(
            encoding="utf-8"
        )
        assert "#[user]" in rendered
        for line in rendered.splitlines():
            assert line.startswith("#"), f"placeholder must be inert: {line!r}"

    def test_tracked_gitconfig_carries_no_identity(self):
        """Guard the leak itself, not just the machinery that avoids it."""
        text = (REPO_ROOT / ".gitconfig").read_text(encoding="utf-8")
        # Match the section header, not the substring: the file explains in a
        # comment why [user] is absent, and that comment is not a section.
        sections = [
            ln.strip()
            for ln in text.splitlines()
            if not ln.lstrip().startswith("#") and ln.strip().startswith("[")
        ]
        assert "[user]" not in sections, (
            ".gitconfig must not carry a [user] section: anyone who clones this "
            "repo and links it would commit under the owner's name and address"
        )
        assert "path = ~/.config/git/user.gitconfig" in text

    def test_rerun_is_idempotent(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()

        first = run_sourced("create_symlinks", shell_env.env)
        assert first.returncode == 0
        second = run_sourced("create_symlinks", shell_env.env)
        assert second.returncode == 0

        assert (home / ".zshrc").is_symlink()
        assert (home / ".zshrc").resolve() == (REPO_ROOT / ".zshrc").resolve()
        assert (home / ".codex/skills/backend-patterns").is_dir()

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
        # The rendered hooks.json is the one real file install.sh writes under
        # ~/.codex. Re-rendering it unconditionally would back its own previous
        # output into a fresh timestamped dir on every run, so ~/.dotfiles_backup_*
        # would pile up. A second no-change run must leave zero backup dirs.
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()

        first = run_sourced("create_symlinks", shell_env.env)
        assert first.returncode == 0, first.stderr
        second = run_sourced("create_symlinks", shell_env.env)
        assert second.returncode == 0, second.stderr

        assert list(home.glob(".dotfiles_backup_*")) == []
        # The links and rendered file are still in place after the no-op rerun.
        assert (home / ".codex/skills/backend-patterns").is_dir()
        assert (home / ".codex/hooks.json").is_file()

    def test_no_backup_dir_left_when_nothing_backed_up(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0
        # Fresh HOME: nothing real pre-existed, so the timestamped backup dir
        # must have been removed as empty.
        assert list(home.glob(".dotfiles_backup_*")) == []

    def test_migration_off_copies_backs_up_rather_than_deletes(self, shell_env):
        # THE safety invariant, under the linked-directory layout: install.sh
        # never DELETES under $HOME. Upgrading from the old copy-based install
        # finds a REAL ~/.codex/skills, and linking the directory moves the whole
        # thing aside -- our stale copies, Codex's managed .system, and any
        # hand-written skill alike. That is the unavoidable cost of linking the
        # dir rather than its entries, so the entire contents must survive in the
        # backup: this installer runs on other people's machines, where a wrong
        # delete is unrecoverable.
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        skills = home / ".codex/skills"
        (skills / ".system").mkdir(parents=True)
        (skills / ".system/SKILL.md").write_text("codex managed\n", encoding="utf-8")
        (skills / "my-own-skill").mkdir(parents=True)
        (skills / "my-own-skill/SKILL.md").write_text(
            "user authored\n", encoding="utf-8"
        )

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr

        # The destination is now our link into the checkout...
        assert skills.is_symlink()
        assert skills.resolve() == (REPO_ROOT / ".codex/skills").resolve()

        # ...and everything that was there is recoverable, byte for byte.
        backups = list(home.glob(".dotfiles_backup_*"))
        assert len(backups) == 1
        saved = backups[0] / ".codex/skills"
        assert (saved / "my-own-skill/SKILL.md").read_text(
            encoding="utf-8"
        ) == "user authored\n"
        assert (saved / ".system/SKILL.md").read_text(
            encoding="utf-8"
        ) == "codex managed\n"

    def test_shared_codex_skills_resolve_to_claude_sources(self, shell_env):
        # The shared set lives in the repo tree (.codex/skills/<name> ->
        # ../../.claude/skills/<name>), not in an install.sh array. Every entry
        # must resolve to a real skill: a dangling link deploys a skill Codex
        # cannot read, and nothing in install.sh would catch that.
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr

        shared = [p for p in (REPO_ROOT / ".codex/skills").iterdir() if p.is_symlink()]
        assert shared, "expected .codex/skills to hold symlinks into .claude/skills"
        for link in shared:
            target = link.resolve()
            assert target.is_dir(), f"{link.name} is dangling: {target}"
            assert target.parent == (REPO_ROOT / ".claude/skills").resolve(), (
                f"{link.name} points outside .claude/skills: {target}"
            )
            assert (target / "SKILL.md").is_file(), f"{link.name} has no SKILL.md"
            # And it is reachable through the deployed link, as Codex sees it.
            assert (home / ".codex/skills" / link.name / "SKILL.md").is_file()

    def test_links_vscode_configs_on_linux(self, shell_env):
        # No OS set (mirrors "no OS var in the ambient test env"): the
        # non-macos / Linux-style ~/.config destinations must be used.
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr

        vscode_user = home / ".config/Code/User"
        for name in ("settings.json", "keybindings.json"):
            code_link = vscode_user / name
            assert code_link.is_symlink(), f"Code {name} should be linked"
            assert (
                code_link.resolve()
                == (REPO_ROOT / ".config/Code/User" / name).resolve()
            )

    def test_links_vscode_configs_on_macos(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        env = dict(shell_env.env)
        env["OS"] = "macos"

        res = run_sourced("create_symlinks", env)
        assert res.returncode == 0, res.stderr

        vscode_user = home / "Library/Application Support/Code/User"
        for name in ("settings.json", "keybindings.json"):
            assert (vscode_user / name).is_symlink()


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


class TestInstallVimPlugins:
    """`vim +PlugInstall +qall || true` used to swallow vim being absent
    (exit 127) the same as a real PlugInstall failure, then printed
    print_success unconditionally either way."""

    def test_missing_vim_warns_and_skips(self, shell_env):
        # macOS ships its own /usr/bin/vim alongside a homebrew one, so a
        # PATH strip can't make `vim` genuinely unresolvable without also
        # taking dirname/touch/sudo (also under /usr/bin) down with it.
        # Shadow `vim` as a function instead (blocks the old code's direct
        # invocation too, so a real vim is never spawned either way) and
        # make command_exists agree it is absent (drives the new guard).
        res = run_sourced(
            "vim() { return 127; }; "
            'command_exists() { [ "$1" = "vim" ] && return 1 '
            '|| command -v "$1" >/dev/null 2>&1; }; '
            'install_vim_plugins; echo "AFTER_VIM_PLUGINS"',
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "AFTER_VIM_PLUGINS" in res.stdout
        assert "[WARNING]" in res.stdout
        assert "Vim plugins installed" not in res.stdout

    def test_plug_install_failure_warns_instead_of_claiming_success(self, shell_env):
        shell_env.stub("vim", exit_code=1)
        res = run_sourced(
            'install_vim_plugins; echo "AFTER_VIM_PLUGINS"', shell_env.env
        )
        assert res.returncode == 0, res.stderr
        assert "AFTER_VIM_PLUGINS" in res.stdout
        assert "[WARNING]" in res.stdout
        assert "Vim plugins installed" not in res.stdout

    def test_successful_plug_install_prints_success(self, shell_env):
        shell_env.stub("vim")
        res = run_sourced(
            'install_vim_plugins; echo "AFTER_VIM_PLUGINS"', shell_env.env
        )
        assert res.returncode == 0, res.stderr
        assert "Vim plugins installed" in res.stdout


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

    def _mark_omz_installed(self, home):
        """Make install_oh_my_zsh consider Oh My Zsh already present.

        It tests for the oh-my-zsh.sh entry point rather than the directory,
        because create_symlinks now runs first and creates
        ~/.oh-my-zsh/custom/themes/ to land the theme -- a directory test would
        skip the install forever. These tests cover the custom/ handling, not
        the download, so short-circuit the install the same way a real machine
        with Oh My Zsh already on it would.
        """
        omz = home / ".oh-my-zsh"
        omz.mkdir(exist_ok=True)
        (omz / "oh-my-zsh.sh").write_text("# stub entry point\n", encoding="utf-8")

    def test_heals_symlinked_custom_dir_before_cloning_plugins(
        self, shell_env, tmp_path
    ):
        # A previous buggy install symlinked $HOME/.oh-my-zsh/custom straight
        # into the dotfiles checkout, which only ships themes/ (no plugins/).
        # install_oh_my_zsh must convert it back to a real directory BEFORE
        # cloning plugins, or the clone lands inside the checkout.
        self._stub_git_clone(shell_env)
        home = shell_env.home
        self._mark_omz_installed(home)
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
        self._mark_omz_installed(home)

        first = run_sourced("install_oh_my_zsh", shell_env.env)
        assert first.returncode == 0, first.stderr
        clone_calls_1 = [c for c in shell_env.calls if c.startswith("git clone")]

        second = run_sourced("install_oh_my_zsh", shell_env.env)
        assert second.returncode == 0, second.stderr
        clone_calls_2 = [c for c in shell_env.calls if c.startswith("git clone")]

        assert clone_calls_2 == clone_calls_1
        assert not (home / ".oh-my-zsh/custom").is_symlink()


# Simulates Oh My Zsh's own official installer: it refuses to run when $ZSH
# (~/.oh-my-zsh) already exists. Written to curl's `-o` target so
# fetch_and_run executes it in place of a real download.
_OMZ_OFFICIAL_INSTALLER_STUB = r"""
out=""; prev=""
for a in "$@"; do
  [ "$prev" = "-o" ] && out="$a"
  prev="$a"
done
if [ -n "$out" ]; then
  cat > "$out" <<'INSTALLER'
if [ -d "$HOME/.oh-my-zsh" ]; then
  echo "Oh My Zsh already installed (stub)" >&2
  exit 1
fi
mkdir -p "$HOME/.oh-my-zsh"
echo "# stub entry point" > "$HOME/.oh-my-zsh/oh-my-zsh.sh"
INSTALLER
fi
"""


class TestOhMyZshFreshInstallOrdering:
    """A true first-ever install must not abort under set -eo pipefail.

    Bug: create_symlinks used to `mkdir -p "$HOME/.oh-my-zsh/custom/themes"`
    to land the theme symlink, which -- on a machine with no prior Oh My Zsh
    -- created $HOME/.oh-my-zsh as a real directory before Oh My Zsh's own
    installer ever ran. The official installer refuses to run when $ZSH
    already exists, so install_oh_my_zsh's unguarded fetch_and_run call
    returned non-zero and set -eo pipefail took the whole script down. The
    theme-linking block is now its own function (link_oh_my_zsh_theme),
    called from main() only after install_oh_my_zsh.
    """

    def _stub_git_clone(self, shell_env):
        body = 'if [ "$1" = "clone" ]; then\n  mkdir -p "${@: -1}"\nfi'
        shell_env.stub("git", body=body)

    def test_fresh_install_succeeds_and_still_links_the_theme(self, shell_env):
        shell_env.stub("curl", body=_OMZ_OFFICIAL_INSTALLER_STUB)
        self._stub_git_clone(shell_env)
        home = shell_env.home
        assert not (home / ".oh-my-zsh").exists()  # genuinely fresh machine

        res = run_sourced(
            "create_symlinks && install_oh_my_zsh && link_oh_my_zsh_theme",
            shell_env.env,
        )
        assert res.returncode == 0, res.stdout + res.stderr

        assert (home / ".oh-my-zsh/oh-my-zsh.sh").is_file()
        theme_link = home / ".oh-my-zsh/custom/themes/px-rose-pine.zsh-theme"
        assert theme_link.is_symlink()
        assert (
            theme_link.resolve()
            == (REPO_ROOT / ".oh-my-zsh/custom/themes/px-rose-pine.zsh-theme").resolve()
        )

    def test_create_symlinks_alone_does_not_create_oh_my_zsh_dir(self, shell_env):
        # The root cause, isolated: create_symlinks must not touch
        # ~/.oh-my-zsh at all on a fresh machine -- that is now entirely
        # link_oh_my_zsh_theme's job, run after install_oh_my_zsh.
        home = shell_env.home
        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert not (home / ".oh-my-zsh").exists()


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


class TestOptionalInstallerFailures:
    """A transient brew/network/apt failure in one optional installer must
    warn and move on -- never abort the whole installer via `set -e`."""

    def test_wezterm_brew_failure_does_not_abort_script_macos(self, shell_env):
        shell_env.stub("brew", exit_code=1)
        res = run_sourced(
            'OS=macos install_wezterm; echo "AFTER_WEZTERM"',
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "AFTER_WEZTERM" in res.stdout
        assert "[WARNING]" in res.stdout

    def test_fonts_brew_failure_does_not_abort_script_macos(self, shell_env):
        shell_env.stub("brew", exit_code=1)
        res = run_sourced(
            'OS=macos install_fonts; echo "AFTER_FONTS"',
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "AFTER_FONTS" in res.stdout
        assert "[WARNING]" in res.stdout

    def test_wezterm_curl_failure_does_not_abort_script(self, shell_env):
        shell_env.stub("curl", exit_code=1)
        shell_env.stub("sudo")
        res = run_sourced(
            "command_exists() { return 1; }; "
            'OS=ubuntu install_wezterm; echo "AFTER_WEZTERM"',
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "AFTER_WEZTERM" in res.stdout
        assert "[WARNING]" in res.stdout

    def test_gh_curl_failure_does_not_abort_script(self, shell_env):
        shell_env.stub("curl", exit_code=1)
        shell_env.stub("sudo")
        res = run_sourced(
            'command_exists() { return 1; }; OS=ubuntu install_gh; echo "AFTER_GH"',
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "AFTER_GH" in res.stdout
        assert "[WARNING]" in res.stdout

    def test_fonts_apt_failure_does_not_abort_script(self, shell_env):
        shell_env.stub("sudo", exit_code=1)
        res = run_sourced(
            'OS=ubuntu install_fonts; echo "AFTER_FONTS"',
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "AFTER_FONTS" in res.stdout
        assert "[WARNING]" in res.stdout

    def test_oh_my_zsh_download_failure_does_not_abort_script(self, shell_env):
        # The Oh My Zsh installer download was the one optional installer whose
        # fetch_and_run was unguarded. Under `set -eo pipefail` a transient
        # network failure there aborted main() outright, so everything after it
        # (vim-plug, tmux plugins, Neovim setup, AI tools, MCP registration, the
        # theme symlink and the shell change) silently never ran.
        shell_env.stub("git")
        res = run_sourced(
            'fetch_and_run() { return 1; }; install_oh_my_zsh; echo "AFTER_OMZ"',
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "AFTER_OMZ" in res.stdout
        assert "[WARNING]" in res.stdout

    def test_oh_my_zsh_download_failure_does_not_claim_success(self, shell_env):
        shell_env.stub("git")
        res = run_sourced(
            "fetch_and_run() { return 1; }; install_oh_my_zsh",
            shell_env.env,
        )
        assert "Oh My Zsh installed" not in res.stdout


class TestAptAliasSymlinks:
    """Debian ships bat/fd as batcat/fdfind, so install_apt_packages drops
    PATH-visible aliases into ~/.local/bin. Those two `ln -sf` calls are the
    only links in the script that never went through create_symlinks' backup
    helper -- and `backup_if_real` is nested inside create_symlinks, so it is
    not even in scope there. A real user binary at that path was destroyed
    with no backup and no warning."""

    def _prepare(self, shell_env):
        shell_env.stub("batcat")
        shell_env.stub("fdfind")
        local_bin = shell_env.home / ".local" / "bin"
        local_bin.mkdir(parents=True, exist_ok=True)
        return local_bin

    def test_real_user_binary_is_never_replaced(self, shell_env):
        local_bin = self._prepare(shell_env)
        mine = local_bin / "fd"
        mine.write_text("#!/bin/sh\necho MINE\n", encoding="utf-8")

        res = run_sourced("install_apt_packages", shell_env.env)

        assert res.returncode == 0, res.stderr
        assert not mine.is_symlink(), "a real user binary must not become a symlink"
        assert "MINE" in mine.read_text(encoding="utf-8")
        assert "[WARNING]" in res.stdout

    def test_alias_is_created_when_nothing_is_in_the_way(self, shell_env):
        local_bin = self._prepare(shell_env)

        res = run_sourced("install_apt_packages", shell_env.env)

        assert res.returncode == 0, res.stderr
        for name in ("bat", "fd"):
            assert (local_bin / name).is_symlink(), f"{name} alias was not created"

    def test_existing_alias_symlink_is_refreshed(self, shell_env):
        # A symlink is ours to replace (same rule backup_if_real applies):
        # a stale target must be re-pointed, not left dangling.
        local_bin = self._prepare(shell_env)
        stale = local_bin / "fd"
        stale.symlink_to("/nonexistent/old-fdfind")

        res = run_sourced("install_apt_packages", shell_env.env)

        assert res.returncode == 0, res.stderr
        assert stale.is_symlink()
        assert stale.resolve().name == "fdfind"


class TestInstallOsPackages:
    """OS-specific package installation, extracted from main()'s inline
    case so the dispatch is unit-testable on its own. A bash `case` with no
    matching arm is a silent no-op: a non-Debian Linux (OS="linux", set by
    detect_os when /etc/debian_version is absent) used to fall through with
    no warning and no packages installed."""

    def test_non_debian_linux_warns_instead_of_silently_skipping(self, shell_env):
        res = run_sourced(
            'OS=linux install_os_packages; echo "AFTER_OS_PACKAGES"', shell_env.env
        )
        assert res.returncode == 0, res.stderr
        assert "AFTER_OS_PACKAGES" in res.stdout
        assert "[WARNING]" in res.stdout

    def test_ubuntu_still_dispatches_to_apt(self, shell_env):
        res = run_sourced(
            "install_apt_packages() { echo CALLED_APT; }; "
            "OS=ubuntu install_os_packages",
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "CALLED_APT" in res.stdout

    def test_macos_still_dispatches_to_homebrew(self, shell_env):
        res = run_sourced(
            "install_homebrew() { echo CALLED_HOMEBREW; }; "
            "install_brew_packages() { echo CALLED_BREW_PACKAGES; }; "
            "OS=macos install_os_packages",
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "CALLED_HOMEBREW" in res.stdout
        assert "CALLED_BREW_PACKAGES" in res.stdout

    def test_windows_still_warns(self, shell_env):
        res = run_sourced("OS=windows install_os_packages", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "[WARNING]" in res.stdout


class TestInstallNodejs:
    """npm can legitimately be absent by the time the prefix is configured:
    the installs above are best-effort (brew/NodeSource failures only warn)
    and the windows branch never installs node at all. A bare `npm config
    set` then exits 127 and `set -e` takes the whole run down with it,
    skipping every step after install_nodejs."""

    @staticmethod
    def _env_without_node(shell_env) -> dict:
        """shell_env's PATH keeps the real one appended, so a host npm would
        satisfy `npm config set` and hide the very abort under test. Drop
        every directory that provides node or npm instead of stubbing them:
        absence is the condition, and a stub would only prove it exists."""
        env = dict(shell_env.env)
        while True:
            found = shutil.which("npm", path=env["PATH"]) or shutil.which(
                "node", path=env["PATH"]
            )
            if not found:
                return env
            drop = str(Path(found).parent)
            env["PATH"] = ":".join(p for p in env["PATH"].split(":") if p != drop)

    def test_missing_npm_does_not_abort_script(self, shell_env):
        # windows never installs node, so this is the ordinary path there.
        env = self._env_without_node(shell_env)
        res = run_sourced('OS=windows install_nodejs; echo "AFTER_NODEJS"', env)
        assert res.returncode == 0, res.stderr
        assert "AFTER_NODEJS" in res.stdout
        assert "[WARNING]" in res.stdout

    def test_missing_npm_after_failed_install_does_not_abort_script(self, shell_env):
        shell_env.stub("brew", exit_code=1)
        env = self._env_without_node(shell_env)
        res = run_sourced('OS=macos install_nodejs; echo "AFTER_NODEJS"', env)
        assert res.returncode == 0, res.stderr
        assert "AFTER_NODEJS" in res.stdout
        assert "[WARNING]" in res.stdout

    def test_configures_prefix_when_npm_present(self, shell_env):
        # The guard must not cost the happy path its prefix.
        shell_env.stub("node")
        shell_env.stub("npm")
        res = run_sourced('OS=macos install_nodejs; echo "AFTER_NODEJS"', shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "AFTER_NODEJS" in res.stdout
        expected = f"npm config set prefix {shell_env.home}/.npm-global"
        assert expected in shell_env.calls
        assert (shell_env.home / ".npm-global").is_dir()


# go install places binaries under $HOME/go/bin (fixture $HOME, not this
# dev machine's real one). Given "install <pkg>@version", write a fake
# executable named after the package's last path segment, mirroring what a
# real `go install .../cmd/<tool>@latest` produces.
_GO_INSTALL_STUB = r"""
if [ "$1" = "install" ]; then
  pkg="${2%@*}"
  tool="${pkg##*/}"
  mkdir -p "$HOME/go/bin"
  touch "$HOME/go/bin/$tool"
  chmod +x "$HOME/go/bin/$tool"
fi
"""


class TestGoInstallPathExport:
    """install_nodejs/install_uv export PATH right after their own install
    so the immediately-following command_exists check sees what was just
    installed. install_glow and install_linters_formatters's Ubuntu
    branches did not: `go install` places binaries under ~/go/bin, which
    is not on PATH until exported, so the following command_exists check
    (and anything later in the same run) falsely reports the tool missing."""

    def test_glow_ubuntu_go_install_path_is_exported(self, shell_env):
        # This dev machine has a real glow on PATH; it must not mask the
        # fixture's fresh "not installed yet" state.
        env = _without_commands(shell_env.env, "glow")
        shell_env.stub("go", body=_GO_INSTALL_STUB)
        res = run_sourced('OS=ubuntu install_glow; echo "AFTER_GLOW"', env)
        assert res.returncode == 0, res.stderr
        assert "AFTER_GLOW" in res.stdout
        assert "glow installed" in res.stdout

    def test_linters_formatters_ubuntu_go_installed_tools_visible_afterward(
        self, shell_env
    ):
        # Neutralize real ambient tools this dev machine happens to have so
        # the Ubuntu branch actually exercises its go-install paths instead
        # of finding them "already installed". gem and pip3 are left to a
        # command_exists override rather than a PATH strip: both also have
        # a copy under /usr/bin (Apple's system Python/Ruby), which
        # _without_commands refuses to remove since it also carries
        # touch/sudo/curl/dirname that the rest of this test still needs.
        env = _without_commands(
            shell_env.env, "staticcheck", "goimports", "npm", "pip", "php"
        )
        shell_env.stub("go", body=_GO_INSTALL_STUB)
        shell_env.stub("sudo")
        res = run_sourced(
            'command_exists() { case "$1" in gem|pip3) return 1 ;; '
            '*) command -v "$1" >/dev/null 2>&1 ;; esac; }; '
            "OS=ubuntu install_linters_formatters; "
            "command_exists staticcheck && echo STATICCHECK_ON_PATH; "
            "command_exists goimports && echo GOIMPORTS_ON_PATH",
            env,
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert "STATICCHECK_ON_PATH" in res.stdout, res.stdout
        assert "GOIMPORTS_ON_PATH" in res.stdout, res.stdout


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

    # macOS ships /bin/zsh (Apple's default-shell zsh) alongside a homebrew
    # one, so a PATH strip can't make `zsh` genuinely unresolvable without
    # also taking /bin -- and therefore bash itself -- off PATH. Shadow the
    # two lookup mechanisms `change_shell` actually uses instead: `which`
    # (the old code's `$(which zsh)`) and `command_exists` (the new guard).
    _ZSH_ABSENT = (
        'which() { [ "$1" = "zsh" ] && return 1 || command which "$@"; }; '
        'command_exists() { [ "$1" = "zsh" ] && return 1 '
        '|| command -v "$1" >/dev/null 2>&1; }; '
    )

    def test_missing_zsh_warns_and_never_invokes_chsh(self, shell_env):
        # Without a `command_exists zsh` guard, `$(which zsh)` resolves to
        # "" when zsh isn't installed, and `[ "$SHELL" != "" ]` is true, so
        # the old code proceeded straight to `chsh -s ""`.
        shell_env.stub("chsh")
        env = dict(shell_env.env)
        env["SHELL"] = "/bin/bash"

        res = run_sourced(
            self._ZSH_ABSENT + 'change_shell; echo "AFTER_CHANGE_SHELL"', env
        )

        assert res.returncode == 0, res.stderr
        assert "AFTER_CHANGE_SHELL" in res.stdout
        assert "[WARNING]" in res.stdout
        assert not any(c.startswith("chsh") for c in shell_env.calls)

    def test_missing_zsh_warns_in_dry_run_too(self, shell_env):
        env = {**shell_env.env, "DRY_RUN": "1", "SHELL": "/bin/bash"}

        res = run_sourced(
            self._ZSH_ABSENT + 'change_shell; echo "AFTER_CHANGE_SHELL"', env
        )

        assert res.returncode == 0, res.stderr
        assert "[WARNING]" in res.stdout


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


class TestHooksJsonDryRunDiff:
    """The DRY_RUN branch used to unconditionally print "would render",
    regardless of whether the rendered output actually differs from what is
    already on disk -- unlike the real branch, which already does the
    cmp -s check to skip a no-op re-render."""

    @staticmethod
    def _hooks_json_lines(stdout: str) -> list:
        return [ln for ln in stdout.splitlines() if "hooks.json" in ln]

    def test_dry_run_reports_unchanged_when_content_matches(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()

        first = run_sourced("create_symlinks", shell_env.env)
        assert first.returncode == 0, first.stderr

        env = {**shell_env.env, "DRY_RUN": "1"}
        second = run_sourced("create_symlinks", env)
        assert second.returncode == 0, second.stderr

        lines = self._hooks_json_lines(second.stdout)
        assert any("unchanged" in ln for ln in lines), second.stdout
        assert not any("would render" in ln for ln in lines), second.stdout

    def test_dry_run_reports_would_render_when_content_differs(self, shell_env):
        home = shell_env.home
        (home / ".codex").mkdir(parents=True)
        (home / ".codex/hooks.json").write_text("{}\n", encoding="utf-8")

        env = {**shell_env.env, "DRY_RUN": "1"}
        res = run_sourced("create_symlinks", env)
        assert res.returncode == 0, res.stderr

        lines = self._hooks_json_lines(res.stdout)
        assert any("would render" in ln for ln in lines), res.stdout
        assert not any("unchanged" in ln for ln in lines), res.stdout

    def test_dry_run_touches_nothing_either_way(self, shell_env):
        # The diff check itself must stay read-only: no write to HOME.
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        env = {**shell_env.env, "DRY_RUN": "1"}

        res = run_sourced("create_symlinks", env)
        assert res.returncode == 0, res.stderr
        assert not (home / ".codex/hooks.json").exists()


# A curl stub that "downloads" a script by writing it to curl's `-o` target.
# The written script echoes its own path ($0) and positional args ($*) so the
# tests can assert both that it ran and how fetch_and_run invoked it.
_CURL_WRITES_SCRIPT = r"""
out=""; prev=""
for a in "$@"; do
  [ "$prev" = "-o" ] && out="$a"
  prev="$a"
done
[ -n "$out" ] && printf '%s\n' 'echo "RAN tmp=$0 args=[$*]"' > "$out"
"""


class TestUsageAndArgs:
    """`--help` / `--dry-run` / bad-flag handling in main()'s arg parser.

    Parsing runs before the checkout guard so --help works anywhere, and
    before the first side effect so nothing is touched on the error paths.
    """

    def test_help_prints_usage_and_does_nothing(self, shell_env):
        res = run_sourced("main --help", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "Usage:" in res.stdout
        assert "--dry-run" in res.stdout
        # --help returns before any installation step.
        assert "Creating symbolic links" not in res.stdout
        assert list(shell_env.home.iterdir()) == []

    def test_short_help_flag(self, shell_env):
        res = run_sourced("main -h", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "Usage:" in res.stdout

    def test_unknown_option_errors_with_exit_2(self, shell_env):
        # `set -e` (from the sourced script) turns main's `return 2` into the
        # shell's exit status directly.
        res = run_sourced("main --bogus", shell_env.env)
        assert res.returncode == 2, res.stdout + res.stderr
        assert "Unknown option: --bogus" in res.stdout
        assert list(shell_env.home.iterdir()) == []

    def test_unexpected_positional_errors_with_exit_2(self, shell_env):
        res = run_sourced("main extra-arg", shell_env.env)
        assert res.returncode == 2, res.stdout + res.stderr
        assert "Unexpected argument: extra-arg" in res.stdout


class TestDryRun:
    """--dry-run must preview the destructive/user-specific work (symlinks,
    backups, rendered configs, chsh) while touching NOTHING on disk."""

    def test_create_symlinks_leaves_fresh_home_empty(self, shell_env):
        # The discriminating check: any unguarded mkdir/ln/cp/render/backup
        # would leave a trace here. HOME must be byte-for-byte untouched.
        home = shell_env.home
        env = {**shell_env.env, "DRY_RUN": "1"}
        res = run_sourced("create_symlinks", env)
        assert res.returncode == 0, res.stderr
        assert list(home.iterdir()) == [], list(home.iterdir())
        # ...and it announced the plan.
        assert "[DRY-RUN]" in res.stdout
        assert "would link" in res.stdout

    def test_create_symlinks_does_not_disturb_existing_entries(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        (home / ".zshrc").write_text("keep me\n", encoding="utf-8")
        before = sorted(p.name for p in home.iterdir())

        env = {**shell_env.env, "DRY_RUN": "1"}
        res = run_sourced("create_symlinks", env)
        assert res.returncode == 0, res.stderr

        assert sorted(p.name for p in home.iterdir()) == before
        # The real .zshrc is neither backed up nor replaced with a symlink.
        assert not (home / ".zshrc").is_symlink()
        assert (home / ".zshrc").read_text(encoding="utf-8") == "keep me\n"
        assert list(home.glob(".dotfiles_backup_*")) == []

    def test_main_dry_run_leaves_home_untouched(self, shell_env):
        # Full flow: detect OS, plan symlinks, skip package/tool installs,
        # plan shell change -- all without writing to HOME.
        home = shell_env.home
        res = run_sourced("main --dry-run", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert list(home.iterdir()) == [], list(home.iterdir())
        assert "Skipping package and tool installation" in res.stdout
        assert "Dry-run complete" in res.stdout

    def test_unstubbed_package_managers_hit_the_backstop(self, shell_env):
        # Regression: a mid-development dry-run gate once let install.sh reach
        # the REAL host `brew` with HOME inside the pytest tmp dir — Homebrew
        # "upgraded" the font cask by relocating the user's real font files
        # into the doomed tmp HOME. The shell_env backstop stubs must
        # intercept system-mutating tools even when a test forgets to stub
        # them. (No RED phase for this one: observing the failure means
        # executing the real package manager and mutating the host.)
        res = run_sourced("OS=macos install_fonts", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert any(c.startswith("brew install --cask") for c in shell_env.calls), (
            "install_fonts must hit the brew backstop stub, never the host brew"
        )

    def test_change_shell_does_not_invoke_chsh(self, shell_env):
        shell_env.stub("zsh")
        shell_env.stub("chsh")
        env = {**shell_env.env, "DRY_RUN": "1", "SHELL": "/bin/bash"}
        res = run_sourced("change_shell", env)
        assert res.returncode == 0, res.stderr
        assert "[DRY-RUN]" in res.stdout
        assert not any(c.startswith("chsh") for c in shell_env.calls)

    def test_dry_run_is_not_the_default(self, shell_env):
        # A plain create_symlinks (no DRY_RUN) still performs the work: the
        # guards must gate on DRY_RUN=1, never fire by default.
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert (home / ".zshrc").is_symlink()


class TestFetchAndRun:
    """fetch_and_run downloads a remote installer in full before running it, so
    a truncated/empty download can't execute a partial script."""

    def test_runs_downloaded_script_and_cleans_up(self, shell_env):
        shell_env.stub("curl", body=_CURL_WRITES_SCRIPT)
        res = run_sourced("fetch_and_run https://example.test/x.sh bash", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "RAN tmp=" in res.stdout
        # With no `--`, the script receives no positional args.
        assert "args=[]" in res.stdout
        # The temp file must be removed after the run (no accumulation).
        m = re.search(r"RAN tmp=(\S+) args=", res.stdout)
        assert m, res.stdout
        assert not Path(m.group(1)).exists()

    def test_passes_positional_args_after_separator(self, shell_env):
        # `-- --unattended` must reach the SCRIPT as $1 (Oh My Zsh's flag),
        # not be consumed as an option to the interpreter.
        shell_env.stub("curl", body=_CURL_WRITES_SCRIPT)
        res = run_sourced(
            "fetch_and_run https://example.test/x.sh bash -- --unattended",
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "args=[--unattended]" in res.stdout

    def test_interpreter_flags_before_separator_stay_interpreter_side(self, shell_env):
        # Args before `--` are the interpreter's own flags and must land BEFORE
        # the script path (this is how `sudo -E bash` callers work). `sh -x <tmp>`
        # runs the script under xtrace -- an sh option -- so the script still runs
        # (marker on stdout) AND the trace on stderr proves `-x` was applied to
        # sh rather than passed to the script.
        shell_env.stub("curl", body=_CURL_WRITES_SCRIPT)
        res = run_sourced(
            "fetch_and_run https://example.test/x.sh sh -x",
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "RAN tmp=" in res.stdout  # script ran
        assert "+ echo" in res.stderr  # xtrace => -x reached sh, not the script

    def test_rejects_empty_download(self, shell_env):
        # A curl that succeeds but writes nothing (empty 200) must be refused,
        # never handed to a (possibly root) shell.
        shell_env.stub("curl")  # default body leaves the -o target empty
        res = run_sourced(
            "fetch_and_run https://example.test/x.sh bash "
            '&& echo OK || echo "FAILED rc=$?"',
            shell_env.env,
        )
        assert "Downloaded empty script" in res.stdout
        assert "FAILED rc=1" in res.stdout
        assert "RAN tmp=" not in res.stdout

    def test_download_failure_returns_nonzero(self, shell_env):
        shell_env.stub("curl", exit_code=1)
        res = run_sourced(
            "fetch_and_run https://example.test/x.sh bash "
            '&& echo OK || echo "FAILED rc=$?"',
            shell_env.env,
        )
        assert "Failed to download" in res.stdout
        assert "FAILED rc=1" in res.stdout
