"""Config wiring: settings / hook JSON must reference files that actually exist
in the repo, and must parse as valid JSON / TOML.

dotfiles' core job is wiring: a renamed hook, a path typo, or an invalid JSON /
TOML edit would silently break a real install while every other test stayed
green (the hooks themselves are tested in isolation, not through the config
that launches them). These tests close that gap.
"""

import json
import re
import sys

import tomllib
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
import _bash_review_common as _bash_review  # noqa: E402

CLAUDE_SETTINGS = REPO_ROOT / ".claude/settings.json"
CODEX_HOOKS_TEMPLATE = REPO_ROOT / ".codex/hooks.json.template"
# Not `.codex/config.toml`: Codex writes mcp_servers (auth headers included),
# projects and plugin state into the live file, so the repo ships a baseline to
# seed from rather than a symlink target. See create_symlinks in install.sh.
CODEX_CONFIG = REPO_ROOT / ".codex/config.toml.template"
GEMINI_SETTINGS = REPO_ROOT / ".gemini/settings.json"


def _referenced_repo_paths(text: str, home_marker: str) -> list[str]:
    """Extract `<home>/.claude/...` / `<home>/.codex/...` script paths from hook
    command strings and return them repo-relative (e.g. `.claude/hooks/x.sh`).

    `home_marker` is `~` for the installed settings.json and `__HOME__` for the
    codex template. Paths are terminated by whitespace or a surrounding quote.
    """
    pattern = re.escape(home_marker) + r"/(\.(?:claude|codex)/[^\s'\"]+)"
    return [m.group(1) for m in re.finditer(pattern, text)]


def test_claude_settings_is_valid_json():
    json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))


def test_claude_settings_hook_and_statusline_paths_exist():
    text = CLAUDE_SETTINGS.read_text(encoding="utf-8")
    rels = _referenced_repo_paths(text, "~")
    # Guard against the regex silently matching nothing (which would make the
    # existence loop vacuously pass).
    assert rels, "expected at least one ~/.claude/... reference in settings.json"
    for rel in rels:
        assert (REPO_ROOT / rel).is_file(), (
            f"settings.json references missing file: {rel}"
        )


# Paths whose *contents* are secrets (or, for .git/config, are what turns a
# "safe" git read into arbitrary code execution -- see the SAFE_COMMANDS comment
# in _bash_review_common.py). The hook README calls permissions.deny "the hard
# boundary" that bash-review only advises on top of; that claim only holds if
# the boundary actually covers writes. It used to deny reads of all of these
# while denying exactly one write pattern, so a write path was guarded by
# bash-review alone -- and bash-review had a hole there (git --output).
# Read-denied build artifacts (node_modules, dist, build) are deliberately NOT
# listed: those denies exist to cut noise, and writing them is legitimate.
#
# The verb is `Edit`, never `Write`. Claude Code evaluates file permission rules
# under `Edit(path)` only, and an `Edit` rule covers *every* file-editing tool
# (Write included). A `Write(path)` deny is not consulted at all: it parses fine
# and reads as protection while enforcing nothing, and the CLI prints a startup
# warning for each one. Adding the `Write` twin was tried and reverted -- keep
# these Edit-only so the list cannot drift back into inert entries.
SECRET_PATH_PATTERNS = [
    "**/id_rsa*",
    "**/id_ed25519*",
    "**/id_ecdsa*",
    "**/*.key",
    "**/*.pem",
    "**/*.token",
    "**/.ssh/**",
    "**/.aws/**",
    "**/secrets/**",
    "**/.git/config",
]


def _deny_rules() -> set[str]:
    return set(
        json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))["permissions"]["deny"]
    )


def test_secret_paths_are_denied_for_editing():
    """Every secret-content path must be denied for editing, not just reading.

    Regression guard for the asymmetry described above.
    """
    deny = _deny_rules()
    missing = [
        f"Edit({pattern})"
        for pattern in SECRET_PATH_PATTERNS
        if f"Edit({pattern})" not in deny
    ]
    assert not missing, f"permissions.deny is missing edit-side guards: {missing}"


def test_dotenv_is_denied_for_editing():
    """`.env` uses its own spellings (no `**/` prefix) in the existing Read denies,
    so it is checked separately rather than bent into SECRET_PATH_PATTERNS."""
    deny = _deny_rules()
    missing = [
        f"Edit({pattern})"
        for pattern in (".env", ".env.*")
        if f"Edit({pattern})" not in deny
    ]
    assert not missing, f"permissions.deny is missing .env edit guards: {missing}"


# bash-review's DENY_EXECUTABLES (sudo, ssh, dd, ...) are *context-free* hard
# denies, but that layer is only advisory: it needs bash-review-launcher.sh to
# actually run. The launcher's own uncovered residual case -- it failing to start
# (missing bash / python3) -- is bounded by permissions.deny alone (see
# hooks/README.md, "Fail toward the human"). So every context-free hard-deny
# executable must have a Bash(<exe>:*) twin here, or that "bounded by
# permissions.deny" claim is false for it and the executable would run unreviewed
# whenever the launcher is down. These entries are already hard-denied by the
# hook, so the deny twin adds no new friction -- only the missing backstop.
def test_deny_executables_have_permission_deny_backstop():
    """Regression guard for the hook/settings asymmetry: DENY_EXECUTABLES used to
    list su/doas/pkexec/dd/shred/ssh with no permissions.deny backstop, so those
    had no hard boundary when the launcher could not start. Keep the two in sync;
    a new DENY_EXECUTABLES entry must gain a deny twin in the same change."""
    deny = _deny_rules()
    missing = [
        f"Bash({exe}:*)"
        for exe in sorted(_bash_review.DENY_EXECUTABLES)
        if f"Bash({exe}:*)" not in deny
    ]
    assert not missing, (
        "permissions.deny is missing hard-boundary backstops for hook "
        f"DENY_EXECUTABLES entries: {missing}"
    )


