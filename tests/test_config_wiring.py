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


def _pretooluse_bash_hooks() -> list[dict]:
    settings = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    hooks = []
    for entry in settings["hooks"]["PreToolUse"]:
        if entry.get("matcher") == "Bash":
            hooks += entry["hooks"]
    return hooks


def _pretooluse_bash_commands() -> list[str]:
    return [hook["command"] for hook in _pretooluse_bash_hooks()]


def test_pretooluse_bash_hooks_are_unconditional():
    """A safety gate must not be narrowed by an `if` filter.

    `if` prefix-matches the command string per sub-command, so a gate written
    as `Bash(git push*)` never fires for the very forms its script exists to
    catch -- `git -C <dir> push`, `git --no-pager push`, `eval "git push ..."`.
    git-push-review.sh detects all of those (see its executes_string_arg /
    git_c_re regexes) and DETECTION_CASES in tests/test_git_push_review.py
    pins that detection, but the script was unreachable for those cases in
    production because the filter ran first. Worse, `permissions.allow` holds
    `Bash(git:*)` with defaultMode `auto`, and at the time there was no `ask`
    list at all, so the hook was the only confirmation left before a push.
    (`Bash(git push:*)` now backstops it -- see
    test_git_push_asks_at_the_permission_layer_too -- but that rule
    prefix-matches too, so it does not cover the forms this test protects.)

    Claude Code's own docs say so directly:

        "Because the filter is best-effort, use the permission system rather
        than a hook to enforce a hard allow or deny."

    Paying for that correctness is cheap: git-push-review.sh short-circuits
    before strip_quoted_ranges (its O(n^2) quote-stripping state machine) on a
    grep of the jq-decoded command, so a non-push command costs a flat ~70ms
    regardless of command length. The .codex side has always wired this hook
    unconditionally.
    """
    hooks = _pretooluse_bash_hooks()
    # Anti-vacuity: without this, deleting the hook entry (or renaming the
    # matcher) empties the list and the assertion below passes while the gate
    # this test exists to protect is gone. Same convention as the deny-rule
    # helpers above.
    assert hooks, "expected PreToolUse hooks under matcher 'Bash'"
    assert any("git-push-review.sh" in h["command"] for h in hooks), (
        "the git push confirmation gate must stay wired under PreToolUse/Bash"
    )
    conditional = [hook["command"] for hook in hooks if "if" in hook]
    assert not conditional, (
        "PreToolUse Bash hooks must run unconditionally; `if` is best-effort "
        f"and silently narrows the gate: {conditional}"
    )


def test_git_push_asks_at_the_permission_layer_too():
    """The push gate must not be hook-only, because the hook fails OPEN.

    git-push-review.sh is deliberately fail-open everywhere (`|| exit 0`,
    `2>/dev/null`) so a broken summary never blocks a real command, and Claude
    Code treats a hook that cannot start, crashes, or times out as a
    non-blocking error. With `Bash(git:*)` in allow, defaultMode `auto` and the
    hook as the only gate, every one of those failure modes let a push through
    with no confirmation at all.

    `Bash(git push:*)` in `ask` is the backstop for that: it is enforced by the
    permission system rather than by a script that can die. It is NOT a
    replacement for the unconditional hook wiring -- `ask` prefix-matches the
    same way `if` did, so it does not fire for `git -C <dir> push` or
    `eval "git push ..."`. The two layers cover different failure modes: the
    hook covers every FORM while it is healthy, this rule covers the common
    form even when it is not.
    """
    settings = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    ask = settings["permissions"].get("ask", [])
    assert "Bash(git push:*)" in ask, (
        "permissions.ask must keep a git push entry so the gate survives a "
        f"hook failure; current ask rules: {ask}"
    )


def test_codex_pretooluse_bash_hooks_are_unconditional():
    """Parity guard for the same hole on the Codex side (it has no `if` today,
    and nothing stopped one from being added)."""
    text = CODEX_HOOKS_TEMPLATE.read_text(encoding="utf-8")
    data = json.loads(text.replace("__HOME__", "/home/tester"))
    hooks = [
        hook
        for entry in data["hooks"]["PreToolUse"]
        if entry.get("matcher") == "Bash"
        for hook in entry["hooks"]
    ]
    assert hooks, "expected codex PreToolUse hooks under matcher 'Bash'"
    conditional = [hook["command"] for hook in hooks if "if" in hook]
    assert not conditional, (
        f"codex PreToolUse Bash hooks must run unconditionally: {conditional}"
    )


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


