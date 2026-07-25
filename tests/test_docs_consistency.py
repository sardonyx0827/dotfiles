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
