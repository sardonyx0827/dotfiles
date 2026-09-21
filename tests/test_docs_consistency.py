"""Docs must not contradict the code they describe.

Every test here guards a contradiction that was actually found in the tree and
that no other check could see. The suite already pins behaviour; nothing pinned
prose, so prose drifted:

- ``docs/setup.md``'s manual-install recipe is the only path a user has when
  they do not run ``install.sh``. It had fallen one entry behind the script
  (the VS Code files), and nothing failed -- the recipe is documentation, so no
  test executed it and no linter compared it to the script it mirrors.
- ``.tmux.conf`` moved its prefix from ``C-b`` to ``C-a`` but two comments kept
  quoting the old key, so the file documented keystrokes it does not bind.
- ``.coveragerc`` explained an omission in terms of a
  ``.codex/hooks/_bash_review_common.py`` symlink. ``test_hook_sync.py`` forbids
  that file existing in any form, so the comment described a tree the suite
  actively prevents.

These are string-level checks on purpose: the invariant IS the text.
"""

import re
import subprocess

import pytest
from conftest import REPO_ROOT

INSTALL_SH = REPO_ROOT / "install.sh"
SETUP_DOC = REPO_ROOT / "docs/setup.md"
TMUX_CONF = REPO_ROOT / ".tmux.conf"


# --------------------------------------------------------------------------
# docs/setup.md ("7. シンボリックリンクの作成") vs install.sh's link set
# --------------------------------------------------------------------------
# Both sides describe the same thing in different notation, so each is expanded
# to a set of repo-relative source paths before comparing.

# link_entry "$DOTFILES_DIR/<literal>" — a single, fully spelled-out source.
_LITERAL_LINK = re.compile(r'link_entry "\$DOTFILES_DIR/(?P<path>[^"$]+)"')
# for <var> in "${<array>[@]}" — binds a loop variable to an array name.
_FOR_OVER_ARRAY = re.compile(r'for (?P<var>\w+) in "\$\{(?P<array>\w+)\[@\]\}"')
# <array>=( "a" "b" ... ) — the array literal itself (possibly multi-line).
_ARRAY_LITERAL = r"{name}=\((?P<body>[^)]*)\)"
# link_entry "$DOTFILES_DIR/<prefix>$<var>" — a source built from a loop var.
_TEMPLATED_LINK = r'link_entry "\$DOTFILES_DIR/(?P<prefix>[^"$]*)\${var}"'

# link_oh_my_zsh_theme globs the themes dir instead of listing entries, so the
# concrete file cannot be read out of install.sh. Pin it here; the assertion
# below still fails if the docs stop mentioning it.
_GLOBBED_SOURCES = {".oh-my-zsh/custom/themes/px-rose-pine.zsh-theme"}


