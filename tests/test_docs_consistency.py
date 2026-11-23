"""Docs must not contradict the code they describe.

The suite pins behaviour; these tests pin prose, which otherwise drifts silently:

- ``docs/setup.md``'s manual-install recipe is the only path a user has when
  they do not run ``install.sh``. It is documentation, so no test executes it
  and no linter compares it to the script it mirrors.
- ``.tmux.conf`` comments that quote a keystroke must quote the prefix the file
  binds, not one it unbinds.
- Config comments must not describe a ``.codex/hooks/_bash_review_common.py``
  symlink. ``test_hook_sync.py`` forbids that file existing in any form, so
  such a comment would describe a tree the suite actively prevents.

These are string-level checks on purpose: the invariant IS the text.
"""

import os
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


# ln -sf[n] ~/dotfiles/<path> ~/... — a single, fully spelled-out source.
_DOC_LITERAL_LINK = re.compile(r"ln -sfn? ~/dotfiles/(?P<path>\S+)")
# for e in A B C; do ... ln -sf[n] ~/dotfiles/<prefix>/$e — the loop form.
_DOC_FOR_LOOP = re.compile(
    r"for (?P<var>\w+) in (?P<entries>[^;]+); do\s*\n"
    r"\s*ln -sfn? ~/dotfiles/(?P<prefix>\S+?)/\$(?P=var)\b"
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
    up with the same tree. A link added to the script but not to the recipe
    leaves that config silently unmanaged on the manual path.
    """
    missing = sorted(_install_sh_sources() - _setup_doc_sources())
    assert not missing, (
        "install.sh links these repo paths but docs/setup.md's manual recipe "
        f"never mentions them: {missing}"
    )


# link_path "$HOME/<target>" "$HOME/<link>" — a link whose target lives under
# $HOME rather than in the checkout, and its manual-recipe spelling
# `ln -sfn ~/<target> ~/<link>`. There is none today: csb used to be one
# (~/.local/bin/csb -> ~/.claude/hooks/claude_sandbox.py) and is now a written
# wrapper (see the csb tests below).
_HOME_LINK = re.compile(r'link_path "\$HOME/(?P<src>[^"$]+)" "\$HOME/(?P<dest>[^"$]+)"')
_DOC_HOME_LINK = re.compile(r"ln -sfn? ~/(?!dotfiles/)(?P<src>\S+) ~/(?P<dest>\S+)")
# Every call site, however its arguments are spelled: the parser above must not
# be able to miss one silently.
_ANY_LINK_PATH_CALL = re.compile(r'\s*link_path "')
# Call sites inside link_entry pass variables through ("$src" "$dest"); they are
# the generic path, already covered by the source-set comparison above.
_GENERIC_LINK_PATH_CALLS = {'link_path "$src" "$dest"'}
EXPECTED_HOME_LINKS: set[tuple[str, str]] = set()


def test_every_link_path_call_is_parsed():
    text = INSTALL_SH.read_text(encoding="utf-8")
    calls = [
        line.strip() for line in text.splitlines() if _ANY_LINK_PATH_CALL.match(line)
    ]
    assert calls, "failed to find link_path's own call sites in install.sh"
    literal = [c for c in calls if c not in _GENERIC_LINK_PATH_CALLS]
    assert len(literal) == len(_HOME_LINK.findall(text)), (
        f"a link_path call the $HOME-relative parser does not read: {literal}"
    )


def test_manual_setup_covers_every_home_relative_link():
    """The source-set comparison above only sees links into the checkout.

    Both directions, and the expected set is pinned: today install.sh makes no
    $HOME-relative link, so the recipe must not make one either (it used to tell
    the reader to symlink csb, which bash-review now refuses to trust).
    """
    install = set(_HOME_LINK.findall(INSTALL_SH.read_text(encoding="utf-8")))
    documented = set(_DOC_HOME_LINK.findall(SETUP_DOC.read_text(encoding="utf-8")))
    assert install == EXPECTED_HOME_LINKS, (
        "install.sh's $HOME-relative links changed; update EXPECTED_HOME_LINKS "
        f"and docs/setup.md: {sorted(install)}"
    )
    assert documented == install, (
        f"docs/setup.md links {sorted(documented - install)} that install.sh does "
        f"not, and misses {sorted(install - documented)}"
    )


# csb: install.sh writes ~/.local/bin/csb from _sandbox_gate.csb_wrapper_text, and
# bash-review trusts a csb call only when the file is that template byte for byte.
# The manual recipe must produce the same file, so it must call the same function.
_CSB_TEMPLATE_CALL = "csb_wrapper_text("


def _doc_csb_recipe() -> str:
    """The recipe's csb lines: from its `mkdir -p ~/.local/bin` to the final mv."""
    lines = SETUP_DOC.read_text(encoding="utf-8").splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if line.startswith("mkdir -p ~/.local/bin")
        and any(_CSB_TEMPLATE_CALL in ln for ln in lines[i : i + 6])
    )
    end = next(
        i
        for i in range(start, len(lines))
        if lines[i].rstrip().endswith("~/.local/bin/csb")
    )
    return "\n".join(lines[start : end + 1]) + "\n"