def _load_jsonc(path):
    """Parse a VS Code JSON-with-comments file (// and /* */ outside strings)."""
    text = path.read_text(encoding="utf-8")
    out = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(ch)
        i += 1
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    return json.loads(cleaned)


VSCODE_USER = REPO_ROOT / ".config/Code/User"


def test_vscode_vim_keybindings_spell_the_leader_token():
    """VSCodeVim only recognises `<leader>`; a bare `leader` is three letters.

    The EasyMotion remaps wrote `"after": ["leader", "leader", ...]`, so the
    `<leader><leader>` prefix EasyMotion listens for was never produced and
    `,jj` / `,js` / `,jc` typed the letters instead. Every `before` in the same
    file already spells `<leader>`.
    """
    settings = _load_jsonc(VSCODE_USER / "settings.json")
    offenders = []
    for key, value in settings.items():
        if not (key.startswith("vim.") and isinstance(value, list)):
            continue
        for binding in value:
            if not isinstance(binding, dict):
                continue  # e.g. a plain list of key names
            for side in ("before", "after"):
                for tok in binding.get(side, []):
                    if tok.lower() == "leader":
                        offenders.append((key, side, binding.get("before")))
    assert offenders == [], f"bare `leader` tokens: {offenders}"


def test_vscode_git_stage_keys_are_distinct():
    """`ctrl+g s` and `ctrl+g shift+s` must not run the same command.

    Both named git.stageAll, so the key whose comment reads "stage changes"
    staged everything -- indistinguishable from its shifted sibling.
    """
    bindings = _load_jsonc(VSCODE_USER / "keybindings.json")
    by_key = {b["key"]: b["command"] for b in bindings if "git.stage" in b["command"]}
    assert by_key.get("ctrl+g shift+s") == "git.stageAll"
    assert by_key.get("ctrl+g s") == "git.stage"


def test_tmux_double_click_selects_the_word_under_the_mouse():
    """copy-mode needs `-M` to start at the mouse, and -M only works in a binding.

    The old `run-shell` branch ran `tmux copy-mode -t <pane>` with no -M, so the
    copy cursor started at the TERMINAL cursor and `select-word` picked the last
    word of the pane whatever was double-clicked; its `send-keys -X` also had
    no -t and went to the active pane. -M is rejected inside run-shell (no mouse
    event there), so the copy branch has to live in the binding itself.
    """
    conf = (REPO_ROOT / ".tmux.conf").read_text(encoding="utf-8")
    start = conf.index("DoubleClick1Pane")
    end = conf.index("\n\n", start)
    binding = conf[start:end]
    assert "copy-mode -M" in binding, binding
    assert "run-shell" not in binding, "the copy branch must not go through run-shell"
    # tmux's bare `<=` is a STRING comparison ("20" <= "5" is true); only the
    # `e|` form is numeric. A double-click 10-49 columns from the edge split
    # the pane instead of selecting the word until this read `#{e|<=:...}`.
    assert binding.count("#{e|<=:") == 2, binding
    assert "#{<=:" not in binding, "edge distance compared as a string"


# --------------------------------------------------------------------------
# .luacheckrc vs .luarc.json: the same set of known Neovim globals
# --------------------------------------------------------------------------
# .luacheckrc's header says its global facts "mirror .luarc.json (LuaJIT +
# `vim`) so the lint and the language server agree on what is defined", and
# ci.yml's luacheck job repeats the claim. They did not agree: luacheck knew
# `R` (a plenary reload helper defined in lua/setup/init.lua) and `Snacks`
# (injected by snacks.nvim), .luarc.json knew only `vim`. CI stayed green
# because luacheck is the permissive side, so nothing surfaced -- while
# lua_ls flagged both as undefined globals in the editor, every day.
#
# The two files spell the same idea differently: luacheck splits `globals`
# (read+write) from `read_globals` (read-only), lua_ls has one flat
# `diagnostics.globals`. So parity means the UNION, not a field-by-field
# match; a test comparing only `globals` would pass while `Snacks` stayed
# broken.
LUACHECKRC = REPO_ROOT / ".config/nvim/.luacheckrc"
LUARC = REPO_ROOT / ".config/nvim/.luarc.json"