# `mkfs` is special-cased in the hook (_is_deny_command): it denies bare `mkfs`
# *and* the `mkfs.<fs>` family (mkfs.ext4, mkfs.xfs, ...) via a `startswith`
# prefix match. A permissions.deny rule cannot express that family in one entry:
# Bash(mkfs:*) enforces a word boundary after `mkfs`, so it matches `mkfs
# /dev/sda` but NOT `mkfs.ext4 /dev/sda`. The family is therefore backstopped
# best-effort by enumerating the common members; exotic filesystems stay covered
# by the hook alone. This list is a floor of realistic cases, not a completeness
# claim -- see the launcher-residual note under "Fail toward the human" in
# hooks/README.md.
MKFS_DENY_BACKSTOP = [
    "mkfs",
    "mkfs.ext2",
    "mkfs.ext3",
    "mkfs.ext4",
    "mkfs.xfs",
    "mkfs.btrfs",
    "mkfs.vfat",
]


def test_mkfs_family_has_permission_deny_backstop():
    deny = _deny_rules()
    missing = [
        f"Bash({name}:*)"
        for name in MKFS_DENY_BACKSTOP
        if f"Bash({name}:*)" not in deny
    ]
    assert not missing, f"permissions.deny is missing mkfs backstops: {missing}"


def test_no_write_verb_deny_rules():
    """`Write(path)` deny rules are never consulted (see the note above), so they
    are protection-shaped noise and make the CLI warn at startup. Fail if one
    reappears."""
    inert = sorted(rule for rule in _deny_rules() if rule.startswith("Write("))
    assert not inert, (
        f"permissions.deny has inert Write() rules; use Edit(...) instead: {inert}"
    )


def _pretooluse_bash_commands() -> list[str]:
    settings = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    commands = []
    for entry in settings["hooks"]["PreToolUse"]:
        if entry.get("matcher") == "Bash":
            commands += [hook["command"] for hook in entry["hooks"]]
    return commands


def test_bash_review_is_wired_through_failclosed_launcher():
    """A bare `python3 .../bash-review.py` hook command fails OPEN when the
    review cannot happen at all: Claude Code treats a hook that cannot start
    (python3 missing) or that crashes as a non-blocking error and runs the
    Bash command anyway. settings.json must launch the review through
    bash-review-launcher.sh, which turns those into an explicit `ask` -- guard
    the wiring so it cannot silently revert to the fail-open form."""
    commands = _pretooluse_bash_commands()
    assert any("bash-review-launcher.sh" in c for c in commands), (
        "PreToolUse Bash hooks must launch bash-review via the launcher"
    )
    direct = [c for c in commands if "bash-review.py" in c]
    assert not direct, f"bash-review.py must not be invoked directly: {direct}"


def test_codex_bash_review_is_wired_through_failclosed_launcher():
    """Same startup fail-open gap as the Claude side, same guard: the template
    must launch bash-review through the .codex launcher variant (which reports
    failure as exit 2 + stderr, since Codex has no `ask` vocabulary and parses
    hook stdout as structured output)."""
    text = CODEX_HOOKS_TEMPLATE.read_text(encoding="utf-8")
    data = json.loads(text.replace("__HOME__", "/home/tester"))
    commands = [
        hook["command"]
        for entry in data["hooks"]["PreToolUse"]
        if entry.get("matcher") == "Bash"
        for hook in entry["hooks"]
    ]
    assert any("bash-review-launcher.sh" in c for c in commands), (
        "codex PreToolUse Bash hooks must launch bash-review via the launcher"
    )
    direct = [c for c in commands if "bash-review.py" in c]
    assert not direct, f"bash-review.py must not be invoked directly: {direct}"


def test_codex_hooks_template_renders_valid_json_and_paths_exist():
    text = CODEX_HOOKS_TEMPLATE.read_text(encoding="utf-8")
    # install.sh renders the template by substituting __HOME__; the result must
    # be valid JSON (Codex parses it verbatim, without expanding ~ or $HOME).
    json.loads(text.replace("__HOME__", "/home/tester"))
    rels = _referenced_repo_paths(text, "__HOME__")
    assert rels, "expected at least one __HOME__/.codex/... reference in the template"
    for rel in rels:
        assert (REPO_ROOT / rel).is_file(), (
            f"hooks template references missing file: {rel}"
        )


def test_codex_config_is_valid_toml():
    tomllib.loads(CODEX_CONFIG.read_text(encoding="utf-8"))


def test_gemini_settings_is_valid_json():
    json.loads(GEMINI_SETTINGS.read_text(encoding="utf-8"))