def test_install_sh_and_the_recipe_share_the_csb_template():
    install = INSTALL_SH.read_text(encoding="utf-8")
    assert install.count(_CSB_TEMPLATE_CALL) == 1
    recipe = _doc_csb_recipe()
    assert recipe.count(_CSB_TEMPLATE_CALL) == 1
    # the interpreter is the launchers' pick, as in install.sh (bash-review
    # trusts only a wrapper naming the interpreter it runs on itself): the
    # launcher is sourced for its _trusted_python, and PATH's python3 is not asked
    pick = '_trusted_python 0 / "${_TRUSTED_CANDIDATES[@]}"'
    assert pick in recipe and pick in install
    launcher = ".claude/hooks/bash-review-launcher.sh"
    assert (
        f". ~/dotfiles/{launcher}" in recipe
        and f'. "$DOTFILES_DIR/{launcher}"' in install
    )
    assert "python3 -c" not in recipe
    assert "cd ~ &&" in recipe and 'cd "$HOME" &&' in install
    assert " -I -S -B -c " in recipe and " -I -S -B -c " in install
    # zsh would source the launcher's bash with 1-based arrays
    assert recipe.splitlines()[1].startswith("/bin/bash -c ")
    assert "claude_sandbox.py ~/.local/bin/csb" not in SETUP_DOC.read_text(
        encoding="utf-8"
    )


def test_the_recipe_writes_the_wrapper_bash_review_trusts(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_ROOT / ".claude" / "hooks"))
    import _sandbox_gate as gate

    home = tmp_path / "home"
    home.mkdir()
    (home / "dotfiles").symlink_to(REPO_ROOT)
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "csb").symlink_to(
        home / ".claude" / "hooks" / "claude_sandbox.py"
    )  # an old symlinked csb is replaced, not written through
    trusted = gate.trusted_python()
    if trusted is None:
        pytest.skip("no trusted system python3 on this machine")
    # PATH's python3 (a pyenv shim, say) must not be what the recipe asks
    marker = tmp_path / "path-python3-ran"
    stub = tmp_path / "stub-bin"
    stub.mkdir()
    (stub / "python3").write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 1\n')
    (stub / "python3").chmod(0o755)
    path = os.pathsep.join([str(stub), os.environ.get("PATH", "")])
    res = subprocess.run(
        ["bash", "-c", _doc_csb_recipe()],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home), "PATH": path},
        cwd=tmp_path,
        timeout=60,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    csb = home / ".local" / "bin" / "csb"
    assert csb.is_file() and not csb.is_symlink()
    assert (csb.stat().st_mode & 0o777) == 0o755
    assert gate.parse_csb_wrapper(csb.read_bytes(), str(home)) == trusted
    assert csb.read_text() == gate.csb_wrapper_text(trusted, str(home))
    assert not marker.exists()


def test_manual_setup_links_directories_with_n():
    """`ln -sf SRC DEST` onto an existing link to a directory follows it and
    plants the new link INSIDE that directory (inside the checkout, on a
    rerun of the recipe). Every directory source needs `-n`."""
    text = SETUP_DOC.read_text(encoding="utf-8")
    offenders = []
    for m in re.finditer(r"ln -sf ~/dotfiles/(?P<path>\S+)", text):
        if "$" not in m.group("path") and (REPO_ROOT / m.group("path")).is_dir():
            offenders.append(m.group("path"))
    for m in _DOC_FOR_LOOP.finditer(text):
        if "ln -sfn " in m.group(0):
            continue
        dirs = [
            e
            for e in m.group("entries").split()
            if (REPO_ROOT / m.group("prefix") / e).is_dir()
        ]
        if dirs:
            offenders.append(f"{m.group('prefix')}/{{{','.join(dirs)}}}")
    assert not offenders, f"docs/setup.md links directories without -n: {offenders}"