# `globals = { "vim", "R" }` / `read_globals = { "Snacks" }`
_LUACHECK_GLOBALS_RE = re.compile(
    r"^\s*(?:globals|read_globals)\s*=\s*\{([^}]*)\}", re.M
)


def _luacheck_known_globals() -> set[str]:
    found: set[str] = set()
    text = LUACHECKRC.read_text(encoding="utf-8")
    for match in _LUACHECK_GLOBALS_RE.finditer(text):
        found.update(re.findall(r'"([^"]+)"', match.group(1)))
    return found


def test_luacheck_and_lua_ls_agree_on_known_globals():
    """Both checkers must know the same globals, or one lies to the reader."""
    luacheck = _luacheck_known_globals()
    assert luacheck, f"no globals parsed out of {LUACHECKRC}"
    lua_ls = set(json.loads(LUARC.read_text(encoding="utf-8"))["diagnostics.globals"])
    assert luacheck == lua_ls, (
        f"luacheck knows {sorted(luacheck)}, lua_ls knows {sorted(lua_ls)}; "
        "these two must declare the same globals -- fix whichever side is "
        "wrong, and update the prose the test below pins"
    )


# The drift that started this was not in the data but in the PROSE about it:
# .luacheckrc's header and ci.yml both said the two files agreed on
# "LuaJIT + vim" long after luacheck had gained R and Snacks. Nothing read
# those sentences, so nobody noticed. Pin the names only -- a substring check
# per global, not a wording match -- so the sentences stay free to be
# rewritten but cannot go on naming a stale set.
def test_parity_prose_names_every_known_global():
    """Whoever adds a global must fix the two comments that list them."""
    globals_ = _luacheck_known_globals()
    sources = {
        ".config/nvim/.luacheckrc": LUACHECKRC.read_text(encoding="utf-8").split(
            "\nstd ="
        )[0],
        ".github/workflows/ci.yml": (REPO_ROOT / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        ),
    }
    for label, prose in sources.items():
        missing = sorted(g for g in globals_ if g not in prose)
        assert not missing, (
            f"{label} describes the known-globals set but never names "
            f"{missing}; it still describes an older set"
        )


def _posttooluse_edit_hooks() -> list[dict]:
    settings = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    hooks = []
    for entry in settings["hooks"].get("PostToolUse", []):
        if entry.get("matcher") == "Write|Edit|MultiEdit":
            hooks += entry["hooks"]
    return hooks


def test_posttooluse_edit_hook_is_a_single_ordered_handler():
    """Formatting must precede linting, and only one handler can promise that.

    Claude Code runs every handler matching an event in parallel -- its docs
    say "Since hooks run in parallel, the order is non-deterministic" -- but
    this repo is built on the opposite assumption. lint.sh's header declares it
    expects to run after auto-format.sh, and _lint_common.sh reports findings
    the formatter owns (ruff's import sort, rubocop's Layout) on that basis;
    commit 132dbfd reasoned from the same premise when it gave the Codex copy a
    `before-format` phase and left Claude's alone. Listing the two scripts side
    by side in one `hooks` array therefore bought ordering that was never real,
    and when lint won the race it handed the agent a hand-fix turn for work the
    formatter was about to do. format-then-lint.sh restores the sequence; see
    tests/test_format_then_lint.py for what it guarantees.
    """
    hooks = _posttooluse_edit_hooks()
    # Anti-vacuity, same convention as the deny-rule tests above: renaming the
    # matcher would empty this list and pass every assertion below while the
    # formatting and linting gate is gone entirely.
    assert hooks, "expected PostToolUse hooks under matcher 'Write|Edit|MultiEdit'"
    commands = [hook["command"] for hook in hooks]
    assert len(hooks) == 1, (
        "two handlers under one matcher run in parallel, so the "
        f"format-then-lint order is not guaranteed: {commands}"
    )
    assert "format-then-lint.sh" in commands[0], commands