def _install_sh_sources() -> set[str]:
    """Repo-relative paths install.sh symlinks into $HOME."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    sources = set(_GLOBBED_SOURCES)

    for m in _LITERAL_LINK.finditer(text):
        sources.add(m.group("path"))

    for loop in _FOR_OVER_ARRAY.finditer(text):
        var, array = loop.group("var"), loop.group("array")
        arr = re.search(_ARRAY_LITERAL.format(name=array), text, re.DOTALL)
        # Search forward from THIS loop header, not from the top of the file:
        # four of these loops share the variable name `entry`, so a global
        # search would give them all the same prefix.
        tmpl = re.search(_TEMPLATED_LINK.format(var=var), text[loop.end() :])
        if not arr or not tmpl:
            continue
        prefix = tmpl.group("prefix")
        for entry in re.findall(r'"([^"]+)"', arr.group("body")):
            sources.add(prefix + entry)

    assert sources, "failed to parse any link source out of install.sh"
    return sources


# ln -sf ~/dotfiles/<path> ~/... — a single, fully spelled-out source.
_DOC_LITERAL_LINK = re.compile(r"ln -sf ~/dotfiles/(?P<path>\S+)")
# for e in A B C; do ... ln -sf ~/dotfiles/<prefix>/$e — the loop form.
_DOC_FOR_LOOP = re.compile(
    r"for (?P<var>\w+) in (?P<entries>[^;]+); do\s*\n"
    r"\s*ln -sf ~/dotfiles/(?P<prefix>\S+?)/\$(?P=var)\b"
)


def _setup_doc_sources() -> set[str]:
    """Repo-relative paths docs/setup.md tells the reader to symlink."""
    text = SETUP_DOC.read_text(encoding="utf-8")
    sources = {
        m.group("path")
        for m in _DOC_LITERAL_LINK.finditer(text)
        # The loop form's own `~/dotfiles/<prefix>/$e` is expanded below.
        if "$" not in m.group("path")
    }
    for m in _DOC_FOR_LOOP.finditer(text):
        for entry in m.group("entries").split():
            sources.add(f"{m.group('prefix')}/{entry}")

    assert sources, "failed to parse any link source out of docs/setup.md"
    return sources


def test_manual_setup_covers_every_install_sh_link():
    """The manual recipe must not fall behind the script it mirrors.

    A user who follows docs/setup.md instead of running install.sh has to end
    up with the same tree. When _link_editor_configs gained the VS Code files,
    this recipe was not updated and those two configs were silently unmanaged
    for anyone on the manual path.
    """
    missing = sorted(_install_sh_sources() - _setup_doc_sources())
    assert not missing, (
        "install.sh links these repo paths but docs/setup.md's manual recipe "
        f"never mentions them: {missing}"
    )


_VSCODE_USER_DIR = re.compile(r'vscode_user_dir="(?P<dir>[^"]+)"')


def test_manual_setup_uses_every_vscode_user_dir_install_sh_uses():
    """The link SOURCES matching is not enough: the destinations are OS-branched.

    install.sh gained a third VS Code location -- %APPDATA%\\Code\\User for Git
    Bash, where the build never reads $HOME/.config -- after a Windows install
    linked into a directory VS Code ignores. The manual recipe kept the old
    two-way branch, so the same dead links awaited anyone following it, and
    the source-set comparison above could not see it.
    """
    expected = set(_VSCODE_USER_DIR.findall(INSTALL_SH.read_text(encoding="utf-8")))
    documented = set(_VSCODE_USER_DIR.findall(SETUP_DOC.read_text(encoding="utf-8")))
    assert expected, "failed to parse vscode_user_dir out of install.sh"
    missing = sorted(expected - documented)
    assert not missing, f"docs/setup.md's VS Code recipe never links into: {missing}"


def test_manual_setup_does_not_invent_links():
    """...and must not tell the reader to link something install.sh does not.

    The reverse drift is just as wrong and even quieter: the reader creates a
    link that install.sh will never refresh or back up.
    """
    extra = sorted(_setup_doc_sources() - _install_sh_sources())
    assert not extra, (
        "docs/setup.md tells the reader to link these paths but install.sh "
        f"does not: {extra}"
    )


# --------------------------------------------------------------------------
# .tmux.conf comments vs the prefix the file actually binds
# --------------------------------------------------------------------------

# `## C-a C-p Start logging.` — a comment quoting a prefix-then-key sequence.
_COMMENT_PREFIX_SEQUENCE = re.compile(
    r"^#+\s*(?P<prefix>C-[a-z])\s+C-[a-z]\b", re.MULTILINE
)


def test_tmux_comments_quote_the_configured_prefix():
    """Comments must name the prefix the file sets, not the one it unbinds.

    The prefix moved to C-a (and C-b is explicitly unbound), but the logging
    comments still read `C-b C-p` / `C-b C-o` -- instructions for a keystroke
    this config guarantees does nothing.
    """
    text = TMUX_CONF.read_text(encoding="utf-8")
    configured = re.search(r"^set -g prefix (?P<key>\S+)", text, re.MULTILINE)
    assert configured, ".tmux.conf must set an explicit prefix"
    prefix = configured.group("key")

    wrong = [
        m.group(0).strip()
        for m in _COMMENT_PREFIX_SEQUENCE.finditer(text)
        if m.group("prefix") != prefix
    ]
    assert not wrong, (
        f".tmux.conf binds prefix {prefix}, but these comments quote another "
        f"prefix: {wrong}"
    )


# --------------------------------------------------------------------------
# No tracked file may name a .codex/hooks path that does not exist
# --------------------------------------------------------------------------
# test_hook_sync.py pins the tree (no copy, no link under .codex/hooks). This
# pins the prose about it: .coveragerc and ci.yml both still explained the
# coverage setup in terms of a .codex/hooks/_bash_review_common.py symlink that
# test_hook_sync.py forbids from existing.

CI_YML = REPO_ROOT / ".github/workflows/ci.yml"

_CONFIG_FILES_DESCRIBING_HOOKS = [
    ".coveragerc",
    ".github/workflows/ci.yml",
]

_CODEX_HOOK_PATH = re.compile(r"\.codex/hooks/(?P<name>[\w.-]+)")


@pytest.mark.parametrize("rel", _CONFIG_FILES_DESCRIBING_HOOKS)
def test_config_files_only_name_codex_hook_paths_that_exist(rel):
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    missing = sorted(
        {
            m.group(0)
            for m in _CODEX_HOOK_PATH.finditer(text)
            if not (REPO_ROOT / m.group(0)).exists()
        }
    )
    assert not missing, (
        f"{rel} refers to .codex/hooks paths that do not exist "
        f"(stale since the shared libs stopped being copied/linked there): "
        f"{missing}"
    )


# --------------------------------------------------------------------------
# "This check is advisory" claims vs whether CI actually gates on it
# --------------------------------------------------------------------------
# luacheck shipped as continue-on-error, then was promoted to gating. ci.yml and
# docs/testing.md were updated; .luacheckrc's own header still told the reader
# the job was "NON-GATING in CI (advisory only)" -- the file a contributor reads
# first when a luacheck warning appears, telling them it cannot break the build.

_ADVISORY_CLAIM = re.compile(r"NON-GATING|non-gating|advisory only|助言的")

# An actual YAML key, not the word inside a comment. ci.yml's own prose says
# "助言的 (continue-on-error) から gating へ格上げした", so a plain substring test
# would see the promotion note itself and skip -- passing vacuously forever.
_CONTINUE_ON_ERROR_KEY = re.compile(r"^\s*continue-on-error\s*:", re.MULTILINE)

# Tool configs that describe how strictly CI treats them. ci.yml is deliberately
# absent: it narrates the promotion in past tense, which is history, not a claim
# about the current run.
_CONFIGS_DESCRIBING_CI_STRICTNESS = [
    ".config/nvim/.luacheckrc",
    ".coveragerc",
]


def test_no_config_claims_a_ci_check_is_advisory_while_ci_gates_everything():
    """No tool config may call itself advisory when no CI job is allowed to fail."""
    ci = CI_YML.read_text(encoding="utf-8")
    if _CONTINUE_ON_ERROR_KEY.search(ci):
        pytest.skip("a CI job is non-gating; the claim may be accurate")

    offenders = []
    for rel in _CONFIGS_DESCRIBING_CI_STRICTNESS:
        path = REPO_ROOT / rel
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _ADVISORY_CLAIM.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()}")

    assert not offenders, (
        "every CI job gates (no continue-on-error key in ci.yml), but these "
        f"files still describe a check as advisory: {offenders}"
    )


def test_manual_neovim_recipe_matches_the_installer_pin():
    """docs/setup.md hardcodes the Neovim version and digest install.sh pins.

    The manual recipe is the path for a reader who does not run install.sh, and
    it tells them to verify the download with `sha256sum -c`. A digest that has
    drifted from the script's turns that verification into a guaranteed failure
    on a correct download -- which teaches the reader to drop the check. The
    two must move together, so this compares them directly.
    """
    install = INSTALL_SH.read_text(encoding="utf-8")
    doc = SETUP_DOC.read_text(encoding="utf-8")

    pairs = [
        (
            "NEOVIM_VERSION",
            "NVIM_VERSION",
            r'^NEOVIM_VERSION="([^"]*)"',
            r"^NVIM_VERSION=(\S+)",
        ),
        (
            "NEOVIM_SHA256_X86_64",
            "NVIM_SHA256",
            r'^NEOVIM_SHA256_X86_64="([^"]*)"',
            r"^NVIM_SHA256=(\S+)",
        ),
    ]
    for script_name, doc_name, script_re, doc_re in pairs:
        in_script = re.search(script_re, install, re.MULTILINE)
        in_doc = re.search(doc_re, doc, re.MULTILINE)
        assert in_script, f"{script_name} is not defined in install.sh"
        assert in_doc, f"{doc_name} is not set in docs/setup.md's manual recipe"
        assert in_script.group(1) == in_doc.group(1), (
            f"install.sh's {script_name} is {in_script.group(1)!r} but "
            f"docs/setup.md's {doc_name} is {in_doc.group(1)!r}; the manual "
            "recipe verifies the download against this value, so a stale copy "
            "makes a correct download fail its checksum check"
        )

    # The arm64 digest appears in the recipe's prose, not as an assignment --
    # the reader swaps it in by hand. It rots the same way, and an arm64 reader
    # (Android's AVF Debian is arm64) has no other copy to check against.
    arm = re.search(r'^NEOVIM_SHA256_ARM64="([^"]*)"', install, re.MULTILINE)
    assert arm, "NEOVIM_SHA256_ARM64 is not defined in install.sh"
    assert arm.group(1) in doc, (
        f"install.sh pins the arm64 digest {arm.group(1)!r}, which docs/setup.md's "
        "manual recipe does not offer; an arm64 reader following the note would "
        "verify against the x86_64 digest and never get past sha256sum"
    )


# --------------------------------------------------------------------------
# Prose must not name a repo script that does not exist
# --------------------------------------------------------------------------
# codex-image-gen/SKILL.md told the reader "A CLI fallback
# (`scripts/image_gen.py`) exists". The script is real -- Codex ships it at
# .codex/skills/.system/imagegen/scripts/image_gen.py -- but it has never sat
# at the repo-root `scripts/` the sentence named, and bfbcc7a untracked that
# whole tree, so an agent resolving the path as written found nothing. The
# name alone is not enough: what makes a reference usable is that it resolves
# from where the reader stands. Generalised rather than pinned to that one
# name, since the same mistake is one rename away in any of these files.
#
# A reference is satisfied by the repo root OR by the file's own directory:
# bundling a `scripts/` beside a SKILL.md is this repo's own layout (five of
# the .codex system skills do it), so root-only resolution would fail a
# skill whose script is right there next to it.
_SCRIPT_REF_RE = re.compile(
    r"`((?:scripts|\.claude/hooks)/[A-Za-z0-9_.-]+\.(?:py|sh))`"
)

_PROSE_DIRS = (".claude/skills", ".claude/commands", ".claude/agents")


def _tracked_markdown(rel_dir: str) -> list:
    """Markdown files git tracks under rel_dir.

    Not rglob(): the working tree holds more than the repo owns. install.sh
    links ~/.claude/skills at .claude/skills, so Claude Code's skill sync
    drops third-party skills into that directory, and their prose is not ours
    to hold to this invariant. Because CI runs from a fresh checkout where
    they do not exist, a walk of the tree only ever failed on the author's
    machine -- green in CI, red locally. tests/test_hook_sync.py reads the
    tracked set the same way.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", rel_dir],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO_ROOT / p for p in out.split("\0") if p.endswith(".md")]