_DOCS_WITH_NPM_PREFIX = ["docs/setup.md", "INSTALL_PLATFORM.md"]


@pytest.mark.parametrize("rel", _DOCS_WITH_NPM_PREFIX)
def test_npm_prefix_is_not_a_quoted_tilde(rel):
    """`npm config set prefix '~/.npm-global'` keeps the tilde literal: npm
    then installs into a directory named `~` under the current directory."""
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert not re.search(r"npm config set prefix ['\"]~", text), rel


INSTALL_PLATFORM = REPO_ROOT / "INSTALL_PLATFORM.md"


# NodeSource: install.sh adds its apt repository only when the keyring holds
# exactly the pinned primary key (_add_signed_apt_repo / _keyring_primary_fprs).
# The manual recipe must apply the same test -- an awk that stops at the first
# `fpr` record, say, passes a keyring with a second, unpinned key behind it.
def _install_sh_pin(var: str) -> str:
    m = re.search(rf'^{var}="([^"]*)"', INSTALL_SH.read_text(encoding="utf-8"), re.M)
    assert m, f"{var} is not defined in install.sh"
    return m.group(1)


def _doc_nodesource_recipe_lines() -> list:
    return [
        ln
        for ln in SETUP_DOC.read_text(encoding="utf-8").splitlines()
        if "nodesource" in ln.lower() and not ln.lstrip().startswith("#")
    ]


def _doc_nodesource_check() -> str:
    """The recipe's check: from its `want=` line to the `fi` closing the if."""
    lines = SETUP_DOC.read_text(encoding="utf-8").splitlines()
    start = next(
        i
        for i, ln in enumerate(lines)
        if ln.startswith('want="$(sed -n') and "NODESOURCE_APT_KEY_FPR" in ln
    )
    end = next(i for i in range(start, len(lines)) if lines[i] == "fi")
    return "\n".join(lines[start : end + 1]) + "\n"


def test_the_nodesource_recipe_dearmors_without_a_prompt_on_a_rerun():
    dearmor = [ln for ln in _doc_nodesource_recipe_lines() if "--dearmor" in ln]
    assert len(dearmor) == 1, dearmor
    # without --batch --yes a rerun stops at "File ... exists. Overwrite? (y/N)"
    assert "gpg --batch --yes --dearmor" in dearmor[0]


def test_the_nodesource_recipe_reads_primary_fingerprints_like_install_sh():
    install = INSTALL_SH.read_text(encoding="utf-8")
    m = re.search(
        r"awk -F: ('[^']*')", install[install.index("_keyring_primary_fprs()") :]
    )
    assert m, "failed to find the awk in _keyring_primary_fprs"
    assert m.group(1) in _doc_nodesource_check()