def test_prose_only_names_repo_scripts_that_exist():
    """A named `scripts/x.py` must be a file, or the reader chases a ghost."""
    missing = []
    scanned = 0
    for rel_dir in _PROSE_DIRS:
        for path in sorted(_tracked_markdown(rel_dir)):
            scanned += 1
            for match in _SCRIPT_REF_RE.finditer(path.read_text(encoding="utf-8")):
                ref = match.group(1)
                if (REPO_ROOT / ref).is_file() or (path.parent / ref).is_file():
                    continue
                missing.append(f"{path.relative_to(REPO_ROOT)} names {ref}")
    # Narrowing the walk to tracked files is only safe while the tracked set is
    # still the prose this guards; a pathspec typo would otherwise turn the
    # assertion below into a no-op that passes forever.
    assert scanned > 20, f"only {scanned} tracked .md files found in {_PROSE_DIRS}"
    assert not missing, "prose names scripts that do not exist: " + "; ".join(missing)


# --------------------------------------------------------------------------
# README's "what install.sh does" list vs the order main() actually runs
# --------------------------------------------------------------------------
# README listed symlink creation second-to-last, after every package and tool
# install. main() runs create_symlinks FIRST, directly after detect_os, and
# its comment says why: "Symlinks first: ... Everything below can fail on a
# flaky network or a renamed formula; when it ran last, one such failure left
# the machine with no dotfiles linked at all." The README never followed that
# fix, so a reader hitting a network failure mid-run would guess exactly
# backwards about whether their dotfiles are linked.
#
# Only that one invariant is pinned, not the whole sequence: the README list
# mixes real function calls with steps that are not (platform detection, font
# installation), so an order-for-order comparison would be fragile prose
# matching rather than a check of anything main() guarantees.
def test_readme_lists_symlink_creation_before_package_installs():
    """The README must not imply dotfiles are linked last."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    symlink_at = readme.index("設定ファイルのシンボリックリンク作成")
    packages_at = readme.index("必要なパッケージのインストール")
    assert symlink_at < packages_at, (
        "README lists symlink creation after package installation, but main() "
        "runs create_symlinks first, directly after detect_os"
    )


def test_main_runs_create_symlinks_before_any_package_install():
    """...and the ordering the README describes is still the real one."""
    install_sh = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    main_at = install_sh.index("\nmain() {")
    body = install_sh[main_at:]
    assert body.index("\n  create_symlinks\n") < body.index("install_os_packages"), (
        "main() no longer links before installing packages; update README.md "
        "and this pair of tests together"
    )