def _run_nodesource_check(tmp_path, shown):
    """Run the recipe's check with sudo/gpg/apt-get stubbed: `shown` are the
    fingerprints of the primary keys the stub gpg lists (each with a subkey)."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "dotfiles").symlink_to(REPO_ROOT)
    stub = tmp_path / "stub-bin"
    stub.mkdir()
    calls = tmp_path / "calls.log"
    rows = []
    for fpr in shown:
        rows += [
            f"pub:-:4096:1:{fpr[-16:]}:1487788586:::-:::scESC::::::23::0:",
            f"fpr:::::::::{fpr}:",
            "sub:-:4096:1:7EA0A9C3F273FCD8:1487788586::::::s::::::23:",
            "fpr:::::::::D3306A018370199E527AE7997EA0A9C3F273FCD8:",
        ]
    listing = "".join(f"{r}\\n" for r in rows)
    for name, body in (
        ("gpg", f"printf '{listing}'"),
        ("sudo", 'case "$1" in tee) cat >/dev/null ;; esac'),
        ("apt-get", ""),
    ):
        path = stub / name
        path.write_text(f'#!/bin/sh\necho "{name} $*" >>"{calls}"\n{body}\n')
        path.chmod(0o755)
    res = subprocess.run(
        ["bash", "-c", _doc_nodesource_check()],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home), "PATH": f"{stub}:{os.environ['PATH']}"},
        cwd=tmp_path,
        timeout=60,
        check=False,
    )
    log = calls.read_text().splitlines() if calls.exists() else []
    return res, log


def test_the_nodesource_recipe_adds_the_repository_for_the_pinned_key(tmp_path):
    res, log = _run_nodesource_check(
        tmp_path, [_install_sh_pin("NODESOURCE_APT_KEY_FPR")]
    )
    assert res.returncode == 0, res.stderr
    assert "sudo tee /etc/apt/sources.list.d/nodesource.list" in log
    assert "sudo apt-get install -y nodejs" in log
    assert not [c for c in log if c.startswith("sudo rm")]


@pytest.mark.parametrize(
    "shown",
    [
        pytest.param(["0123456789ABCDEF0123456789ABCDEF01234567"], id="another-key"),
        pytest.param(
            ["{pin}", "0123456789ABCDEF0123456789ABCDEF01234567"], id="an-extra-key"
        ),
        pytest.param([], id="no-key"),
    ],
)
def test_the_nodesource_recipe_discards_any_other_keyring(tmp_path, shown):
    pin = _install_sh_pin("NODESOURCE_APT_KEY_FPR")
    res, log = _run_nodesource_check(tmp_path, [s.format(pin=pin) for s in shown])
    assert "sudo rm -f /etc/apt/keyrings/nodesource.gpg" in log
    assert "sudo tee /etc/apt/sources.list.d/nodesource.list" not in log
    assert not [c for c in log if c.startswith(("sudo apt-get", "apt-get"))]
    assert "got:" in res.stderr and pin in res.stderr


def test_install_platform_does_not_list_debian_11_as_supported():
    """Debian 11's python3 is 3.9, and the `mcp` package the gemini-consultant
    step installs needs 3.10 or later: that step would fail on every run."""
    text = INSTALL_PLATFORM.read_text(encoding="utf-8")
    for line in text.splitlines():
        # "Debian 11 (Bullseye) 以降" / "Debian 11 以降": a supported range
        assert not re.search(r"Debian 11( \(Bullseye\))? 以降", line), line
    assert re.search(r"^- Debian 12 .*以降", text, re.M)


def test_install_platform_quotes_the_failure_hint_install_sh_prints():
    """The sample failure summary must match what the script prints."""
    m = re.search(
        r'print_info "(Reasons are printed above as [^"]+)"',
        INSTALL_SH.read_text(encoding="utf-8"),
    )
    assert m, "failed to find the failure hint in install.sh"
    assert m.group(1) in INSTALL_PLATFORM.read_text(encoding="utf-8")


def test_install_platform_states_the_python_needed_by_gen_codex_agents():
    """gen_codex_agents.py imports tomllib (3.11+), newer than the stock
    python3 of the oldest Ubuntu release INSTALL_PLATFORM.md lists."""
    gen = (REPO_ROOT / "scripts/gen_codex_agents.py").read_text(encoding="utf-8")
    if not re.search(r"^import tomllib", gen, re.M):
        pytest.skip("the generator no longer needs tomllib")
    text = INSTALL_PLATFORM.read_text(encoding="utf-8")
    assert re.search(r"Python 3\.11", text), (
        "INSTALL_PLATFORM.md must state Python 3.11+"
    )


def test_install_platform_has_a_docker_section():
    text = INSTALL_PLATFORM.read_text(encoding="utf-8")
    assert re.search(r"^## Docker", text, re.M)
    for needle in ("docker info", "hadolint", "csb", "newgrp docker", "docker-desktop"):
        assert needle in text, needle


_VSCODE_USER_DIR = re.compile(r'vscode_user_dir="(?P<dir>[^"]+)"')


def test_manual_setup_uses_every_vscode_user_dir_install_sh_uses():
    """The link SOURCES matching is not enough: the destinations are OS-branched.

    install.sh picks the VS Code user dir per OS, including %APPDATA%\\Code\\User
    for Git Bash, where VS Code never reads $HOME/.config. A recipe missing one
    branch links into a directory VS Code ignores, which the source-set
    comparison above cannot see.
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

    A comment quoting `C-b C-p` while the file sets another prefix and unbinds
    C-b documents a keystroke this config guarantees does nothing.
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
# pins the prose about it: .coveragerc and ci.yml must not explain the coverage
# setup in terms of a .codex/hooks/_bash_review_common.py symlink that
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
# While no ci.yml job sets continue-on-error, a tool config's header must not
# tell the reader its job is "NON-GATING in CI (advisory only)": the file a
# contributor reads first when a warning appears would say it cannot break the
# build.

_ADVISORY_CLAIM = re.compile(r"NON-GATING|non-gating|advisory only|助言的")

# An actual YAML key, not the word inside a comment: a plain substring test
# would also match prose that mentions continue-on-error, and skip -- passing
# vacuously forever.
_CONTINUE_ON_ERROR_KEY = re.compile(r"^\s*continue-on-error\s*:", re.MULTILINE)

# Tool configs that describe how strictly CI treats them. ci.yml is absent: it
# is the source the claims are checked against, not a claim itself.
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


# --------------------------------------------------------------------------
# Prose must not name a repo script that does not exist
# --------------------------------------------------------------------------
# A reader (or an agent) resolves a named script path as written, so the name
# alone is not enough: it must resolve from where the reader stands. A skill
# saying "a CLI fallback (`scripts/image_gen.py`) exists" is a ghost reference
# when that script lives elsewhere (Codex ships it under
# .codex/skills/.system/imagegen/scripts/), not at the repo-root `scripts/`.
# Generalised rather than pinned to that one name, since the same mistake is
# one rename away in any of these files.
#
# A reference is satisfied by the repo root OR by the file's own directory:
# bundling a `scripts/` beside a SKILL.md is this repo's own layout, so
# root-only resolution would fail a skill whose script is right there next to
# it.
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
# main() runs create_symlinks FIRST, directly after detect_os (see the
# "Symlinks first" comment there): everything below it can fail on a flaky
# network or a renamed formula. The README's list must say the same, or a
# reader hitting a network failure mid-run would guess backwards about whether
# their dotfiles are linked.
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


# Codex CLI 0.154 removed its MCP-server mode, so the `mcp__codex__*` tools can
# never connect again: an instruction that still names them sends the model to
# a tool that does not exist (and the second-opinion pair silently loses its
# Codex half to Gemini). Every Codex call goes through `codex exec` now. The patterns are
# assembled so this file does not match itself.
_REMOVED_CODEX_MCP = re.compile(
    "|".join(
        (
            re.escape("mcp__" + "codex__"),
            re.escape("codex " + "mcp-server"),
            re.escape("codex" + "-reply"),
        )
    )
)


def _tracked_files() -> list:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        p
        for p in out.split("\0")
        # Vendored by Codex itself, and tests that assert the absence.
        if p and not p.startswith((".codex/skills/.system/", "tests/"))
    ]


def test_nothing_names_the_removed_codex_mcp_server():
    hits = []
    for rel in _tracked_files():
        try:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if _REMOVED_CODEX_MCP.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:100]}")
    assert not hits, "still points at the removed Codex MCP server:\n" + "\n".join(hits)


# Shell fences only: a Python example that quotes the command in a string (the
# subprocess anti-pattern in python-scripting-patterns) is not an invocation.
# Indented fences count too: the skills put the command inside a numbered list.
_FENCE = re.compile(
    r"^[ \t]*```(?:bash|sh|shell|zsh)\b[^\n]*\n(.*?)^[ \t]*```", re.M | re.S
)


def _codex_exec_command_lines() -> list:
    lines = []
    for rel_dir in (".claude/skills", ".claude/rules", ".claude/agents", ".claude"):
        for path in _tracked_markdown(rel_dir):
            if rel_dir == ".claude" and path.parent != REPO_ROOT / ".claude":
                continue
            for block in _FENCE.findall(path.read_text(encoding="utf-8")):
                for line in block.splitlines():
                    if re.search(r"\bcodex exec\b", line):
                        lines.append((path.relative_to(REPO_ROOT), line.strip()))
    return lines


def test_skills_document_codex_exec_invocations():
    # The migration must leave a runnable command shape behind, not only prose.
    assert _codex_exec_command_lines(), "no `codex exec` command shape documented"


@pytest.mark.parametrize("rel,line", _codex_exec_command_lines() or [("", "")])
def test_every_documented_codex_exec_scans_the_prompt_first(rel, line):
    """A prompt written with the Write tool never passes through bash-review, so
    the secret scan in front of `codex exec` is the only one it gets. Chained with
    `&&` it also fails closed: exit 1 (a credential) and exit 2 (scanner could not
    run) both stop the send."""
    if not line:
        pytest.skip("no documented invocation")
    scan_at = line.find("secret_scan.py")
    assert 0 <= scan_at < line.index("codex exec"), f"{rel}: {line}"
    assert "&&" in line[scan_at : line.index("codex exec")], f"{rel}: {line}"
    for forbidden in ("--last", "danger-full-access", "--dangerously"):
        assert forbidden not in line, f"{rel}: {forbidden} in {line}"
