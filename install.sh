#!/usr/bin/env bash

# Dotfiles Installation Script
# Supports: macOS, Ubuntu/Debian (WSL is detected and treated as Ubuntu),
# Windows (Git Bash)

# -e: exit on error. -o pipefail: a pipeline (e.g. `curl ... | sudo tee`)
# fails if ANY stage fails, not just the last one -- without it, a failed
# curl in front of `sudo tee`/`sudo dd` would go unnoticed.
# (-u is intentionally omitted: several env vars are read without a
# default elsewhere in this script, so enabling it would turn those
# reads into aborts, and auditing every use is out of scope.)
set -eo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Dotfiles directory: resolved from this script's location so the repo can
# be cloned anywhere (~/dotfiles, ~/work/github/dotfiles, ...)
DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Dry-run: when 1, the destructive / user-specific steps (backups, symlinks,
# rendered config files, chsh) are PRINTED instead of performed, and nothing on
# disk is touched. --dry-run sets this; it is also env-overridable so tests can
# exercise a single function in dry-run the same way they set OS=macos.
# Package / tool installation (Homebrew, APT, Node, AI CLIs, ...) is not
# simulated -- main() announces and skips that whole block. See usage().
DRY_RUN="${DRY_RUN:-0}"
# Every DRY_RUN guard below spells `[ "$DRY_RUN" -eq 1 ]`, an integer
# comparison. Given a non-numeric value `[` fails with "integer expression
# expected" and, because the guard sits in a condition, `set -e` does not fire:
# ALL of them fall through to the real branch. Since the value is deliberately
# env-overridable (see above), `DRY_RUN=true` is a reachable typo that would
# relink $HOME and move the user's dotfiles aside while looking like a preview.
# Reject anything but 0/1 here rather than coercing.
case "$DRY_RUN" in
0 | 1) ;;
*)
  printf 'install.sh: DRY_RUN must be 0 or 1 (got: %s)\n' "$DRY_RUN" >&2
  exit 2
  ;;
esac

# --- Pinned upstream bootstrap scripts ---------------------------------------
# These four refs are fetched over the network: three are executed (Homebrew,
# lazydocker, ohmyzsh) and one is downloaded as a plugin file (vim-plug). All
# four run as the invoking user -- none of them under sudo. Pointing at
# HEAD/master means the bytes that run can change between two runs of this
# script with no signal to us, so each is pinned to an immutable commit.
# raw.githubusercontent.com serves any commit SHA, and a commit SHA already
# identifies its content cryptographically, so pinning the ref gives the same
# guarantee a separate checksum table would -- without a manifest to maintain,
# and without breaking every time upstream ships a release.
#
# What this does NOT cover: the pinned installer still downloads whatever is
# current when it runs (Homebrew's own formulae, the ohmyzsh clone, ...). The
# boundary is deliberate -- it is the bootstrap script itself that is verified,
# not the tree it goes on to install.
#
# The remaining fetched installers go through vendor redirectors (astral.sh/uv,
# pyenv.run) that expose no immutable ref, so they stay unpinned; pinning them
# would require a content hash re-pinned on every upstream release. Both run as
# the invoking user.
#
# No fetched script is executed as root. Docker Engine and Node.js on
# Ubuntu/Debian used to come from get.docker.com (`sudo sh`) and NodeSource's
# setup_lts.x (`sudo -E bash`) -- the highest-privilege step with the weakest
# integrity check. They now come from the vendors' signed apt repositories,
# whose keyring must hold exactly the pinned key below (_add_signed_apt_repo),
# and fetch_and_run refuses a sudo interpreter outright.
#
# Other places pipe a download into a root process to *store* bytes, not run
# them (the apt keyrings: `curl | sudo gpg --dearmor`, `curl | sudo tee`,
# `curl | sudo dd`), and `sudo apt-get install` runs vendor dpkg maintainer
# scripts as root -- separate trust paths that pinning a raw.githubusercontent
# ref would not address either way. Only the Docker and NodeSource keyrings are
# checked against a pinned fingerprint; gh's and WezTerm's are not (yet).
#
# To refresh a pin (do this deliberately, and review the diff):
#   git ls-remote https://github.com/Homebrew/install HEAD
#   git ls-remote https://github.com/jesseduffield/lazydocker master
#   git ls-remote https://github.com/ohmyzsh/ohmyzsh master
#   git ls-remote https://github.com/junegunn/vim-plug master
# A pin left alone indefinitely goes stale rather than insecure: an old
# installer may stop working on a newer OS, which surfaces as a visible
# failure, not a silent one.
HOMEBREW_INSTALL_REF="99e13e96cbbdc1ac1ac09c0a40b450bf219ef3aa"
LAZYDOCKER_INSTALL_REF="7e7aadc2071d58031bf2daafca1fbd4093efc23f"
OHMYZSH_INSTALL_REF="98fe9b81a62ed75baf25cf23aa41e338a83bec6d"
VIM_PLUG_REF="88e31471818e9a29a8a20a0ee61360cfd7bdc1cd"

# --- Pinned MCP servers, tools and apt signing keys --------------------------
# Claude Code starts the MCP servers below at the start of every session, so an
# unpinned spec (`uvx --from git+...` on HEAD, `npx -y <pkg>` on latest) runs
# whatever upstream published last, with no signal to us. Each is pinned to an
# exact commit / version instead. register_claude_mcp_servers re-registers a
# server whose registered command no longer matches, so bumping a pin here and
# re-running ./install.sh is the whole upgrade.
#
# To bump (deliberately, after reading the upstream changelog / diff):
#   git ls-remote https://github.com/oraios/serena HEAD    -> SERENA_REF
#   npm view @upstash/context7-mcp version                  -> CONTEXT7_MCP_VERSION
#   python3 -m pip index versions mcp                       -> MCP_PY_VERSION
#     Stay on 1.x: mcp 2.x no longer ships mcp.server.fastmcp, which
#     .claude/mcp-servers/gemini-consultant/server.py imports.
SERENA_REF="7f7d6245bf96b60fdf4e752a779917372fd1ee6e"
CONTEXT7_MCP_VERSION="4.7.3"
MCP_PY_VERSION="1.30.0"

# hadolint (Dockerfile linter) on Ubuntu/Debian: a release binary, installed
# only when its sha256 matches. The digests are the release's own
# checksums.sha256, cross-checked against the GitHub asset digests:
#   gh release view --repo hadolint/hadolint                  (latest tag)
#   gh release download vX.Y.Z --repo hadolint/hadolint --pattern checksums.sha256
HADOLINT_VERSION="2.15.1"
HADOLINT_SHA256_X86_64="c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507"
HADOLINT_SHA256_ARM64="f6198ef8090f404dbb771abfee086eb8c48ac177f30da7fd3510aca35b344b5d"

# Primary-key fingerprints the Docker and NodeSource apt keyrings must carry
# (_add_signed_apt_repo). Docker's is the one its install docs have long
# published; NodeSource publishes none, so its value is the key its keyring URL
# served when this was pinned (trust on first use). A rotated key fails the
# check loudly rather than being trusted silently. To re-check a key:
#   curl -fsSL <key url> | gpg --show-keys --with-colons    (the `fpr` after `pub`)
DOCKER_APT_KEY_FPR="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
NODESOURCE_APT_KEY_FPR="6F71F525282841EEDAF851B42F59B5F99B1BE0B4"
# The Node.js major the NodeSource repository is set up for (an LTS line; the
# old setup_lts.x picked whichever LTS was current on the day it ran).
NODESOURCE_NODE_MAJOR="24"

print_info() {
  echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
  echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
  echo -e "${RED}[ERROR]${NC} $1"
}

# --- Install-failure bookkeeping ---------------------------------------------
# Every optional step warns and carries on (one flaky formula or a dropped
# connection must not stop the rest of the run), which used to leave the run
# ending in an unconditional "Installation completed!" however many steps had
# failed. record_failure notes a step that did not install; main() prints the
# list at the end and exits 1 when it is non-empty.
#
# A plain global indexed array, bash 3.2 safe (macOS /bin/bash): `+=`,
# `${#a[@]}` and `"${a[@]}"` only -- no negative subscripts, associative
# arrays, mapfile or `declare -g`. It must be assigned at the top level so it
# stays global when this file is sourced.
#
# record_failure appends in the CURRENT shell, so never call it, try_install or
# pip_install_user inside `$(...)`, a pipeline or a background job: that runs
# in a subshell and the entry is silently lost. tests/test_install.py checks
# the source for exactly that.
INSTALL_FAILURES=()

# Record that the step called LABEL did not install. Always returns 0: callers
# call it bare under `set -e`, and a non-zero status here would abort the run
# it is only meant to describe. An identical consecutive label is dropped, so
# one failure path that reports itself twice reads as one step; independent
# failures that happen to share a label further apart are kept.
record_failure() {
  local label="$1" count=${#INSTALL_FAILURES[@]}
  if [ "$count" -gt 0 ] && [ "${INSTALL_FAILURES[$((count - 1))]}" = "$label" ]; then
    return 0
  fi
  INSTALL_FAILURES+=("$label")
  return 0
}

# Print the failure summary, or nothing when no step failed. Labels go through
# printf %s, not print_*/echo -e, so a backslash in one is never interpreted.
report_install_failures() {
  local count=${#INSTALL_FAILURES[@]} label
  if [ "$count" -eq 0 ]; then
    return 0
  fi
  print_error "Installation finished with $count failed step(s):"
  for label in "${INSTALL_FAILURES[@]}"; do
    printf '  - %s\n' "$label"
  done
  print_info "Reasons are printed above as [WARNING] or [ERROR]. Fix the cause and re-run ./install.sh (finished steps are skipped)."
  return 0
}

# Help text. Dry-run previews only the steps that modify the user's own files;
# the text says outright that package installation is skipped, not simulated.
usage() {
  cat <<'EOF'
Dotfiles installation script.

Usage: ./install.sh [options]

Options:
  -n, --dry-run   Preview changes without touching the filesystem. Every
                  backup, symlink, and rendered-config action is printed with
                  its exact source and destination instead of being performed.
                  Package and tool installation (Homebrew / APT packages,
                  Node.js, editor plugins, AI CLIs, MCP registration) is NOT
                  simulated -- it is announced and skipped; the exact package
                  lists live in the install_* functions.
  -h, --help      Show this help and exit.

With no options install.sh symlinks the dotfiles into $HOME (backing up any
real files it replaces, and recording where any replaced symlink pointed),
installs packages for the detected OS (macOS / Ubuntu / WSL) -- Docker and
hadolint included -- and switches the default shell to zsh.

An install step that fails prints a warning and the run carries on. The failed
steps are listed at the end instead of "Installation completed!"; fix the cause
and re-run (steps that already finished are skipped).

Exit status:
  0  success, including a completed --dry-run preview and --help.
  1  one or more install steps failed (listed at the end of the run). Also
     exits 1 when run outside a dotfiles checkout or on an unsupported OS.
  2  usage error: an unknown option or unexpected argument (also a DRY_RUN
     environment value other than 0 or 1).
EOF
}

detect_os() {
  if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
  elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    # DEBIAN_VERSION_FILE はテストで分岐を検証するための差し込み口（既定は実パス）
    if [ -f "${DEBIAN_VERSION_FILE:-/etc/debian_version}" ]; then
      OS="ubuntu"
    else
      OS="linux"
    fi
  elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    # Note: WSL reports OSTYPE=linux-gnu, so it is always caught by the
    # linux-gnu branch above (and classified ubuntu/linux there) before
    # this branch would ever be reached. A `-n "$WSL_DISTRO_NAME"` check
    # here is therefore dead code -- do not reintroduce one; it would
    # silently reclassify existing WSL installs as "windows".
    OS="windows"
  else
    print_error "Unsupported operating system: $OSTYPE"
    exit 1
  fi
  print_info "Detected OS: $OS"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

# Fetch a remote install script to a temp file, then execute it -- instead of
# piping curl straight into a shell. A bare `curl ... | sh` executes bytes as
# they arrive, so a truncated download (connection dropped mid-transfer) runs a
# PARTIAL script; downloading in full first lets curl's exit status gate whether
# the script runs at all. stderr from curl and the script is left visible on
# purpose (no `2>/dev/null`) so a failed or tampered install surfaces instead of
# being silently swallowed.
# Usage: fetch_and_run <url> <interpreter> [interpreter-args...] [-- script-args...]
#   fetch_and_run https://astral.sh/uv/install.sh sh
#   fetch_and_run https://.../ohmyzsh/install.sh sh -- --unattended
# The interpreter is never sudo: the download sits in a temp file the invoking
# user can rewrite, so handing it to root would let anything running as that
# user swap what root executes (and no fetched script here warrants root).
# Packages that need root come from signed apt repositories instead (see
# _add_signed_apt_repo).
# Everything before an optional `--` is the interpreter and its own flags (they
# run BEFORE the downloaded script path); everything after `--` is handed to the
# script as positional args (AFTER the path), i.e. `sh <tmp> --unattended`. This
# lets installers that read `$1`-style flags (Oh My Zsh's --unattended) work the
# same as their canonical `sh -c "$(curl ...)" "" --unattended` invocation, where
# the flag is a positional parameter, not an option to the shell itself.
# When called in a guarded context (`|| print_warning`, or `if ...; then`) set -e
# is disabled inside the function, so the temp-file cleanup at the end always
# runs. When called bare, a failure propagates and aborts the script (set -e),
# matching the original unguarded `curl | bash` behavior.
fetch_and_run() {
  local url="$1"
  shift
  # Split the rest at an optional `--`: interpreter (+ its flags) on the left,
  # script positional args on the right. No `--` => all interpreter side.
  local -a interp=() script_args=()
  local seen_sep=0 arg
  for arg in "$@"; do
    if [ "$seen_sep" -eq 0 ] && [ "$arg" = "--" ]; then
      seen_sep=1
      continue
    fi
    if [ "$seen_sep" -eq 1 ]; then
      script_args+=("$arg")
    else
      interp+=("$arg")
    fi
  done
  case "${interp[0]:-}" in
  sudo | */sudo)
    print_warning "Refusing to run $url as root: fetch_and_run executes downloaded scripts as the invoking user only"
    return 1
    ;;
  esac
  local tmp status
  tmp="$(mktemp)"
  if ! curl -fsSL "$url" -o "$tmp"; then
    print_warning "Failed to download $url"
    rm -f "$tmp"
    return 1
  fi
  # A blank body can slip past `curl -f` (e.g. an empty 200 response); never
  # hand an empty script to a shell.
  if [ ! -s "$tmp" ]; then
    print_warning "Downloaded empty script from $url"
    rm -f "$tmp"
    return 1
  fi
  "${interp[@]}" "$tmp" "${script_args[@]}"
  status=$?
  rm -f "$tmp"
  return "$status"
}

# Install Homebrew (macOS)
install_homebrew() {
  if ! command_exists brew; then
    print_info "Installing Homebrew..."
    # Download-then-run (not `bash -c "$(curl ...)"`) so a truncated download
    # can't execute a partial installer -- see fetch_and_run's header.
    #
    # Guarded, so a transient network failure cannot abort main() under
    # `set -eo pipefail` and skip every later step. Unlike the other optional
    # installers it does NOT warn-and-continue: install_os_packages runs
    # install_brew_packages next and that needs `brew`, so continuing would
    # only buy a second wave of failures. Return non-zero and let the caller
    # skip the brew packages. Bail before the Apple-Silicon PATH block
    # (pointless with no brew to source) and before print_success (a lie).
    if ! fetch_and_run "https://raw.githubusercontent.com/Homebrew/install/$HOMEBREW_INSTALL_REF/install.sh" /bin/bash; then
      record_failure "Homebrew"
      print_warning "Homebrew bootstrap failed; skipping Homebrew packages"
      return 1
    fi

    # Add Homebrew to PATH for Apple Silicon
    if [[ -d "/opt/homebrew/bin" ]]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    print_success "Homebrew installed"
  else
    print_success "Homebrew already installed"
  fi
}

# Install APT packages (Ubuntu/Debian)
install_apt_packages() {
  print_info "Updating APT repositories..."
  # Guarded: a single unreachable repository must not abort the installer. The
  # apt-get install below reports what actually could not be fetched.
  sudo apt-get update || print_warning "apt-get update failed (continuing)"

  print_info "Installing required packages..."
  sudo apt-get install -y \
    git \
    zsh \
    vim \
    neovim \
    tmux \
    curl \
    wget \
    gnupg \
    ca-certificates \
    build-essential \
    xsel \
    fzf \
    jq \
    ripgrep \
    bat \
    fd-find \
    universal-ctags \
    libnotify-bin \
    golang-go \
    python3 \
    python3-pip \
    python3-venv \
    libssl-dev \
    zlib1g-dev \
    libbz2-dev \
    libreadline-dev \
    libsqlite3-dev \
    libffi-dev \
    liblzma-dev || {
    record_failure "APT packages"
    print_warning "Some APT packages failed to install (continuing)"
  }

  # On Debian/Ubuntu the binaries are named `batcat` and `fdfind`, but the
  # configs (.zshrc vf(), nvim telescope) invoke `bat` and `fd`. Provide
  # PATH-visible aliases so those code paths resolve.
  #
  # Never clobber a real file here: a user's own `fd` or `bat` wrapper in
  # ~/.local/bin must survive. The rule stays hand-rolled rather than using
  # backup_if_real: these aliases are generated artifacts that belong in
  # ~/.local/bin, not dotfiles worth moving into $backup_dir. A symlink is ours
  # to replace (the same rule backup_if_real applies); anything else belongs to
  # the user and wins.
  link_debian_alias() {
    local source_cmd="$1" alias_path="$HOME/.local/bin/$2"
    command_exists "$source_cmd" || return 0
    if [ -e "$alias_path" ] && [ ! -L "$alias_path" ]; then
      print_warning "$alias_path already exists and is not a symlink; leaving it alone (no $2 alias created)"
      return 0
    fi
    ln -sf "$(command -v "$source_cmd")" "$alias_path"
  }

  # ensure_dir, not a bare `mkdir -p`: a dangling symlink at ~/.local (or
  # ~/.local/bin) makes mkdir fail, and under `set -e` that ends the whole run
  # with no [ERROR] line and no failure summary -- before Node.js, the shell
  # setup or anything else after the package step. The parent is cleared first
  # for the same reason as ~/.config / ~/.config/git in _render_git_local_config.
  ensure_dir "$HOME/.local"
  ensure_dir "$HOME/.local/bin"
  link_debian_alias batcat bat
  link_debian_alias fdfind fd

  print_success "APT packages installed"

  # Last, so the caveat is the final word of this step rather than scrolling
  # away above the success line. Warning only -- see check_neovim_version.
  check_neovim_version
}

# Install Homebrew packages (macOS)
install_brew_packages() {
  print_info "Installing Homebrew packages..."

  packages=(
    git
    zsh
    vim
    neovim
    tmux
    curl
    wget
    fzf             # fuzzy finder (.zshrc shell integration via `fzf --zsh`, sshs/cf/vf)
    jq              # JSON processor (.claude/statusline-command.sh)
    ripgrep         # rg: vim :Find / nvim telescope live_grep
    bat             # vf() fzf preview
    fd              # nvim telescope find_files
    universal-ctags # vim tagbar (F4) / tag jump (Ctrl-t) via vim-gutentags
    make            # gmake for vimproc / treesitter compilation
    python          # python3 for pip-based linters and MCP server
    go              # ~/go/bin tools (goimports, staticcheck), .zshrc PATH
    pyenv           # Python version manager (install_pyenv covers Ubuntu only)
  )

  for package in "${packages[@]}"; do
    if brew list "$package" &>/dev/null; then
      print_info "$package already installed"
    else
      # Guarded like every other installer here: one formula that is renamed,
      # deprecated or momentarily unreachable must not abort the whole run.
      try_install "$package" brew install "$package"
    fi
  done

  print_success "Homebrew packages installed"
}

install_wezterm() {
  # Every brew/network/apt step below can fail transiently. WezTerm is
  # optional, so failures must warn and continue -- under `set -e` an
  # unguarded failure here aborts the whole installer (symlinks, MCP, ...).
  if [[ "$OS" == "macos" ]]; then
    if ! brew list --cask wezterm &>/dev/null; then
      print_info "Installing WezTerm..."
      try_install WezTerm brew install --cask wezterm
      if brew list --cask wezterm &>/dev/null; then
        print_success "WezTerm installed"
      fi
    else
      print_success "WezTerm already installed"
    fi
  elif [[ "$OS" == "ubuntu" ]]; then
    if ! command_exists wezterm; then
      print_info "Installing WezTerm..."
      if curl -fsSL https://apt.fury.io/wez/gpg.key | sudo gpg --yes --dearmor -o /usr/share/keyrings/wezterm-fury.gpg; then
        echo 'deb [signed-by=/usr/share/keyrings/wezterm-fury.gpg] https://apt.fury.io/wez/ * *' |
          sudo tee /etc/apt/sources.list.d/wezterm.list >/dev/null || {
          # Same label as the try_install below, so a repository that could not
          # be added and the install that then fails with it read as ONE failed
          # step: apt-get update records nothing in between, and an identical
          # consecutive label is dropped.
          record_failure "WezTerm"
          print_warning "Failed to add WezTerm apt repository"
        }
        sudo apt-get update || print_warning "apt-get update failed"
        try_install WezTerm sudo apt-get install -y wezterm
      else
        record_failure "WezTerm"
        print_warning "Failed to fetch WezTerm GPG key; skipping WezTerm"
      fi
      if command_exists wezterm; then
        print_success "WezTerm installed"
      fi
    else
      print_success "WezTerm already installed"
    fi
  fi
}

install_fonts() {
  # Fonts are optional: a failed brew/apt step warns and continues instead
  # of aborting the whole installer under `set -e`.
  local fonts_ok=true
  if [[ "$OS" == "macos" ]]; then
    print_info "Installing fonts..."
    # homebrew/cask-fonts was deprecated in 2024 and folded into homebrew/cask.
    brew install --cask font-ubuntu-mono ||
      {
        fonts_ok=false
        record_failure "font-ubuntu-mono"
        print_warning "Failed to install font-ubuntu-mono"
      }
    brew install --cask font-hack-nerd-font ||
      {
        fonts_ok=false
        record_failure "font-hack-nerd-font"
        print_warning "Failed to install font-hack-nerd-font"
      }
    if [[ "$fonts_ok" == true ]]; then
      print_success "Fonts installed"
    fi
  elif [[ "$OS" == "ubuntu" ]]; then
    print_info "Installing fonts..."
    if sudo apt-get install -y fonts-ubuntu fonts-hack; then
      print_success "Fonts installed"
    else
      record_failure "fonts-ubuntu fonts-hack"
      print_warning "Failed to install fonts"
    fi
  fi
}

# Whether git may be told to use `gh auth git-credential`. Two ways to qualify:
# this OS has an install path in install_gh (macos / ubuntu), or gh is already
# here by some other route. The second clause is not decoration -- detect_os
# also yields `linux` (Arch and Fedora ship gh in their own repositories) and
# `windows` (Git Bash + winget/scoop). Gating on the OS name alone would take gh
# away from those users and drop them onto the generic cache helper,
# re-prompting on every HTTPS operation.
#
# install_gh and _render_git_local_config must never disagree about this, so it
# lives here rather than being inlined twice. _render_git_local_config runs
# from create_symlinks, ahead of install_gh, so on a fresh macos/ubuntu the
# first clause is what answers.
gh_is_supported() {
  [[ "$OS" == "macos" || "$OS" == "ubuntu" ]] || command_exists gh
}

# Install GitHub CLI (gh)
# git's github.com credential helper is wired to `gh auth git-credential` by
# _render_git_local_config, but only where gh_is_supported.
install_gh() {
  if command_exists gh; then
    print_success "gh already installed"
    return
  fi
  if ! gh_is_supported; then
    # Warn rather than return silently: the credential helper is skipped in
    # step with this, and a user who expected gh needs to know which half of
    # the pair is missing.
    print_warning "No gh install path for OS=$OS; skipping (github.com credential helper not configured)"
    return
  fi
  print_info "Installing GitHub CLI (gh)..."
  if [[ "$OS" == "macos" ]]; then
    try_install gh brew install gh
  elif [[ "$OS" == "ubuntu" ]]; then
    # Same guard rationale as install_wezterm: gh is optional, so a failed
    # keyring/apt step must warn and continue, not abort the installer.
    if curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg |
      sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg; then
      # Record a half-done repository setup even when the apt install below
      # then succeeds (e.g. from the distro's own package), as install_wezterm
      # does: a broken keyring or sources entry is the user's to fix.
      sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg || {
        record_failure "gh"
        print_warning "Failed to chmod gh keyring"
      }
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" |
        sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null || {
        record_failure "gh"
        print_warning "Failed to add gh apt repository"
      }
      sudo apt-get update || print_warning "apt-get update failed"
      try_install gh sudo apt-get install -y gh
    else
      record_failure "gh"
      print_warning "Failed to fetch gh keyring; skipping gh"
    fi
  fi
  # Report success only when present. The `if` (no else) returns 0 when the
  # tool is absent, so a missing tool never aborts the installer under `set -e`
  # — a bare `cmd && print_success` would return non-zero and abort instead.
  if command_exists gh; then
    print_success "gh installed"
  fi
}

# Install uv / uvx (Astral) — required to launch the serena MCP server.
install_uv() {
  if command_exists uv; then
    print_success "uv already installed"
    return
  fi
  print_info "Installing uv (Astral)..."
  fetch_and_run https://astral.sh/uv/install.sh sh || {
    record_failure "uv"
    print_warning "Failed to install uv"
  }
  # uv installs to ~/.local/bin; make it visible for the rest of this script.
  export PATH="$HOME/.local/bin:$PATH"
  if command_exists uv; then
    print_success "uv installed"
  fi
}

# Install pyenv (macOS handled by brew packages; this covers Ubuntu).
install_pyenv() {
  if command_exists pyenv || [ -d "$HOME/.pyenv" ]; then
    print_success "pyenv already installed"
    return
  fi
  if [[ "$OS" == "ubuntu" ]]; then
    print_info "Installing pyenv..."
    fetch_and_run https://pyenv.run bash || {
      record_failure "pyenv"
      print_warning "Failed to install pyenv"
    }
    if command_exists pyenv || [ -d "$HOME/.pyenv" ]; then
      print_success "pyenv installed"
    fi
  fi
}

# Install glow — a terminal Markdown viewer.
install_glow() {
  if command_exists glow; then
    print_success "glow already installed"
    return
  fi
  print_info "Installing glow..."
  if [[ "$OS" == "macos" ]]; then
    try_install glow brew install glow
  elif [[ "$OS" == "ubuntu" ]]; then
    # Prefer go install (go is installed via apt) to avoid another apt repo.
    if command_exists go; then
      try_install "glow (go)" go install github.com/charmbracelet/glow@latest
      # go install places the binary under ~/go/bin, which isn't on PATH
      # until exported; without this the command_exists check just below
      # falsely reports glow as missing right after installing it.
      export PATH="$HOME/go/bin:$PATH"
    else
      print_warning "go not found; skipping glow"
    fi
  fi
  if command_exists glow; then
    print_success "glow installed"
  fi
}

# Install lazydocker — referenced by nvim toggleterm and editor keybindings.
install_lazydocker() {
  if command_exists lazydocker; then
    print_success "lazydocker already installed"
    return
  fi
  print_info "Installing lazydocker..."
  if [[ "$OS" == "macos" ]]; then
    try_install lazydocker brew install lazydocker
  elif [[ "$OS" == "ubuntu" ]]; then
    fetch_and_run "https://raw.githubusercontent.com/jesseduffield/lazydocker/$LAZYDOCKER_INSTALL_REF/scripts/install_update_linux.sh" bash || {
      record_failure "lazydocker"
      print_warning "Failed to install lazydocker"
    }
  fi
  if command_exists lazydocker; then
    print_success "lazydocker installed"
  fi
}

# --- Signed third-party apt repositories --------------------------------------
# Print the primary-key fingerprint of every key in keyring/key file $1, one per
# line (the `fpr` record right after each `pub`; a subkey's `fpr` follows a
# `sub` and is skipped). gpg runs in a throwaway GNUPGHOME so the check never
# creates or touches ~/.gnupg. Returns non-zero when gpg cannot read the file.
_keyring_primary_fprs() {
  local home rc=0
  home="$(mktemp -d)" || return 1
  GNUPGHOME="$home" gpg --batch --quiet --show-keys --with-colons "$1" 2>/dev/null |
    awk -F: '$1 == "pub" { want = 1; next } $1 == "fpr" && want { print $10; want = 0 }' ||
    rc=1
  rm -rf "$home"
  return "$rc"
}

# Add an apt repository that is trusted only through a keyring holding exactly
# one pinned key:
#   _add_signed_apt_repo <label> <key-url> <keyring> <fingerprint> <list-name> <sources-line>
# The key is written by root to <keyring>.new beside the keyring (`*.asc`
# stored as served, anything else dearmored), and THAT root-owned file is what
# gets checked -- never a user-owned temp copy that could be swapped between
# the check and root's read. It is made 0644 first (the check runs as the
# invoking user, and a strict umask leaves root's file unreadable to it), and
# must then hold one primary key whose fingerprint is <fingerprint>; only then
# does root rename it over <keyring> and write the sources entry. Any other
# content (another key, an extra key, no key), a key gpg cannot read, or a
# failed download, removes the .new file and leaves an existing keyring as it
# was. gpg itself must be installed (install_apt_packages puts gnupg there);
# without it nothing is fetched. The keyring and list names match what the
# vendors' own setup scripts
# used, so a rerun on a machine set up by get.docker.com / setup_lts.x
# replaces their files rather than adding a second Signed-By for the same URI
# (which apt rejects).
# Returns 0 once the repository is configured; otherwise warns and returns 1,
# and the CALLER records the failure under its own label, so a repository that
# could not be added and the install that is then skipped read as one step.
_add_signed_apt_repo() {
  local label="$1" key_url="$2" keyring="$3" want_fpr="$4" list="$5" line="$6"
  local staged="$keyring.new" fetched=0 fprs
  # The fingerprint check below needs gpg. The vendor scripts this replaced
  # (get.docker.com, setup_lts.x) installed gnupg themselves; install_apt_packages
  # does now, so this only trips when that step failed or was skipped, and then
  # it says so instead of failing later as "got: none".
  if ! command_exists gpg; then
    print_error "gpg is not installed (apt-get install gnupg); the $label signing key cannot be checked, so no repository was added"
    return 1
  fi
  if ! sudo install -d -m 0755 "$(dirname "$keyring")"; then
    print_warning "Could not create $(dirname "$keyring")"
    return 1
  fi
  case "$keyring" in
  *.asc) curl -fsSL "$key_url" | sudo tee "$staged" >/dev/null && fetched=1 ;;
  *) curl -fsSL "$key_url" | sudo gpg --batch --yes --dearmor -o "$staged" && fetched=1 ;;
  esac
  if [ "$fetched" -ne 1 ]; then
    print_warning "Failed to fetch the $label signing key ($key_url)"
    sudo rm -f "$staged" || true
    return 1
  fi
  # World-readable BEFORE the check: root wrote the file, and the check below
  # runs as the invoking user, whose gpg cannot read a 0600 file (a strict
  # umask makes root's files so). Safe to do first -- the file is root-owned
  # in a root-owned directory, so nobody else can swap it. apt reads keyrings as
  # the unprivileged _apt user, so it needs this mode in the end anyway.
  if ! sudo chmod 0644 "$staged"; then
    print_warning "Could not make the $label signing key readable ($staged)"
    sudo rm -f "$staged" || true
    return 1
  fi
  # A key gpg cannot read at all and a key with the wrong fingerprint are
  # different problems: say which one this is.
  if ! fprs="$(_keyring_primary_fprs "$staged")"; then
    print_error "Could not read the $label signing key with gpg ($staged); discarded it and added no repository"
    sudo rm -f "$staged" || true
    return 1
  fi
  if [ "$fprs" != "$want_fpr" ]; then
    print_error "The $label signing key fingerprint does not match the pinned one (got: $(printf '%s' "${fprs:-none}" | tr '\n' ' '); want: $want_fpr); discarded it and added no repository"
    sudo rm -f "$staged" || true
    return 1
  fi
  if ! sudo mv -f "$staged" "$keyring"; then
    print_warning "Could not put the $label signing key in place at $keyring"
    sudo rm -f "$staged" || true
    return 1
  fi
  if ! printf '%s\n' "$line" | sudo tee "/etc/apt/sources.list.d/$list" >/dev/null; then
    print_warning "Failed to add the $label apt repository"
    return 1
  fi
  sudo apt-get update || print_warning "apt-get update failed"
  return 0
}

# Print "<distro> <codename>" for download.docker.com (ubuntu / debian), from
# os-release; nothing when the release cannot be told. detect_os calls every
# Debian-family system "ubuntu", so Debian itself (and a derivative such as
# Mint, via ID_LIKE + UBUNTU_CODENAME) must be told apart here: Docker's
# repositories are per distribution. OS_RELEASE_FILE is the test hook, like
# DEBIAN_VERSION_FILE in detect_os.
_docker_apt_distro() {
  local file="${OS_RELEASE_FILE:-/etc/os-release}"
  [ -r "$file" ] || return 0
  (
    # shellcheck disable=SC1090
    . "$file"
    case "${ID:-}" in
    ubuntu) printf 'ubuntu %s\n' "${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}" ;;
    debian) printf 'debian %s\n' "${VERSION_CODENAME:-}" ;;
    *)
      case " ${ID_LIKE:-} " in
      *" ubuntu "*) printf 'ubuntu %s\n' "${UBUNTU_CODENAME:-}" ;;
      *" debian "*) printf 'debian %s\n' "${VERSION_CODENAME:-}" ;;
      esac
      ;;
    esac
  )
}

# Set up Docker's apt repository (returns 1 when it could not be configured).
_add_docker_apt_repo() {
  local distro="" codename="" arch
  read -r distro codename <<<"$(_docker_apt_distro || true)" || true
  if [ -z "$distro" ] || [ -z "$codename" ]; then
    print_warning "Could not tell the Debian/Ubuntu release from ${OS_RELEASE_FILE:-/etc/os-release}; Docker publishes repositories per distribution"
    return 1
  fi
  if ! arch="$(dpkg --print-architecture)"; then
    print_warning "dpkg --print-architecture failed"
    return 1
  fi
  _add_signed_apt_repo Docker "https://download.docker.com/linux/$distro/gpg" \
    /etc/apt/keyrings/docker.asc "$DOCKER_APT_KEY_FPR" docker.list \
    "deb [arch=$arch signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$distro $codename stable"
}

# Docker Desktop is the cask (`docker-desktop`; `docker` is its old name) or
# the app dragged into /Applications by hand. The CLI is NOT a signal: it stays
# off PATH until the app has been launched once. DOCKER_DESKTOP_APP is the
# test hook for the app path.
_docker_desktop_installed() {
  brew list --cask docker-desktop &>/dev/null ||
    brew list --cask docker &>/dev/null ||
    [ -d "${DOCKER_DESKTOP_APP:-/Applications/Docker.app}" ]
}

_install_docker_desktop_macos() {
  if _docker_desktop_installed; then
    print_success "Docker Desktop already installed"
  elif command_exists docker; then
    # OrbStack, Colima, Rancher Desktop, ... bring their own engine and CLI.
    print_info "docker CLI found at $(command -v docker) without Docker Desktop (OrbStack / Colima / ...?); not installing Docker Desktop"
    return 0
  else
    print_info "Installing Docker Desktop..."
    try_install "Docker Desktop" brew install --cask docker-desktop
    # A failure is recorded by try_install; nothing below applies then.
    _docker_desktop_installed || return 0
    print_success "Docker Desktop installed"
  fi
  if ! command_exists docker; then
    print_info "Launch Docker Desktop once (open -a Docker): that puts the docker CLI on PATH and starts the engine."
  fi
}

# Add the invoking user to the docker group, so docker runs without sudo.
# Called whenever an engine is present -- on a rerun too, so a membership that
# was never added (or was removed) is put back. The user comes from `id -un`,
# not $USER, which the environment can carry over from someone else (sudo -E,
# su without -l, a container). `id -nG <user>` reads the group database, so a
# membership added earlier counts even before a relogin.
_ensure_docker_group() {
  local user groups
  if ! user="$(id -un)" || [ -z "$user" ]; then
    print_warning "Could not tell the current user (id -un); not changing docker group membership"
    return 0
  fi
  if ! getent group docker >/dev/null 2>&1; then
    print_info "No docker group on this system (rootless Docker, or Docker Desktop's WSL integration); skipping group membership"
    return 0
  fi
  groups="$(id -nG "$user" 2>/dev/null || true)"
  case " $groups " in
  *" docker "*)
    print_info "$user is already in the docker group"
    return 0
    ;;
  esac
  if sudo usermod -aG docker "$user"; then
    print_success "Added $user to the docker group. Log out and back in (or run 'newgrp docker') for it to take effect."
  else
    record_failure "docker group"
    print_warning "Could not add $user to the docker group; run: sudo usermod -aG docker $user"
  fi
}

_install_docker_engine_ubuntu() {
  if command_exists docker; then
    print_success "Docker already installed"
  else
    print_info "Installing Docker Engine from Docker's signed apt repository..."
    if _add_docker_apt_repo; then
      try_install "Docker engine" sudo apt-get install -y \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    else
      record_failure "Docker engine"
      print_warning "Failed to install Docker engine (the repository could not be set up)"
    fi
    # Only an engine that is actually there gets a group membership.
    command_exists docker || return 0
    print_success "Docker installed"
  fi
  _ensure_docker_group
}

# Non-fatal reachability check, run after the install step. A daemon that is
# not up yet is the normal state right after an install (Docker Desktop never
# launched, a login session older than the docker group), so it is a warning
# and never a recorded failure: recording it would make every first run exit 1.
_check_docker_daemon() {
  command_exists docker || return 0
  if docker info >/dev/null 2>&1; then
    print_success "Docker daemon is reachable (docker info)"
    return 0
  fi
  local hint
  if [[ "$OS" == "macos" ]]; then
    hint="launch Docker Desktop (open -a Docker), or start your OrbStack / Colima VM"
  else
    hint="start it with 'sudo systemctl start docker'; if it is running, log out and back in (or 'newgrp docker') so the docker group applies"
  fi
  print_warning "docker info failed: the Docker daemon is not reachable yet ($hint). Not counted as a failed step."
}

# Install Docker -- the engine (Ubuntu/Debian) or Docker Desktop (macOS) -- then
# check that the daemon answers. Used by .zshrc's Docker CLI completions
# (fpath), lazydocker and the csb container sandbox CLI.
install_docker() {
  if [[ "$OS" == "macos" ]]; then
    _install_docker_desktop_macos
  elif [[ "$OS" == "ubuntu" ]]; then
    _install_docker_engine_ubuntu
  elif command_exists docker; then
    print_success "Docker already installed"
  else
    print_warning "No Docker install path for OS=$OS; install Docker by hand"
  fi
  _check_docker_daemon
}

# Print the sha256 of file $1 (sha256sum on Linux, shasum on macOS), or nothing.
_sha256_of() {
  if command_exists sha256sum; then
    sha256sum "$1" | awk '{ print $1 }'
  elif command_exists shasum; then
    shasum -a 256 "$1" | awk '{ print $1 }'
  fi
}

# Download the pinned hadolint release binary into a private temp dir, check
# its sha256, and install it as the user into ~/.local/bin. Nothing here runs
# as root, so no more privileged process ever reads the user-writable download.
# Returns 1 on a failure (after saying why), 2 when there is no release binary
# for this machine (a skip, not a failure).
_install_hadolint_release() {
  local machine asset want dir got
  machine="$(uname -m)"
  case "$machine" in
  x86_64 | amd64)
    asset="hadolint-linux-x86_64"
    want="$HADOLINT_SHA256_X86_64"
    ;;
  aarch64 | arm64)
    asset="hadolint-linux-arm64"
    want="$HADOLINT_SHA256_ARM64"
    ;;
  *)
    print_warning "No hadolint release binary for $machine; install it by hand (Dockerfile linting is skipped until then)"
    return 2
    ;;
  esac
  dir="$(mktemp -d)" || return 1
  if ! curl -fsSL "https://github.com/hadolint/hadolint/releases/download/v$HADOLINT_VERSION/$asset" -o "$dir/hadolint"; then
    print_warning "Failed to download hadolint $HADOLINT_VERSION ($asset)"
    rm -rf "$dir"
    return 1
  fi
  got="$(_sha256_of "$dir/hadolint" || true)"
  if [ "$got" != "$want" ]; then
    print_error "hadolint $HADOLINT_VERSION checksum mismatch (got: ${got:-none}; want: $want); not installed"
    rm -rf "$dir"
    return 1
  fi
  if ! ensure_dir "$HOME/.local" || ! ensure_dir "$HOME/.local/bin" ||
    ! install -m 0755 "$dir/hadolint" "$HOME/.local/bin/hadolint"; then
    print_warning "Could not install hadolint into $HOME/.local/bin"
    rm -rf "$dir"
    return 1
  fi
  rm -rf "$dir"
  export PATH="$HOME/.local/bin:$PATH"
  return 0
}

# Install hadolint -- the Dockerfile linter.
install_hadolint() {
  # ~/.local/bin is where _install_hadolint_release puts it, and a shell that
  # does not have that directory on PATH (the install.sh run itself, before
  # .zshrc is sourced) would otherwise download and verify it again every time.
  if command_exists hadolint || [ -x "$HOME/.local/bin/hadolint" ]; then
    print_success "hadolint already installed"
    return 0
  fi
  print_info "Installing hadolint..."
  if [[ "$OS" == "macos" ]]; then
    try_install hadolint brew install hadolint
  elif [[ "$OS" == "ubuntu" ]]; then
    local rc=0
    _install_hadolint_release || rc=$?
    if [ "$rc" -eq 1 ]; then
      record_failure "hadolint"
    fi
  else
    print_warning "No hadolint installer for OS=$OS; install it by hand (Dockerfile linting is skipped until then)"
    return 0
  fi
  if command_exists hadolint; then
    print_success "hadolint installed"
  fi
}

# nvim-treesitter (main branch) builds every parser with the tree-sitter CLI
# and needs this version or later. Its README rejects the npm package: npm's
# install script downloads the real binary, and when that step is blocked a
# shim that cannot run is left on PATH, where `command -v` still finds it.
TREE_SITTER_CLI_MIN_VERSION="0.26.1"

# True when dotted version $1 is at least $2 (numeric per field, so 0.26.10
# sorts above 0.26.9; missing fields count as 0).
version_at_least() {
  local -a have want
  local i h w
  IFS=. read -r -a have <<<"$1"
  IFS=. read -r -a want <<<"$2"
  for i in 0 1 2; do
    h="${have[i]:-0}"
    w="${want[i]:-0}"
    h="${h%%[!0-9]*}"
    w="${w%%[!0-9]*}"
    if ((10#${h:-0} > 10#${w:-0})); then return 0; fi
    if ((10#${h:-0} < 10#${w:-0})); then return 1; fi
  done
  return 0
}

# Print the version of a tree-sitter CLI that actually runs (e.g. 0.27.1).
# Prints nothing when it is missing or cannot run.
tree_sitter_cli_version() {
  command_exists tree-sitter || return 0
  # A shim that cannot run fails the pipeline; under `set -eo pipefail` that
  # would abort the caller's `have=$(...)`, so report "no version" instead.
  tree-sitter --version 2>/dev/null | awk 'NR == 1 { print $2 }' || true
}

# Install the tree-sitter CLI that nvim-treesitter builds parsers with.
install_tree_sitter_cli() {
  local have
  have="$(tree_sitter_cli_version)"
  if [ -n "$have" ] && version_at_least "$have" "$TREE_SITTER_CLI_MIN_VERSION"; then
    print_success "tree-sitter CLI $have already installed"
    return
  fi
  if [ -n "$have" ]; then
    print_warning "tree-sitter CLI $have is older than $TREE_SITTER_CLI_MIN_VERSION, which nvim-treesitter needs"
  elif command_exists tree-sitter; then
    print_warning "tree-sitter on PATH does not run (likely an npm shim); remove it with: npm uninstall -g tree-sitter-cli"
  fi
  print_info "Installing tree-sitter CLI..."
  if [[ "$OS" == "macos" ]]; then
    # `brew install` also upgrades the formula when it is already installed.
    try_install "tree-sitter CLI" brew install tree-sitter-cli
  elif [[ "$OS" == "ubuntu" ]]; then
    # apt's tree-sitter-cli is older than the minimum, so build it with cargo.
    if command_exists cargo; then
      try_install "tree-sitter CLI" cargo install --locked tree-sitter-cli
      export PATH="$HOME/.cargo/bin:$PATH"
    else
      print_warning "cargo not found; install tree-sitter CLI >= $TREE_SITTER_CLI_MIN_VERSION from https://github.com/tree-sitter/tree-sitter/releases (not npm)"
      return
    fi
  else
    print_warning "No tree-sitter CLI installer for $OS; install >= $TREE_SITTER_CLI_MIN_VERSION from https://github.com/tree-sitter/tree-sitter/releases (not npm)"
    return
  fi
  have="$(tree_sitter_cli_version)"
  if [ -n "$have" ] && version_at_least "$have" "$TREE_SITTER_CLI_MIN_VERSION"; then
    print_success "tree-sitter CLI $have installed"
  else
    # インストールが通っても、PATH の先にある古い CLI や動かない npm shim が
    # 新しいものを隠していることがある。黙って終えると nvim 側で初めて気づく。
    print_warning "tree-sitter on PATH is still not >= $TREE_SITTER_CLI_MIN_VERSION (found: ${have:-none that runs}); check PATH order (\`command -v tree-sitter\`) -- an npm shim may shadow it"
  fi
}

# This config needs Neovim 0.12 or later (INSTALL_PLATFORM.md: the `buf` option
# of vim.keymap.set, vim.lsp.config, ...). The `neovim` package apt installs on
# current Ubuntu LTS releases is older, and nothing else would say so: the
# install finishes "successfully" and the editor only fails when first opened.
NEOVIM_MIN_VERSION="0.12"

# Print the version of the nvim on PATH (e.g. 0.12.4 from `NVIM v0.12.4`).
# Prints nothing when nvim is missing, cannot run, or prints no version line.
neovim_version() {
  command_exists nvim || return 0
  # Same trap as tree_sitter_cli_version: a broken nvim fails the pipeline, and
  # under `set -eo pipefail` that would abort the caller's `have=$(...)`.
  nvim --version 2>/dev/null | awk 'NR == 1 { v = $2; sub(/^v/, "", v); print v }' || true
}

# Warn when the nvim on PATH is older than NEOVIM_MIN_VERSION. Called after the
# APT step, the only install path that cannot supply a new enough one (brew
# ships current releases).
#
# WARNING ONLY, deliberately: no record_failure. apt has no newer package to
# offer, so recording this would make every run on a stock Ubuntu LTS exit 1
# whatever the user does -- the failure summary would stop meaning "something
# you can fix by re-running". A missing nvim or an unreadable version warns
# too and carries on; this must never abort the installer, so it always
# returns 0 (install_apt_packages ends with this call, and a non-zero status
# there would trip `set -e` in the caller).
check_neovim_version() {
  local have
  local doc="INSTALL_PLATFORM.md#neovimのバージョンが古い"
  # Must look like a version before it reaches version_at_least, which turns
  # any non-numeric field into 0: `nvim` printing garbage would otherwise be
  # reported as "older than 0.12". The pattern sits in a variable because
  # bash 3.2 (macOS /bin/bash) and 5 disagree on quoting inside `=~`.
  local version_re='^[0-9]+\.[0-9]+'
  have="$(neovim_version)"
  if ! command_exists nvim; then
    print_warning "nvim not found; this config needs Neovim $NEOVIM_MIN_VERSION or later (see $doc)"
  elif [[ ! "$have" =~ $version_re ]]; then
    print_warning "Could not read the version from \`nvim --version\`; this config needs Neovim $NEOVIM_MIN_VERSION or later (see $doc)"
  elif version_at_least "$have" "$NEOVIM_MIN_VERSION"; then
    print_success "Neovim $have is new enough (needs $NEOVIM_MIN_VERSION or later)"
  else
    print_warning "Neovim $have is older than $NEOVIM_MIN_VERSION, which this config needs; see $doc"
  fi
  return 0
}

# Clear the way for a re-clone into a directory an INTERRUPTED `git clone`
# left behind, and report whether cloning may proceed (0 = go, 1 = skip).
#
# 通常の clone 失敗 (URL ミス、DNS 断、ネットワーク無し) は git 自身が後始末を
# する -- 作ったディレクトリを消してから抜けるので、呼び出し側のガードには何も
# 残らない。残るのは HARD な中断のときだけ: SIGKILL、OOM kill、fetch 中のスリープ。
# git clone は最初に .git を書くので、書きかけの .git だけを抱えたディレクトリが
# その場に残る。そしてその残骸こそが再実行を詰まらせる -- `git clone` は「存在
# していて空でない」ディレクトリへの書き込みを拒否するからだ。
#
# 消すのは "$dir/.git" ただ一つ、しかも find が「$dir の下にはそれしか無い」と
# 証明したときだけ。ここで扱う 3 つのプラグインはどれも実体のあるファイルを
# トップレベルに持つので、中身のあるディレクトリは中断された clone ではありえず、
# こちらが消してよいものでもない (install_oh_my_zsh が $ZSH に対して取るのと
# 同じ「すでに中身のあるディレクトリはユーザーのもの」という判断)。
# ディレクトリ自体は残す: git clone は「存在する空ディレクトリ」なら受け入れる
# ので、空にするだけで足りる。
#
# 修理できない / する必要がないものはすべて 1 を返す。呼び出し側はこれを「この
# プラグインは飛ばす」として扱うこと。main() は `set -eo pipefail` の下でこれらを
# 素で呼ぶので、致命的エラーとして伝播させると run 全体が落ちる。
reclaim_aborted_clone() {
  local dir="$1"
  local label="$2"

  # 何も邪魔していない: 新規インストール、あるいは git が自分で片付けた後。
  # `-L` も見るのは、壊れた symlink が `-e` を false にするため -- そのまま
  # clone に進むと git が EEXIST で死ぬ。
  if [ ! -e "$dir" ] && [ ! -L "$dir" ]; then
    return 0
  fi

  # Containment. 以下で消すのは必ず $dir 配下の 1 パスだけで、呼び出し側は
  # いずれも $HOME 直下のリテラルなプラグインパスを渡している。信用せず表明
  # しておくことで、将来の呼び出し側が明らかによそ (/etc/... 等) を指した場合に
  # 気付けるようにする。
  #
  # ただしこれは**文字列としての前置チェック**であって、物理パスの検査ではない。
  # 途中の要素が symlink (例: ~/.tmux がよそへのリンク) だと、この判定は通った
  # まま rm は物理的に $HOME の外へ着地する。実害が乗らないのは下の 2 つの検査
  # のおかげで、削除対象は「中身が実ディレクトリの .git ただ一つだけ」に限られる
  # -- 物理パスで検査したところで同じものを消す。この行に期待してよいのは
  # 「取り違えに気付く」ところまでで、境界を張っているのは下の検査である。
  if [ -z "$HOME" ] || [ "${dir#"$HOME"/}" = "$dir" ]; then
    record_failure "$label"
    print_warning "Refusing to reclaim $label: $dir is not under \$HOME"
    return 1
  fi

  # symlink はユーザーがそのプラグインを自分の置き場所に向けた印。`-d` は
  # symlink を「通して」true になるので、無防備な修理はリンク先の中身を空に
  # してしまう -- プラグインパスの外側を壊す唯一の経路。決して辿らない。
  if [ -L "$dir" ] || [ ! -d "$dir" ]; then
    record_failure "$label"
    print_warning "Skipping $label: $dir exists but is not a plain directory; remove it by hand and re-run ./install.sh"
    return 1
  fi

  # 手で空にされた、あるいは前回の修理のあと clone 自体が失敗した状態。
  # git clone は空ディレクトリならそのまま書けるので、消すものは何も無い。
  if [ -z "$(find "$dir" -mindepth 1 -maxdepth 1)" ]; then
    return 0
  fi

  # .git 以外が有るなら中断された clone ではない -- entry point だけを失った
  # checkout か、ユーザーが自分で置いたファイルか。どちらも自動修理の対象外だが、
  # 黙って飛ばすのはここで直している不具合そのものなので、必ず声に出す。
  # .git がディレクトリでない (gitfile / symlink) 場合も同じ扱い: worktree や
  # submodule のポインタを rm -rf する筋合いは無い。
  if [ -n "$(find "$dir" -mindepth 1 -maxdepth 1 ! -name '.git')" ] ||
    [ -L "$dir/.git" ] || [ ! -d "$dir/.git" ]; then
    record_failure "$label"
    print_warning "$label at $dir looks incomplete but is not an interrupted clone; inspect it, remove it by hand, then re-run ./install.sh"
    return 1
  fi

  # main() は dry-run でここまで到達しない (パッケージ / ツール導入ブロックごと
  # 飛ばされる) が、プレビュー実行に晒されうる `rm -rf` をリファクタ一回分の
  # 距離に置いておく理由も無い。明示的に断る。
  if [ "$DRY_RUN" -eq 1 ]; then
    print_info "[DRY-RUN] would remove the interrupted clone leftover $dir/.git and re-clone $label"
    return 1
  fi

  print_warning "$label was left half-cloned at $dir (interrupted clone); removing $dir/.git and cloning again"
  # rm の失敗を握り潰さないこと。素の `rm -rf` を最後の文にすると、その終了
  # ステータスがそのまま関数の戻り値になり、呼び出し側は「このプラグインは
  # 飛ばす」と読む -- 直前に「消して clone し直す」と表示した後で、黙って
  # 何もしないまま次へ進んでしまう。
  # 実際に起こりうる: sudo 配下で中断された clone や、復元したバックアップが
  # 残した root 所有・書き込み不可の .git。
  if ! rm -rf "${dir:?}/.git"; then
    record_failure "$label"
    print_warning "Could not remove $dir/.git; remove it by hand and re-run ./install.sh"
    return 1
  fi
}

# Install tpm (Tmux Plugin Manager) — .tmux.conf declares plugins via @plugin
# and runs ~/.tmux/plugins/tpm/tpm, but tpm does not bootstrap itself.
install_tmux_plugins() {
  # Test for tpm's ENTRY POINT, not the directory (install_oh_my_zsh が
  # oh-my-zsh.sh を見るのと同じ理由)。`-d` は中断された clone が残した
  # 「.git だけのディレクトリ」にも true を返すので、以降どの実行も
  # "tpm already installed" と言って再取得せず、.tmux.conf 末尾の
  # `run '~/.tmux/plugins/tpm/tpm'` が tmux 起動のたびに失敗し続ける。
  if [ -f "$HOME/.tmux/plugins/tpm/tpm" ]; then
    print_success "tpm already installed"
    return
  fi
  # `return 0` であって素の `return` ではない: main() は `set -eo pipefail` の
  # 下でこれを素で呼ぶので、素の `return` は直前に失敗したテストのステータスを
  # そのまま返して run 全体を落とす。
  reclaim_aborted_clone "$HOME/.tmux/plugins/tpm" tpm || return 0
  print_info "Installing tpm..."
  try_install tpm git clone https://github.com/tmux-plugins/tpm "$HOME/.tmux/plugins/tpm"
  print_info "Launch tmux and press prefix + I to install the declared plugins."
}

# Install a Python package with `pip install --user`, surviving PEP 668 and
# surfacing real failures.
#
# On externally-managed interpreters (Debian 12 / Ubuntu 23.04+ system Python)
# a plain `pip install --user` aborts with `externally-managed-environment`.
# We try the plain install first and only retry with --break-system-packages
# once pip reports that guard: applying the flag unconditionally would break
# older pip (< 23.0.1), which rejects the option -- and that older pip is
# exactly the one without the marker, so it never needs the retry. stderr is
# captured (fd shuttle: 3>&1 1>/dev/null 2>&3) instead of `2>/dev/null`-
# discarded, so a genuine failure (bad package, no network) prints its reason
# rather than a bare "Failed to install"; pip's own progress (stdout) stays
# quiet on success. Non-fatal by contract (warns, records the failure, returns
# 0) to match the optional-tool `|| print_warning` idiom the rest of the script
# uses under `set -e`. A retry that succeeds records nothing. --user keeps installs in ~/.local, never system site-packages;
# pipx would be the heavier "correct" route for CLI tools but needs its own
# bootstrap, out of scope here.
pip_install_user() {
  local pip_cmd="$1" pkg="$2" err
  if err="$("$pip_cmd" install --user "$pkg" 3>&1 1>/dev/null 2>&3)"; then
    return 0
  fi
  if printf '%s\n' "$err" | grep -q 'externally-managed-environment'; then
    print_info "System Python is externally managed; retrying $pkg into the user site..."
    if err="$("$pip_cmd" install --user --break-system-packages "$pkg" 3>&1 1>/dev/null 2>&3)"; then
      return 0
    fi
  fi
  print_warning "Failed to install $pkg"
  record_failure "$pkg"
  if [ -n "$err" ]; then
    printf '%s\n' "$err" | sed 's/^/    /'
  fi
  return 0
}

# Run a tool-install command, keeping success quiet but surfacing the reason on
# failure (non-fatal: warns, records the failure for main()'s end-of-run
# summary, and returns 0 -- never 1, or a bare caller would abort under
# `set -e`), so a failed toolchain/network
# install reports the installer's own error (npm ERR!, go's module error,
# apt's message, ...) instead of a bare "Failed to install". stderr is captured
# via the same fd shuttle as pip_install_user; the installer's stdout progress
# stays quiet on the happy path. Only mutating installs route through here --
# existence probes (`brew list`, `npm list -g`, `pip show`, ...) keep their own
# output-discarding redirect, since they only care about exit status. (pip
# installs use the dedicated pip_install_user, which additionally handles the
# PEP 668 retry.)
try_install() {
  local label="$1"
  shift
  local err
  if err="$("$@" 3>&1 1>/dev/null 2>&3)"; then
    return 0
  fi
  print_warning "Failed to install $label"
  record_failure "$label"
  if [ -n "$err" ]; then
    printf '%s\n' "$err" | sed 's/^/    /'
  fi
  return 0
}

# Install Python deps for the bundled MCP server (.claude/mcp-servers/gemini-consultant).
# server.py imports `mcp.server.fastmcp`, so the `mcp` package must be present.
# A missing package is installed at MCP_PY_VERSION; one that is already there
# (at whatever version) is left alone, since the user site may share it.
install_mcp_server_deps() {
  if command_exists pip3 || command_exists pip; then
    local pip_cmd="pip3"
    command_exists pip3 || pip_cmd="pip"
    if ! $pip_cmd show mcp &>/dev/null; then
      print_info "Installing Python 'mcp' $MCP_PY_VERSION for the gemini-consultant MCP server..."
      pip_install_user "$pip_cmd" "mcp==$MCP_PY_VERSION"
    else
      print_info "Python 'mcp' package already installed"
    fi
  else
    print_warning "pip not found; skipping MCP server deps"
  fi
}

# Print the value of the `  <field>: ` line of `claude mcp get` output $1 (the
# first such line), or nothing. A here-string, not a pipe: awk stops at the
# first match, and an early-exiting reader would fail the pipeline under
# pipefail.
_mcp_field() {
  awk -v key="  $2: " 'index($0, key) == 1 { print substr($0, length(key) + 1); exit }' <<<"$1"
}

# Write the `claude mcp get` output $2 to the file $1, owner-only from the
# start (umask 077, then chmod to be sure): it can hold an --api-key argument
# or an environment value. Creates the file's directory. Non-zero when any step
# fails. `&&` rather than `!`, which bash 3.2 can drop when a redirect fails.
_save_mcp_registration() {
  mkdir -p "$(dirname "$1")" &&
    (umask 077 && printf '%s\n' "$2" >"$1") &&
    chmod 0600 "$1"
}

# Register one MCP server in user scope, converging on the command given:
#   _add_mcp <name> -- <command> [args...]
# Not registered -> add it. Registered with exactly this command and these args
# -> leave it. Registered with anything else -> save the old registration
# (`claude mcp get` output, owner-only) to $backup_dir/mcp-<name>.txt, then
# remove and add it again, so a bumped pin, or an interpreter that has moved
# since an older run, is fixed by re-running ./install.sh rather than only
# warned about. The messages name the saved file and never print the old
# arguments, which can hold a key the user added; a registration that cannot be
# saved is left as it is (and recorded), and an add that fails after the
# remove says the server is no longer registered.
#
# `claude mcp get` prints `  Command: <cmd>` and `  Args: <args joined by
# spaces>`; both are compared to the expected values exactly (an argument with
# a space in it cannot be told apart from two, which only ever errs towards a
# needless re-registration). Never a substring match: /usr/bin/python3 is
# not /usr/bin/python3.9.
#
# Left alone, with a note: a remote (http / sse) registration, which is the
# user's own choice of transport. A mismatch registered in another scope
# (local / project) is a recorded failure: `claude mcp get` shows that entry
# ahead of the user-scope one, so replacing the user-scope entry would change
# nothing.
_add_mcp() {
  local name="$1"
  shift
  # "$@" is `-- <command> <args...>`, exactly as `claude mcp add` takes it.
  local want_cmd="${2:-}" want_args="${*:3}"
  local registered have_type have_cmd have_args have_scope
  if ! registered="$(claude mcp get "$name" 2>/dev/null)"; then
    print_info "Registering MCP server '$name'..."
    claude mcp add --scope user "$name" "$@" || {
      record_failure "MCP server $name"
      print_warning "Failed to register MCP server '$name'"
    }
    return 0
  fi
  have_type="$(_mcp_field "$registered" Type)"
  have_cmd="$(_mcp_field "$registered" Command)"
  have_args="$(_mcp_field "$registered" Args)"
  if [ "$have_cmd" = "$want_cmd" ] && [ "$have_args" = "$want_args" ]; then
    print_info "MCP server '$name' already registered"
    return 0
  fi
  if [ -n "$have_type" ] && [ "$have_type" != "stdio" ]; then
    print_info "MCP server '$name' is registered as a $have_type server; leaving it as is"
    return 0
  fi
  have_scope="$(_mcp_field "$registered" Scope)"
  case "$have_scope" in
  "User config"*) ;;
  *)
    # The old arguments stay out of the message (like the re-registration
    # below): they may carry a key the user added.
    record_failure "MCP server $name"
    print_warning "MCP server '$name' is registered in another scope (${have_scope:-unknown}) with a command or arguments other than '$want_cmd $want_args' (see: claude mcp get $name); remove it there (claude mcp remove $name) and re-run ./install.sh"
    return 0
    ;;
  esac
  # Keep what is about to be replaced: an argument or an environment value the
  # user added (context7's --api-key, say) is gone after the remove, and a
  # remove whose add then fails leaves the server unregistered. Nothing is
  # removed that was not saved first.
  [ -n "${backup_dir:-}" ] || backup_dir="$HOME/.dotfiles_backup_$(date +%Y%m%d_%H%M%S)"
  local saved="$backup_dir/mcp-$name.txt"
  if ! _save_mcp_registration "$saved" "$registered"; then
    record_failure "MCP server $name"
    print_warning "Could not save the current registration of MCP server '$name' to $saved; leaving it as it is (nothing was removed)"
    return 0
  fi
  # The path, not the old arguments: the file is owner-only because they can
  # hold a secret, and a terminal log is not.
  print_info "Re-registering MCP server '$name' (the old registration is saved in $saved)"
  if ! claude mcp remove --scope user "$name" >/dev/null; then
    record_failure "MCP server $name"
    print_warning "Failed to remove MCP server '$name' (the old registration is saved in $saved); fix it with: claude mcp remove --scope user $name && ./install.sh"
  elif ! claude mcp add --scope user "$name" "$@"; then
    record_failure "MCP server $name"
    print_warning "MCP server '$name' was removed but could not be added again, so it is NOT registered now; the old registration is saved in $saved. Re-run ./install.sh once the cause is fixed"
  else
    print_success "Re-registered MCP server '$name' (the old registration is saved in $saved)"
  fi
}

# Register MCP servers with Claude Code (user scope, idempotent and convergent:
# see _add_mcp). Server connections live in ~/.claude.json, which is NOT under
# dotfiles management, so re-register them here so a fresh machine matches the
# servers assumed by the skills and CLAUDE.md. The versions are the pins near
# the top of this file.
#
# Codex is deliberately absent: Codex CLI 0.154 removed its MCP-server mode,
# so a registration would only fail with CONNECTION_CLOSED on every start.
# Claude reaches Codex through `codex exec` (.claude/skills/codex-consultation).
# A registration left over from an older install is not removed here; drop it
# once with `claude mcp remove --scope user codex`.
register_claude_mcp_servers() {
  if ! command_exists claude; then
    print_warning "claude CLI not found; skipping MCP server registration"
    return
  fi

  _add_mcp context7 -- npx -y "@upstash/context7-mcp@$CONTEXT7_MCP_VERSION"
  # `claude-code` is the context serena renamed `ide-assistant` to (the old
  # name is still accepted, with a deprecation warning, as an alias of it).
  _add_mcp serena -- uvx --from "git+https://github.com/oraios/serena@$SERENA_REF" serena start-mcp-server --context claude-code

  # Bare `python` does not exist on stock Ubuntu or Homebrew installs
  # (only `python3`); resolve a concrete interpreter instead.
  local python_cmd python_exe
  python_cmd="$(command -v python3 || command -v python || true)"
  # Pin the concrete interpreter, not a version-manager shim. Under pyenv
  # `command -v python3` is ~/.pyenv/shims/python3, which picks the version
  # from the cwd's .python-version every time Claude Code starts the server:
  # in a project pinned to another version `mcp` is not installed there and
  # the server never starts. sys.executable is what the shim resolves to now,
  # the same interpreter install_mcp_server_deps' pip installed into. Fall back
  # to the resolved command when that cannot be read.
  if [ -n "$python_cmd" ]; then
    python_exe="$("$python_cmd" -c 'import sys; print(sys.executable)' 2>/dev/null || true)"
    case "$python_exe" in
    /*)
      if [ -f "$python_exe" ] && [ -x "$python_exe" ]; then
        python_cmd="$python_exe"
      fi
      ;;
    esac
  fi
  if [ -n "$python_cmd" ]; then
    # _add_mcp converges the registration on $python_cmd (an older run's pyenv
    # shim, or a since-removed version, is re-registered), so this is the
    # interpreter that has to import `mcp`. install_mcp_server_deps resolves
    # pip on its own, so it can have installed `mcp` for a different one.
    if ! "$python_cmd" -c 'import mcp.server.fastmcp' &>/dev/null; then
      print_warning "$python_cmd cannot import the 'mcp' package; gemini-consultant will not start until it is installed for that interpreter (e.g. $python_cmd -m pip install --user mcp==$MCP_PY_VERSION)"
      record_failure "gemini-consultant (mcp not importable)"
    fi
    _add_mcp gemini-consultant -- "$python_cmd" "$HOME/.claude/mcp-servers/gemini-consultant/server.py"
  else
    print_warning "python3/python not found; skipping gemini-consultant MCP server registration"
  fi
}

# Install Node.js and npm
install_nodejs() {
  if ! command_exists node; then
    print_info "Installing Node.js..."
    if [[ "$OS" == "macos" ]]; then
      try_install "Node.js" brew install node
    elif [[ "$OS" == "ubuntu" ]]; then
      # NodeSource's signed apt repository, set up the way its setup_lts.x
      # did (same keyring and list names) but without running that script as
      # root: the keyring must hold NODESOURCE_APT_KEY_FPR. Guarded, so
      # NodeSource being down must not abort the run.
      if _add_signed_apt_repo NodeSource \
        https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        /etc/apt/keyrings/nodesource.gpg "$NODESOURCE_APT_KEY_FPR" nodesource.list \
        "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODESOURCE_NODE_MAJOR.x nodistro main"; then
        try_install "Node.js" sudo apt-get install -y nodejs
      else
        record_failure "Node.js"
        print_warning "NodeSource setup failed; skipping Node.js (continuing)"
      fi
    fi
    command_exists node && print_success "Node.js installed"
  else
    print_success "Node.js already installed ($(node --version))"
  fi

  # Setup npm global directory. Guarded: everything above is best-effort --
  # brew/NodeSource failures only warn, and the windows branch never installs
  # node at all -- so npm is legitimately absent here. Unguarded, `npm config
  # set` exits 127 and `set -e` aborts the whole run, skipping every step
  # after this one (e.g. gh, pyenv, docker, linters, oh-my-zsh).
  if command_exists npm; then
    # ensure_dir, not a bare `mkdir -p`: a dangling symlink at ~/.npm-global
    # (an unmounted volume, a moved directory) fails mkdir, and under `set -e`
    # that ends the whole run with no [ERROR] line and no failure summary. A
    # symlink that resolves to a real directory is the user's redirect and is
    # kept.
    ensure_dir "$HOME/.npm-global"
    npm config set prefix "$HOME/.npm-global"
    # Ensure the npm-global bin is visible to the rest of this script so subsequent
    # `command_exists` checks and direct invocations of globally installed tools
    # (prettier, eslint, claude, etc.) resolve correctly. .zshrc already exports
    # this for interactive shells.
    export PATH="$HOME/.npm-global/bin:$PATH"
    print_success "npm global directory configured"
  else
    print_warning "npm not found; skipping npm global directory setup (continuing)"
  fi
}

# Install Oh My Zsh
install_oh_my_zsh() {
  # Test for the entry point, not the directory: a half-made $ZSH (a directory
  # with no oh-my-zsh.sh) must still read as "not installed", or a `-d` test
  # would report Oh My Zsh as already installed and skip it forever.
  if [ ! -f "$HOME/.oh-my-zsh/oh-my-zsh.sh" ]; then
    print_info "Installing Oh My Zsh..."
    # Download-then-run for the same truncation safety as Homebrew. `--unattended`
    # is a positional arg to the installer (keeps it from starting a shell or
    # running chsh -- install.sh handles the shell change separately), so it goes
    # after `--`, yielding `sh <tmp> --unattended --keep-zshrc`.
    #
    # `--keep-zshrc` is not optional here. main() runs create_symlinks BEFORE
    # this, so ~/.zshrc is already a symlink into the checkout by now, and Oh My
    # Zsh's setup_zshrc guards on `-f OR -h` -- a symlink counts as "an existing
    # zshrc". Without the flag it moves our link to ~/.zshrc.pre-oh-my-zsh and
    # installs its own template, and `--unattended` makes that silent: that flag
    # sets RUNZSH / CHSH / OVERWRITE_CONFIRMATION but leaves KEEP_ZSHRC at `no`,
    # so it suppresses the confirmation prompt without suppressing the clobber.
    # The install then reports success with none of this repo's zsh config live.
    # Dropping `--unattended` would not help either: Oh My Zsh sets
    # OVERWRITE_CONFIRMATION=no on its own whenever stdin is not a tty, which is
    # exactly how fetch_and_run invokes it. KEEP_ZSHRC is the only knob that
    # stops the clobber rather than merely silencing the prompt about it.
    #
    # Guarded like every other optional installer: a transient network failure
    # must not abort main() under `set -eo pipefail` and take vim-plug, tmux
    # plugins, the AI tools, the MCP registration, the theme symlink and the
    # shell change down with it, for a component none of them depend on.
    if fetch_and_run "https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/$OHMYZSH_INSTALL_REF/tools/install.sh" sh -- --unattended --keep-zshrc; then
      print_success "Oh My Zsh installed"
    else
      record_failure "Oh My Zsh"
      print_warning "Failed to install Oh My Zsh (continuing; the zsh plugins and theme below are skipped, not partially applied)"
    fi
  else
    print_success "Oh My Zsh already installed"
  fi

  # Everything below this point writes INTO $ZSH, so none of it may run when
  # the block above did not leave one. The `mkdir -p
  # "$HOME/.oh-my-zsh/custom"` and the plugin clones would create
  # $HOME/.oh-my-zsh as a real directory even on the failure branch, and Oh
  # My Zsh's own installer exits 1 when $ZSH already exists: a transient
  # network failure would become a permanent one (every later ./install.sh
  # never installs, while .zshrc's `source $ZSH/oh-my-zsh.sh` fails on every
  # login).
  #
  # Retested via the entry point rather than fetch_and_run's exit status, for
  # the same reason the "already installed?" test at the top of this function
  # uses it: it also rejects a half-made $ZSH (a directory with no
  # oh-my-zsh.sh), which is the state that must stay visible to the next run
  # as "not installed yet". That $ZSH is left in place, never deleted: a
  # directory that already has content is the user's to clear.
  #
  # `return 0`, never non-zero: main() calls this bare under `set -eo
  # pipefail`, so returning a failure here would abort the whole run -- the
  # exact outcome the guard on fetch_and_run above exists to prevent.
  # link_oh_my_zsh_theme carries the matching guard; main() calls it later, so
  # fixing only this side would let it recreate $ZSH anyway.
  if [ ! -f "$HOME/.oh-my-zsh/oh-my-zsh.sh" ]; then
    print_warning "Oh My Zsh is not installed; skipping the zsh plugins (continuing). Re-run ./install.sh once the network is back."
    return 0
  fi

  # Self-heal: a $HOME/.oh-my-zsh/custom symlinked straight to
  # $DOTFILES_DIR/.oh-my-zsh/custom (which only tracks themes/, no plugins/)
  # would lose cloned plugins on the first run and, on a second run, receive
  # new clones THROUGH the symlink into the dotfiles git checkout.
  # Ensure custom/ is a real directory before cloning anything into it.
  if [ -L "$HOME/.oh-my-zsh/custom" ]; then
    print_warning "Migrating $HOME/.oh-my-zsh/custom from a symlink to a real directory"
    rm -f "$HOME/.oh-my-zsh/custom"
  fi
  mkdir -p "$HOME/.oh-my-zsh/custom"

  # Install zsh plugins
  print_info "Installing zsh plugins..."

  # Guarded like the tpm clone: a failed plugin clone must not abort the run.
  #
  # どちらのテストもディレクトリではなく ENTRY POINT (.zshrc の plugins=() を
  # 経由して実際に source されるファイル) を見る。install_tmux_plugins と同じ
  # 理由で、中断された clone は .git だけを抱えたディレクトリを残し、`-d` は
  # それを「導入済み」と読んでしまう -- プラグインは二度と再取得されず、対話
  # シェルは毎回それ無しで起動する。
  # reclaim_aborted_clone はその残骸だけを (そしてそれだけを) 取り除いて再取得
  # を通す。断られたときはこのプラグインを飛ばして次へ進む -- ここは早期
  # return してはいけない。片方の残骸がもう片方の導入を巻き添えにする。
  # zsh-autosuggestions
  if [ ! -f "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh" ] &&
    reclaim_aborted_clone "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" zsh-autosuggestions; then
    git clone https://github.com/zsh-users/zsh-autosuggestions "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" || {
      record_failure "zsh-autosuggestions"
      print_warning "Failed to clone zsh-autosuggestions (continuing)"
    }
  fi

  # zsh-syntax-highlighting
  if [ ! -f "$HOME/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh" ] &&
    reclaim_aborted_clone "$HOME/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting" zsh-syntax-highlighting; then
    git clone https://github.com/zsh-users/zsh-syntax-highlighting "$HOME/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting" || {
      record_failure "zsh-syntax-highlighting"
      print_warning "Failed to clone zsh-syntax-highlighting (continuing)"
    }
  fi

  print_success "zsh plugins installed"
}

# Link the Oh My Zsh custom theme(s) tracked in the repo. Called from main()
# only AFTER install_oh_my_zsh, not from create_symlinks: `mkdir -p` of the
# themes directory would create $HOME/.oh-my-zsh before Oh My Zsh's own
# installer runs on a genuinely fresh machine, and that installer refuses to
# run when $ZSH already exists.
link_oh_my_zsh_theme() {
  # Refuse to create $ZSH from nothing. The `mkdir -p
  # "$HOME/.oh-my-zsh/custom/themes"` below creates $HOME/.oh-my-zsh itself
  # when it is absent, and Oh My Zsh's own installer exits 1 when $ZSH already
  # exists -- so on a run whose Oh My Zsh download failed, this function alone
  # is enough to make the failure permanent. install_oh_my_zsh carries the
  # matching guard; both are required, since main() reaches this one after it.
  #
  # Deliberately keyed on the DIRECTORY, where install_oh_my_zsh's guard is
  # keyed on the oh-my-zsh.sh entry point -- do not "simplify" the two into
  # one condition. All this one needs to know is whether the mkdir would
  # conjure $ZSH out of nothing; a machine whose $ZSH exists for any other
  # reason (a partial install, a hand-made dir) should still get its theme.
  #
  # Gated on DRY_RUN=0 so the preview is byte-identical: a dry run writes
  # nothing, hence can never materialise $ZSH, and it must keep listing the
  # theme it would link even on a machine with no Oh My Zsh yet.
  if [ "$DRY_RUN" -eq 0 ] && [ ! -d "$HOME/.oh-my-zsh" ]; then
    print_warning "Skipping the Oh My Zsh theme link: $HOME/.oh-my-zsh does not exist (continuing)"
    return 0
  fi

  # Oh My Zsh custom: keep a REAL directory (install_oh_my_zsh clones plugins
  # into custom/plugins/) and symlink only the theme file(s) tracked in the
  # repo. Symlinking the whole custom/ dir would destroy cloned plugins on
  # the first run and, on a rerun, clone new plugins straight into the
  # dotfiles git checkout (see the self-heal in install_oh_my_zsh).
  #
  # The replacement is decided once and both branches act on it. The real run
  # links each theme into a fresh, empty themes/ -- nothing to back up and
  # nothing resolving into the checkout. The preview replaces nothing, so
  # link_entry would look THROUGH the old link and misreport that plan: a
  # skip when it pointed into the checkout, a backup when it pointed at a
  # directory elsewhere.
  local custom_replaced=0 theme dest
  if [ -L "$HOME/.oh-my-zsh/custom" ]; then
    custom_replaced=1
    if [ "$DRY_RUN" -eq 1 ]; then
      print_info "[DRY-RUN] would replace the ~/.oh-my-zsh/custom symlink with a real directory"
    else
      rm -f "$HOME/.oh-my-zsh/custom"
    fi
  fi
  ensure_dir "$HOME/.oh-my-zsh/custom/themes"
  for theme in "$DOTFILES_DIR"/.oh-my-zsh/custom/themes/*; do
    [ -e "$theme" ] || continue
    dest="$HOME/.oh-my-zsh/custom/themes/$(basename "$theme")"
    if [ "$DRY_RUN" -eq 1 ] && [ "$custom_replaced" -eq 1 ]; then
      print_info "[DRY-RUN] would link $dest -> $theme"
      continue
    fi
    link_entry "$theme" "$dest"
  done
}

# Install vim-plug
# Neovim is managed by lazy.nvim (see .config/nvim/), so vim-plug is only
# needed for classic Vim (.vimrc).
install_vim_plug() {
  print_info "Installing vim-plug..."

  if [ ! -f "$HOME/.vim/autoload/plug.vim" ]; then
    # Guarded: a failed download must not abort the run. Report the real state
    # rather than printing success unconditionally.
    #
    # The rm is the load-bearing half. `curl -o` streams into its target, so a
    # connection dropped mid-transfer leaves a partial plug.vim rather than
    # nothing -- and a partial file satisfies both the existence check below
    # (which would print success over a corpse) and the `[ ! -f ]` guard above
    # (which would make every future run skip the download). Without this the
    # corruption is permanent: no rerun of install.sh can ever repair it.
    curl -fLo "$HOME/.vim/autoload/plug.vim" --create-dirs \
      "https://raw.githubusercontent.com/junegunn/vim-plug/$VIM_PLUG_REF/plug.vim" || {
      rm -f "$HOME/.vim/autoload/plug.vim"
      record_failure "vim-plug"
      print_warning "Failed to download vim-plug (continuing)"
    }
  fi

  if [ -f "$HOME/.vim/autoload/plug.vim" ]; then
    print_success "vim-plug installed"
  fi
}

# --- create_symlinks and its steps -------------------------------------------
# create_symlinks is an orchestrator over the steps below (dotfile linking, git
# credential/identity rendering, per-tool VS Code / Claude / Codex / Gemini /
# tmux wiring); each step keeps the comments that explain its own decisions.
#
# backup_if_real and link_entry are top-level, not nested inside
# create_symlinks, so the steps' dependency on them is explicit rather than a
# side effect of call order. Both read `backup_dir`, which create_symlinks
# assigns before invoking any step; it is deliberately global (not `local`)
# for exactly that reason.

# Print the directory under $backup_dir that holds the backup of $1. The path
# structure is preserved: a flat, basename-only backup silently overwrites files
# that share a basename across different destinations (settings.json /
# keybindings.json live under Code/ and .claude/), so all but the last one
# moved would be lost.
_backup_parent_dir() {
  local target="$1" rel
  case "$target" in
  "$HOME"/*) rel="${target#"$HOME"/}" ;;
  /*) rel="${target#/}" ;;
  *) rel="$target" ;;
  esac
  printf '%s\n' "$backup_dir/$(dirname "$rel")"
}

# Helper: clear the way for a link at $1. A real file/dir is moved into
# $backup_dir; a symlink is removed once its old target is recorded.
# Returns 0 when $1 is gone (or never existed), 1 when it could not be cleared
# -- the path is then left exactly as it was, and the caller must not link.
backup_if_real() {
  local target="$1"
  # A symlink (even a broken one) is ours to replace and has no content worth
  # moving, but where it pointed is worth keeping: a stow tree, an older
  # checkout, a hand-made redirect. One line per replaced link goes into
  # $backup_dir/symlinks.txt (`<path> -> <old target>`) before it is removed,
  # so the user can put it back.
  if [ -L "$target" ]; then
    local old
    # `|| true`: a message and a record, not a decision.
    old="$(readlink "$target" || true)"
    if [ "$DRY_RUN" -eq 1 ]; then
      print_info "[DRY-RUN] would replace existing symlink $target (was -> $old)"
      return 0
    fi
    # mkdir: create_symlinks removes an empty backup dir, and
    # link_oh_my_zsh_theme runs after that. Tested with `&&` rather than `!`,
    # which bash 3.2 can drop when a redirect fails (see
    # _render_git_local_config).
    if mkdir -p "$backup_dir" &&
      printf '%s -> %s\n' "$target" "$old" >>"$backup_dir/symlinks.txt" &&
      rm -f "$target"; then
      print_warning "Replacing symlink $target (was -> $old; recorded in $backup_dir/symlinks.txt)"
      return 0
    fi
    print_error "Could not record or remove the symlink $target (-> $old); leaving it in place"
    return 1
  fi
  # Nothing there: return success (a bare `return` would propagate the failed
  # test's exit status and abort the caller under `set -e`).
  [ -e "$target" ] || return 0
  local dest_parent
  dest_parent="$(_backup_parent_dir "$target")"
  if [ "$DRY_RUN" -eq 1 ]; then
    print_info "[DRY-RUN] would back up $target -> $dest_parent/"
    return 0
  fi
  # Name the destination every time: create_symlinks reports the backup dir
  # only for its own step, and link_oh_my_zsh_theme (called later from main)
  # can be the sole step that moves anything -- the user must still be told
  # where their file went.
  print_warning "Backing up existing $(basename "$target") -> $dest_parent/"
  # Checked, not bare: under `set -e` a failed mkdir/mv (a read-only HOME, a
  # backup dir on a full disk) used to end the run with no failure summary.
  if mkdir -p "$dest_parent" && mv "$target" "$dest_parent/"; then
    return 0
  fi
  print_error "Could not back up $target to $dest_parent/; leaving it in place"
  return 1
}

# Helper: back up $1 for a caller that replaces it with ONE `mv -f NEW $1`.
# backup_if_real moves the old file away first, so a mv that then fails leaves
# nothing at the path; here the old file is COPIED (cp -p) and stays put until
# the rename overwrites it atomically. A symlink is recorded in
# $backup_dir/symlinks.txt, as backup_if_real does, but not removed (rename(2)
# replaces the link itself). A directory, or a link to one, is handled exactly
# as backup_if_real does: `mv -f` would drop NEW into it, so it has to be moved
# aside. Returns 0 when $1 is ready to be overwritten (or is not there), 1 when
# it could not be backed up -- the path is then untouched, and the caller must
# not replace it.
backup_copy_if_real() {
  local target="$1"
  if [ -d "$target" ] || { [ ! -L "$target" ] && [ ! -f "$target" ]; }; then
    backup_if_real "$target"
    return
  fi
  local dest_parent
  dest_parent="$(_backup_parent_dir "$target")"
  if [ -L "$target" ]; then
    local old
    old="$(readlink "$target" || true)"
    if [ "$DRY_RUN" -eq 1 ]; then
      print_info "[DRY-RUN] would replace existing symlink $target (was -> $old)"
      return 0
    fi
    if mkdir -p "$backup_dir" &&
      printf '%s -> %s\n' "$target" "$old" >>"$backup_dir/symlinks.txt"; then
      print_warning "Replacing symlink $target (was -> $old; recorded in $backup_dir/symlinks.txt)"
      return 0
    fi
    print_error "Could not record the symlink $target (-> $old); leaving it in place"
    return 1
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    print_info "[DRY-RUN] would back up $target -> $dest_parent/"
    return 0
  fi
  print_warning "Backing up existing $(basename "$target") -> $dest_parent/"
  if mkdir -p "$dest_parent" && cp -p "$target" "$dest_parent/"; then
    return 0
  fi
  print_error "Could not back up $target to $dest_parent/; leaving it in place"
  return 1
}

# Helper: create a destination directory, replacing a dangling symlink first.
#
# On a stale symlink (an unmounted volume, a moved directory) `mkdir -p` fails
# with a bare "No such file or directory". In an OR-list such as
# `[ "$DRY_RUN" -eq 1 ] || mkdir -p "$dir"` that mkdir IS the command `set -e`
# watches, so the installer would die mid-run, before packages, MCP
# registration or chsh, with no [ERROR] line. backup_if_real already treats a
# symlink -- even a broken one -- as ours to replace; a symlink that resolves
# to a real directory elsewhere is the user's redirect and is kept.
#
# The dry run touches nothing but still announces that replacement, so the
# preview of a machine with a dangling ~/.gemini (or ~/.claude, ...) says what
# the real run will do to it.
ensure_dir() {
  if [ -L "$1" ] && [ ! -e "$1" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      print_info "[DRY-RUN] would replace dangling symlink $1 with a real directory"
      return 0
    fi
    print_warning "Replacing dangling symlink $1 with a real directory"
    rm -f "$1"
  fi
  [ "$DRY_RUN" -eq 1 ] && return 0
  mkdir -p "$1"
}

# Helper: symlink a repo entry into a destination directory, backing up reals.
# A source missing from the checkout is skipped (see link_path for a link whose
# target may legitimately not exist yet).
link_entry() {
  local src="$1"
  local dest="$2"
  if [ ! -e "$src" ]; then
    print_warning "Skipping missing source: $src"
    return
  fi
  link_path "$src" "$dest"
}

# Helper: make $2 a symlink to $1, whether or not $1 exists yet.
#
# A link that already points at $1 (the same readlink text) is left alone --
# not removed and re-created on every run, which left a window with no link at
# all and churned its mtime. Anything else in the way goes through
# backup_if_real first: a real file is moved to the backup dir, another link
# has its old target recorded there. A failure to clear the way or to create
# the link is a recorded failure, never a bare abort under `set -e` (which used
# to end the run with no failed-step summary).
link_path() {
  local src="$1"
  local dest="$2"
  # Refuse to write through a parent that already resolves INTO the checkout
  # (`ln -s ~/dotfiles/.claude ~/.claude` is a common pre-existing layout).
  # ensure_dir / mkdir -p no-op on such a symlink, so dest resolves back onto
  # src itself: backup_if_real would then MOVE the repo's own entry into the
  # backup dir and `ln -sf` would leave a self-referential link in the working
  # tree -- while reporting success. install_oh_my_zsh carries the same
  # self-heal for ~/.oh-my-zsh/custom; this is the general form.
  #
  # Identity, not spelling: `-ef` asks whether the two parents are the same
  # directory (device + inode). A `pwd -P` string compare is not enough: bash
  # resolves symlinks textually and never canonicalises case, so on a
  # case-insensitive filesystem (the macOS default) a link to `.../Dotfiles`
  # and an installer run from `.../dotfiles` are two strings for one
  # directory. The parents are compared rather than dest itself: an existing
  # dest link to src (every re-run) must reach the already-linked check below,
  # and `-ef` on dest would follow it and match. A missing dest parent makes
  # `-ef` false, which is correct -- nothing resolves there.
  if [ "$(dirname "$dest")" -ef "$(dirname "$src")" ] &&
    [ "$(basename "$dest")" = "$(basename "$src")" ]; then
    print_warning "Skipping $dest: it already resolves into the checkout ($src)"
    return 0
  fi
  if [ -L "$dest" ] && [ "$(readlink "$dest" || true)" = "$src" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      print_info "[DRY-RUN] already linked $dest -> $src"
    else
      print_info "Already linked $(basename "$dest")"
    fi
    return 0
  fi
  if ! backup_if_real "$dest"; then
    record_failure "link $dest"
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    print_info "[DRY-RUN] would link $dest -> $src"
    return 0
  fi
  # -n: never descend into an existing link to a directory. backup_if_real has
  # just cleared dest, so this only matters if something reappeared there.
  if ln -sfn "$src" "$dest"; then
    print_success "Linked $(basename "$dest")"
  else
    print_error "Could not link $dest -> $src"
    record_failure "link $dest"
  fi
}

_link_top_level_dotfiles() {
  files=(
    ".zshrc"
    ".vimrc"
    ".tmux.conf"
    ".gitconfig"
    ".gitignore_global"
    ".wezterm.lua"
  )

  for file in "${files[@]}"; do
    link_entry "$DOTFILES_DIR/$file" "$HOME/$file"
  done
}

_seed_zsh_secrets() {
  # ~/.zsh_secrets is the only place secrets belong once ~/.zshrc is a symlink
  # into the checkout: an `export API_KEY=...` added to .zshrc itself is edited
  # inside the working tree and is one `git add` away from being committed. The
  # last line of .zshrc sources this file when present, so it lives in $HOME and
  # is never tracked -- the same separation ~/.codex/config.toml gets by not
  # being linked, and ~/.config/git/user.gitconfig by being rendered per machine.
  #
  # Seeded only when absent, like those two: this file is the user's, and a
  # re-run must never clobber the keys already in it. The commented-out exports
  # are just a starting point, so an empty stub is still a success.
  local secrets="$HOME/.zsh_secrets"
  # A symlink here is the user's redirect (an encrypted volume, a synced
  # folder). `[ -e ]` is false once its target is gone, so it reads as absent
  # and a seed would write THROUGH it: a stub planted where the real keys
  # belong or, with the target's directory gone too, a bare ENOENT under set -e
  # that kills the installer with no [ERROR] line. Never write through it;
  # ahead of the dry-run branch so the preview reports the same decision.
  if [ -L "$secrets" ] && [ ! -e "$secrets" ]; then
    local dry_prefix=""
    [ "$DRY_RUN" -eq 1 ] && dry_prefix="[DRY-RUN] "
    print_warning "${dry_prefix}Leaving $secrets alone: it is a dangling symlink (its target is missing)"
    return 0
  fi
  # Dry-run previews the decision it would actually make, not just the write:
  # a bare "would create" on a machine that already has the file would misreport
  # the plan.
  if [ "$DRY_RUN" -eq 1 ]; then
    if [ -e "$secrets" ]; then
      print_info "[DRY-RUN] would keep existing $secrets"
    else
      print_info "[DRY-RUN] would create $secrets (mode 600) with commented-out example exports"
    fi
    return 0
  fi
  if [ -e "$secrets" ]; then
    print_info "Keeping existing $secrets"
    return 0
  fi
  # umask in a subshell, not a chmod afterwards: the file must never exist
  # world-readable, not even for the instant between creation and chmod.
  (
    umask 077
    cat >"$secrets" <<'EOF'
# Secrets sourced by ~/.zshrc. Keep this file OUT of the dotfiles checkout --
# ~/.zshrc is a symlink into it, so anything written there is tracked by git.
# export GEMINI_API_KEY=...
# export GITHUB_ACCESS_TOKEN=...
EOF
  )
  print_success "Created $secrets (fill in your API keys)"
}

_render_git_local_config() {
  # Captured by create_symlinks BEFORE the .gitconfig link replaces whatever
  # identity git currently resolves.
  local prior_git_name="$1" prior_git_email="$2"

  # OS 別の資格情報ヘルパーを ~/.config/git/os.gitconfig に生成する。
  # .gitconfig は [include] でこれを読み込む。macOS は osxkeychain、それ以外は
  # cache を使い、credential-osxkeychain が存在しない Linux/WSL で
  # 「is not a git command」警告が出るのを防ぐ。生成ファイルなのでバックアップ
  # 不要 (毎回上書きで冪等)。git は未生成でも include を黙って無視する。
  local git_cred_helper="cache --timeout=3600"
  [[ "$OS" == "macos" ]] && git_cred_helper="osxkeychain"
  # Identity lives here rather than in the tracked .gitconfig, which would make
  # everyone who clones this repo commit under the owner's name and address.
  # Never overwrite: this file is the user's, and a re-run must not clobber it.
  local git_user_config="$HOME/.config/git/user.gitconfig"
  local os_gitconfig="$HOME/.config/git/os.gitconfig"
  local dry_prefix=""
  [ "$DRY_RUN" -eq 1 ] && dry_prefix="[DRY-RUN] "

  # Per-machine files, and the user's real name/email: if ~/.config itself
  # resolves into the checkout (the layout link_entry guards against), they
  # would land in the working tree as untracked files.
  # `-ef` (same directory), not a `pwd -P` string compare -- see link_entry
  # for why.
  # Ahead of the dry-run branch so the preview and the real run agree.
  if [ "$HOME/.config" -ef "$DOTFILES_DIR/.config" ]; then
    print_warning "${dry_prefix}Skipping git config render: $HOME/.config resolves into the checkout"
    return 0
  fi

  # os.gitconfig is generated and rewritten on every run, so a symlink there
  # -- live or broken -- is ours to replace (backup_if_real's rule). The
  # dangling ~/.zsh_secrets / user.gitconfig redirects are left alone because
  # they hold the user's own keys and identity; nothing of the user's lives
  # in this file. Writing THROUGH the link is the one thing not to do, whether
  # it is stale or not: with its target's directory gone the redirect below
  # failed, and with the target present the render overwrote whatever the link
  # pointed at, with no backup. `rm -f` on a symlink removes the link itself
  # and never follows it, so the target is left exactly as it was.
  #
  # The `-e` test only picks the wording: it follows the link, so it is false
  # exactly when the link is dangling. It must not gate the replacement, which
  # is what used to let a live link through.
  if [ -L "$os_gitconfig" ]; then
    local link_kind="symlink" link_dest
    [ -e "$os_gitconfig" ] || link_kind="dangling symlink"
    # `|| true`: the assignment's status is readlink's, and this is a message,
    # not a decision -- it must not abort the installer under `set -e`.
    link_dest="$(readlink "$os_gitconfig" || true)"
    if [ "$DRY_RUN" -eq 1 ]; then
      print_warning "[DRY-RUN] would replace $link_kind $os_gitconfig (-> $link_dest) with the generated file; the link's target is not written to"
    else
      print_warning "Replacing $link_kind $os_gitconfig (-> $link_dest) with the generated file; the link's target is not written to"
      rm -f "$os_gitconfig"
    fi
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    # Read-only preview of the same decisions the real branch makes below --
    # never prompt (dry-run must not block on input) and never write.
    if gh_is_supported; then
      print_info "[DRY-RUN] would render $os_gitconfig (credential helper: $git_cred_helper, github.com: gh)"
    else
      print_info "[DRY-RUN] would render $os_gitconfig (credential helper: $git_cred_helper; no gh on OS=$OS)"
    fi
    if [ -e "$git_user_config" ]; then
      print_info "[DRY-RUN] would keep existing git identity ($git_user_config)"
    elif [ -L "$git_user_config" ]; then
      print_warning "[DRY-RUN] would leave $git_user_config alone: it is a dangling symlink (its target is missing)"
    elif [ -n "$prior_git_name" ] && [ -n "$prior_git_email" ]; then
      print_info "[DRY-RUN] would render $git_user_config inheriting $prior_git_name <$prior_git_email>"
    else
      print_info "[DRY-RUN] would prompt for a git identity (interactive) or write a commented-out placeholder at $git_user_config"
    fi
  else
    local gh_host
    # ~/.config itself may be a dangling symlink (see ensure_dir); this is the
    # first write under it, ahead of _link_editor_configs. So may ~/.config/git,
    # where a bare `mkdir -p` would die with no [ERROR] line.
    #
    # Failures here are recorded, not fatal: create_symlinks calls this bare
    # under `set -e`, and a non-zero return used to stop the whole run half-way
    # (the editor / Claude / Codex / Gemini links and every package step
    # skipped) over one unwritable file -- against main()'s "symlinks first".
    # main's failure summary and exit status still carry it.
    if ! ensure_dir "$HOME/.config" || ! ensure_dir "$HOME/.config/git"; then
      print_error "Could not create $HOME/.config/git: git has no credential helper or identity file until it is fixed and ./install.sh is re-run"
      record_failure "git config (os.gitconfig)"
      return 0
    fi
    # The github blocks are emitted BEFORE the generic one, and the order is
    # behaviour rather than taste: their `helper =` resets the helper list
    # accumulated so far, so a generic helper written above them would be
    # discarded for github URLs. Written in this order git resolves
    # github.com to [gh, <generic>] -- gh first, the keychain/cache behind it.
    #
    # The write is checked with a plain `if`. A bare `{ ... } >file` fired no
    # errexit on macOS /bin/bash 3.2 when the redirect failed, so the
    # print_success after it ran over a file that was never written; and
    # `if ! { ... } >file` is no fix, since 3.2 drops the `!` in exactly that
    # case. Do not "simplify" this back to either form.
    if {
      if gh_is_supported; then
        for gh_host in github.com gist.github.com; do
          printf '[credential "https://%s"]\n\thelper =\n\thelper = !gh auth git-credential\n' \
            "$gh_host"
        done
      fi
      printf '[credential]\n\thelper = %s\n' "$git_cred_helper"
    } >"$os_gitconfig"; then
      if gh_is_supported; then
        print_success "Rendered os.gitconfig (credential helper: $git_cred_helper, github.com: gh)"
      else
        print_success "Rendered os.gitconfig (credential helper: $git_cred_helper; no gh on OS=$OS)"
      fi
    else
      print_error "Could not write $os_gitconfig: git has no credential helper until it is fixed and ./install.sh is re-run"
      record_failure "git config (os.gitconfig)"
    fi

    if [ -e "$git_user_config" ]; then
      print_info "Keeping existing git identity ($git_user_config)"
    elif [ -L "$git_user_config" ]; then
      # Same trap as the dangling ~/.zsh_secrets in _seed_zsh_secrets: `[ -e ]`
      # is false on a broken link, and both writers below then fail on it
      # (git cannot take its lock) under set -e. The redirect is the user's.
      print_warning "Leaving $git_user_config alone: it is a dangling symlink (its target is missing)"
    else
      local git_name="$prior_git_name" git_email="$prior_git_email"
      # Nothing to inherit (fresh machine, or an upgrade from the version that
      # kept [user] in the tracked file): ask, but only with a TTY -- a bare
      # `read` at EOF returns non-zero and would abort the installer under set -e.
      if [ -z "$git_name" ] || [ -z "$git_email" ]; then
        if [ -t 0 ]; then
          [ -z "$git_name" ] && { read -p "Git user.name: " -r git_name || git_name=""; }
          [ -z "$git_email" ] && { read -p "Git user.email: " -r git_email || git_email=""; }
        fi
      fi
      if [ -n "$git_name" ] && [ -n "$git_email" ]; then
        # Written through git itself, not printf: a raw value is corrupted by
        # `#` / `;` (comment start), `"` (swallowed) or a trailing backslash
        # (line continuation -- the name then absorbed the email line and
        # user.email was left unset, so git refused to commit).
        #
        # Checked like the os.gitconfig write above: a read-only ~/.config/git
        # fails both, and a bare failure here would abort the run under set -e.
        if git config --file "$git_user_config" user.name "$git_name" &&
          git config --file "$git_user_config" user.email "$git_email"; then
          print_success "Rendered user.gitconfig ($git_name <$git_email>)"
        else
          # A name without an email would be kept as "existing identity" by
          # every later run; drop the partial file so the re-run writes it.
          rm -f "$git_user_config"
          print_error "Could not write $git_user_config: git has no identity until it is fixed and ./install.sh is re-run"
          record_failure "git config (user.gitconfig)"
        fi
      else
        # Commented-out keys, not empty ones: an empty `name =` makes git report
        # a configured-but-blank identity instead of prompting the user to set it.
        if printf '# Fill in before committing:\n#[user]\n#\tname = Your Name\n#\temail = you@example.com\n' \
          >"$git_user_config"; then
          print_warning "No git identity configured. Edit $git_user_config before committing."
        else
          rm -f "$git_user_config"
          print_error "Could not write $git_user_config"
          record_failure "git config (user.gitconfig)"
        fi
      fi
    fi
  fi
}

_link_editor_configs() {
  # Recorded, not fatal (see _render_git_local_config): an unusable ~/.config
  # would otherwise abort the run here, before the Claude / Codex / Gemini links.
  if ! ensure_dir "$HOME/.config"; then
    print_error "Could not create $HOME/.config: the Neovim and VS Code links are skipped until it is fixed"
    record_failure "editor configs"
    return 0
  fi

  link_entry "$DOTFILES_DIR/.config/nvim" "$HOME/.config/nvim"

  # VS Code: reads user settings from an OS-specific location (not
  # $HOME/.config on macOS). Symlink individual files (not the whole User/
  # dir) so editor runtime state (globalStorage, workspaceStorage, etc.)
  # never ends up in the repo.
  #
  # Three locations. detect_os yields "windows" for msys/cygwin (Git Bash),
  # where VS Code reads %APPDATA%\Code\User; links under $HOME/.config/Code/User
  # would be created and silently have no effect there. APPDATA is a
  # Windows-only variable, so it cannot mislead the other two branches.
  #
  # Falls back to the Linux path when APPDATA is unset (a bare msys shell rather
  # than Git Bash). `-u` is intentionally not set here (see the note above
  # `set -eo pipefail`), so `${APPDATA:-}` isn't guarding against a crash -- it
  # just makes the unset case explicit, matching how DRY_RUN and
  # DEBIAN_VERSION_FILE are guarded elsewhere in this script.
  local vscode_user_dir
  if [[ "$OS" == "macos" ]]; then
    vscode_user_dir="$HOME/Library/Application Support/Code/User"
  elif [[ "$OS" == "windows" && -n "${APPDATA:-}" ]]; then
    vscode_user_dir="$APPDATA/Code/User"
  else
    vscode_user_dir="$HOME/.config/Code/User"
  fi
  if ! ensure_dir "$vscode_user_dir"; then
    print_error "Could not create $vscode_user_dir: the VS Code links are skipped until it is fixed"
    record_failure "VS Code config links"
    return 0
  fi

  local editor_config_files=(
    "settings.json"
    "keybindings.json"
  )
  for entry in "${editor_config_files[@]}"; do
    link_entry "$DOTFILES_DIR/.config/Code/User/$entry" "$vscode_user_dir/$entry"
  done
}

# Print .codex/hooks.json.template with this machine's $HOME in place of
# __HOME__. The single renderer for both of _link_codex_config's branches.
#
# $HOME lands in three nested languages at once, so it is escaped innermost
# first -- sed runs its -e expressions in order on each line, so one pass
# applies the layers in sequence:
#   1. shell: every __HOME__ opens a single-quoted word ('__HOME__/...'; a
#      test pins that), where only `'` is special. It becomes '\'' (close,
#      escaped quote, reopen): an unescaped /home/o'brien closes the quote in
#      every hook command.
#   2. JSON: that word sits in a JSON string, so `\` -- including the one
#      step 1 just added -- becomes `\\`, and `"` becomes `\"`. Either one
#      raw made the whole file unparseable and Codex loaded no hooks.
#   3. sed: the result is a sed REPLACEMENT, where `&` means "the matched
#      text" and `|` closes the s||| expression: /Users/a&b rendered as the
#      nonexistent /Users/a__HOME__b without a warning, and /home/a|b made
#      sed fail under set -e. Escape both, and the backslash that escapes
#      them.
# Control characters in $HOME (a newline, a tab) are not handled.
_render_codex_hooks_json() {
  local home_esc
  home_esc="$(printf '%s\n' "$HOME" | sed \
    -e "s/'/'\\\\''/g" \
    -e 's/\\/\\\\/g' \
    -e 's/"/\\"/g' \
    -e 's/[\\&|]/\\&/g')"
  sed "s|__HOME__|$home_esc|g" "$DOTFILES_DIR/.codex/hooks.json.template"
}

_link_claude_config() {
  # Claude Code config: symlink individual entries so CLI runtime data
  # (projects/, sessions/, history.jsonl, backups/, etc.) stays out of the repo.
  # Recorded, not fatal: a ~/.claude that cannot be a directory (a file in its
  # place, a read-only parent) used to end the whole run here under `set -e`.
  if ! ensure_dir "$HOME/.claude"; then
    print_error "Could not create $HOME/.claude: the Claude Code links are skipped until it is fixed"
    record_failure "Claude config links"
    return 0
  fi
  local claude_entries=(
    "CLAUDE.md"
    "settings.json"
    "statusline-command.sh"
    "agents"
    "commands"
    "hooks"
    "mcp-servers"
    "mods"
    "rules"
    "skills"
  )
  # `[ -e ] && link_entry` ではなく if/fi なのは、`&&` 形だと最後の要素が
  # 欠けたときに for ループの終了ステータスが 1 になり、それが関数の最終
  # コマンドである以上 関数自体が 1 を返すため。`set -eo pipefail` の下では
  # そこでスクリプトごと落ち、main() は何のメッセージも出さずに exit 1 する
  # (欠落を許容するための [ -e ] ガードが、最後の 1 要素にだけ効かなかった)。
  # if/fi なら link_entry 本体の失敗はこれまで通り伝播する。
  for entry in "${claude_entries[@]}"; do
    if [ -e "$DOTFILES_DIR/.claude/$entry" ]; then
      link_entry "$DOTFILES_DIR/.claude/$entry" "$HOME/.claude/$entry"
    fi
  done
}

_link_codex_config() {
  # Codex config. Every entry is symlinked, including the directories Codex
  # scans for itself (skills/, agents/). hooks.json is the sole exception:
  # Codex does NOT expand ~ or $HOME inside it, so it is install-time RENDERED
  # from hooks.json.template (see below).
  #
  # The shared skill set is defined in the repo tree, not here: .codex/skills
  # holds relative symlinks into .claude/skills. Share or drop a skill by adding
  # or removing a link there. (Hooks resolve ../../.claude/hooks by path
  # instead, since symlinks break a core.symlinks=false checkout; Codex reads
  # the skills dir itself, so that fix does not apply there. See
  # INSTALL_PLATFORM.md.)
  #
  # Note that ~/.codex/skills and ~/.codex/agents resolve into the checkout, so
  # whatever Codex writes there lands in the repo working tree: its managed
  # .system skills (gitignored) and anything installed via skill-installer
  # (which shows up as untracked).
  #
  # A REAL ~/.codex/skills (or agents/) is moved to $backup_dir wholesale --
  # Codex's .system and any hand-written skill alike. Nothing is deleted, but a
  # hand-written skill stops being live in Codex until it is moved back into
  # .codex/skills here. That is the cost of linking the directory rather than
  # its entries.
  if ! ensure_dir "$HOME/.codex"; then
    print_error "Could not create $HOME/.codex: the Codex links are skipped until it is fixed"
    record_failure "Codex config links"
    return 0
  fi

  local codex_link_entries=(
    "AGENTS.md"
    "hooks"
    "agents"
    "skills"
  )
  # `[ -e ] && link_entry` ではなく if/fi な理由は _link_claude_config を参照。
  for entry in "${codex_link_entries[@]}"; do
    if [ -e "$DOTFILES_DIR/.codex/$entry" ]; then
      link_entry "$DOTFILES_DIR/.codex/$entry" "$HOME/.codex/$entry"
    fi
  done

  # Everything below WRITES per-machine files under ~/.codex. If ~/.codex
  # itself resolves into the checkout (see the guard in link_entry), a rendered
  # hooks.json carrying this machine's $HOME and a seeded config.toml would
  # land in the working tree as untracked files -- exactly the "one `git add`
  # away from committing a token" trap the comments below describe.
  #
  # `-ef` (same directory), not a `pwd -P` string compare -- see link_entry
  # for why. It is also a plain test, so a missing ~/.codex or checkout .codex
  # (the entry loop above deliberately tolerates both) is simply false under
  # set -e.
  if [ "$HOME/.codex" -ef "$DOTFILES_DIR/.codex" ]; then
    print_warning "Skipping config.toml / hooks.json: $HOME/.codex resolves into the checkout"
    return 0
  fi

  # config.toml is deliberately NOT symlinked. Codex owns this file at runtime:
  # `codex mcp add` writes mcp_servers into it -- Authorization headers and all
  # -- and Codex accumulates projects/, plugins/ and desktop state there too. A
  # symlink would land every bit of that in the checkout, one `git add` away
  # from committing a token. It is the same trap as zsh plugins cloned through
  # ~/.oh-my-zsh/custom into the repo (see install_oh_my_zsh).
  #
  # Older installs did link it, so replace such a link with a real file.
  #
  # Whether to seed is decided HERE, before the removal below, so both branches
  # share it: a symlink -- live or dangling -- is replaced, so it counts as
  # absent. In dry-run the removal never happens, so a check placed after it
  # would see the live link and say "Keeping existing config.toml" while the
  # real run seeded from the template.
  local seed_codex_config=0
  if [ -L "$HOME/.codex/config.toml" ] || [ ! -e "$HOME/.codex/config.toml" ]; then
    seed_codex_config=1
  fi
  if [ -L "$HOME/.codex/config.toml" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      print_info "[DRY-RUN] would replace the ~/.codex/config.toml symlink with a real file"
    else
      print_warning "Replacing the ~/.codex/config.toml symlink with a real file"
      rm -f "$HOME/.codex/config.toml"
    fi
  fi
  # Seed the baseline only when nothing is there. Re-rendering would delete
  # whatever Codex has written since, so a live config is left strictly alone;
  # the template is the starting point, not a managed copy.
  if [ -f "$DOTFILES_DIR/.codex/config.toml.template" ]; then
    if [ "$seed_codex_config" -eq 1 ]; then
      if [ "$DRY_RUN" -eq 1 ]; then
        print_info "[DRY-RUN] would seed $HOME/.codex/config.toml from template"
      else
        cp "$DOTFILES_DIR/.codex/config.toml.template" "$HOME/.codex/config.toml"
        print_success "Seeded config.toml from template"
      fi
    elif [ "$DRY_RUN" -eq 1 ]; then
      print_info "[DRY-RUN] would keep existing config.toml (Codex owns it; baseline lives in .codex/config.toml.template)"
    else
      print_info "Keeping existing config.toml (Codex owns it; baseline lives in .codex/config.toml.template)"
    fi
  fi

  # hooks.json: render from the template, substituting the placeholder for
  # this machine's real $HOME (Codex does not expand ~ or $HOME itself).
  if [ -f "$DOTFILES_DIR/.codex/hooks.json.template" ]; then
    # Both branches render through _render_codex_hooks_json (the $HOME
    # escaping lives there), so the preview's cmp compares the same bytes the
    # real run would write.
    if [ "$DRY_RUN" -eq 1 ]; then
      # Read-only preview of the same diff check the real branch below
      # performs, so it does not claim "would render" for byte-identical output.
      local dry_rendered_tmp
      dry_rendered_tmp="$(mktemp)"
      _render_codex_hooks_json >"$dry_rendered_tmp"
      if [ ! -f "$HOME/.codex/hooks.json" ] ||
        ! cmp -s "$dry_rendered_tmp" "$HOME/.codex/hooks.json"; then
        print_info "[DRY-RUN] would render $HOME/.codex/hooks.json from template (resolving \$HOME)"
      else
        print_info "[DRY-RUN] $HOME/.codex/hooks.json already up to date; skipping render"
      fi
      rm -f "$dry_rendered_tmp"
    else
      local rendered_tmp
      # Beside the destination, not in $TMPDIR: the rename below is only atomic
      # within one filesystem (/tmp is often a tmpfs).
      if ! rendered_tmp="$(mktemp "$HOME/.codex/.hooks.json.XXXXXX")"; then
        print_error "Could not create a temporary file in $HOME/.codex: Codex runs without these hooks until it is fixed"
        record_failure "Codex hooks.json"
        return 0
      fi
      _render_codex_hooks_json >"$rendered_tmp"
      # Only replace (and back up) when the rendered result actually changed, so
      # re-runs don't move an identical hooks.json into a fresh backup dir.
      if [ ! -f "$HOME/.codex/hooks.json" ] ||
        ! cmp -s "$rendered_tmp" "$HOME/.codex/hooks.json"; then
        # Checked like link_path: a failed backup or move is recorded rather
        # than ending the run bare under `set -e`. The old file is copied, not
        # moved, so a failed rename leaves it where it was.
        if backup_copy_if_real "$HOME/.codex/hooks.json" &&
          mv -f "$rendered_tmp" "$HOME/.codex/hooks.json"; then
          print_success "Rendered hooks.json"
        else
          rm -f "$rendered_tmp"
          print_error "Could not write $HOME/.codex/hooks.json: Codex runs without these hooks until it is fixed"
          record_failure "Codex hooks.json"
        fi
      else
        rm -f "$rendered_tmp"
      fi
    fi
  fi
}

_link_gemini_config() {
  if ! ensure_dir "$HOME/.gemini"; then
    print_error "Could not create $HOME/.gemini: the Gemini link and settings.json are skipped until it is fixed"
    record_failure "Gemini config links"
    return 0
  fi
  # `[ -e ] && link_entry` ではなく if/fi な理由は _link_claude_config を参照。
  if [ -e "$DOTFILES_DIR/.gemini/GEMINI.md" ]; then
    link_entry "$DOTFILES_DIR/.gemini/GEMINI.md" "$HOME/.gemini/GEMINI.md"
  fi
  _seed_gemini_settings
}

# ~/.gemini/settings.json is deliberately NOT symlinked -- the same reasoning as
# ~/.codex/config.toml in _link_codex_config. Gemini CLI owns this file at
# runtime: `gemini mcp add`, /auth and /theme rewrite it with ${VAR} references
# (the tracked file's ${GITHUB_ACCESS_TOKEN} header) expanded to their values.
# The rewrite writes a temp file at the default mode (0644 under umask 022) and
# renames it over settings.json, so a symlink turns into a world-readable file
# holding the token, silently detached from the checkout -- and link_entry's
# backup_if_real would then move that file into ~/.dotfiles_backup_* on the
# next run.
#
# So: seed the tracked baseline once, owner-only; leave an existing real file's
# content alone (no backup, no replace) but restrict it to 0600; replace a
# symlink left by older installs with a real copy. Every Gemini rewrite resets
# the file to 0644 again, so the ~/.gemini directory is restricted to 0700 as
# well: that is what actually keeps other users out between install runs.
_seed_gemini_settings() {
  local src="$DOTFILES_DIR/.gemini/settings.json"
  local gemini_dir="$HOME/.gemini"
  local dest="$gemini_dir/settings.json"
  [ -f "$src" ] || return 0
  # A ~/.gemini that resolves into the checkout would make dest the tracked
  # file itself (see the guard in link_entry and _link_codex_config). Nothing
  # here can fix that layout: Gemini's own rewrites land in the working tree.
  # The backups live under $HOME whatever ~/.gemini points at.
  _restrict_gemini_backups
  if [ "$gemini_dir" -ef "$DOTFILES_DIR/.gemini" ]; then
    print_warning "Skipping settings.json: $gemini_dir resolves into the checkout, so Gemini CLI writes expanded tokens into the working tree; make ~/.gemini a real directory"
    return 0
  fi
  # A ~/.gemini the user redirected elsewhere is not chmodded through the link
  # (that would change a directory this installer does not own); say so, since
  # nothing else then keeps Gemini's 0644 rewrites away from other users.
  # A DANGLING link is not such a redirect: ensure_dir replaces it with a real
  # directory (in the dry run it only says so), which is then restricted below
  # -- so it must not read as "not restricting" in the preview.
  if [ -L "$gemini_dir" ] && [ -e "$gemini_dir" ]; then
    local dry_prefix=""
    if [ "$DRY_RUN" -eq 1 ]; then
      dry_prefix="[DRY-RUN] "
    fi
    print_warning "${dry_prefix}Not restricting ~/.gemini to mode 0700: it is a symlink; restrict the directory it points to yourself"
  elif [ "$DRY_RUN" -eq 1 ]; then
    # Also on a fresh HOME: the real run creates ~/.gemini (ensure_dir) first.
    print_info "[DRY-RUN] would restrict ~/.gemini to mode 0700"
  elif [ -d "$gemini_dir" ] && ! chmod 700 "$gemini_dir"; then
    print_error "Could not restrict $gemini_dir to mode 0700"
    record_failure "Gemini settings.json"
  fi
  # Not moved aside: it is the user's, and older installs never made one. But
  # Gemini has no usable settings file this way, so it is a failed step.
  if [ -d "$dest" ] && [ ! -L "$dest" ]; then
    print_warning "Skipping settings.json: $dest is a directory"
    record_failure "Gemini settings.json"
    return 0
  fi
  # Decided up front so dry-run reports the same plan as the real run (the
  # trap described in _link_codex_config).
  local seed=0
  if [ -L "$dest" ] || [ ! -e "$dest" ]; then
    seed=1
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    if [ -L "$dest" ]; then
      print_info "[DRY-RUN] would replace the ~/.gemini/settings.json symlink with a real file"
    fi
    if [ "$seed" -eq 1 ]; then
      print_info "[DRY-RUN] would seed ~/.gemini/settings.json from .gemini/settings.json (mode 0600)"
    else
      print_info "[DRY-RUN] would keep existing ~/.gemini/settings.json (Gemini CLI owns it) and restrict it to mode 0600"
    fi
    return 0
  fi
  if [ "$seed" -eq 1 ]; then
    if [ -L "$dest" ]; then
      print_warning "Replacing the ~/.gemini/settings.json symlink with a real file"
    fi
    # Copy into an owner-only temp file next to dest, then rename it into
    # place, so a copy that fails half-way never lands at dest (a truncated
    # file there would be kept forever by the "existing file" branch on later
    # runs). An old link at dest is dropped only once the copy is complete, and
    # dropped rather than renamed over: `mv` onto a link to a directory moves
    # the file INTO that directory and leaves the link. The final test makes
    # success mean "a real file is at dest", not "mv returned 0".
    local tmp=""
    if tmp="$(umask 077 && mktemp "$gemini_dir/.settings.json.XXXXXX")" &&
      cp "$src" "$tmp" && chmod 600 "$tmp" &&
      { [ ! -L "$dest" ] || rm -f "$dest"; } &&
      mv -f "$tmp" "$dest" && [ -f "$dest" ] && [ ! -L "$dest" ]; then
      print_success "Seeded ~/.gemini/settings.json (mode 0600)"
    else
      if [ -n "$tmp" ]; then
        rm -f "$tmp"
      fi
      print_error "Could not seed $dest"
      record_failure "Gemini settings.json"
    fi
  elif chmod 600 "$dest"; then
    print_info "Keeping existing ~/.gemini/settings.json (Gemini CLI owns it; baseline lives in .gemini/settings.json); mode 0600"
  else
    print_error "Could not restrict $dest to mode 0600"
    record_failure "Gemini settings.json"
  fi
}

# Older installs linked ~/.gemini/settings.json. Once Gemini CLI had replaced
# that link with a token-bearing 0644 file, the next run's backup_if_real moved
# the file (mode and all) into ~/.dotfiles_backup_<stamp>/.gemini/. Restrict
# any such copy that others can still read; the user decides whether to delete
# it or rotate the token.
_restrict_gemini_backups() {
  local f
  for f in "$HOME"/.dotfiles_backup_*/.gemini/settings.json; do
    if [ ! -f "$f" ] || [ -L "$f" ]; then
      continue
    fi
    # `-perm -040` = group-read set, `-perm -004` = other-read set; the same
    # spelling works in BSD and GNU find.
    if [ -z "$(find "$f" \( -perm -040 -o -perm -004 \) -print 2>/dev/null)" ]; then
      continue
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
      print_info "[DRY-RUN] would restrict $f to mode 0600 (an older run backed it up readable by others)"
    elif chmod 600 "$f"; then
      print_warning "Restricted $f to mode 0600: an older run backed it up readable by others. If it holds a token, consider deleting it and rotating the token"
    else
      print_error "Could not restrict $f to mode 0600"
      record_failure "Gemini settings.json"
    fi
  done
}

_link_tmux_helper() {
  # tmux helper script: .tmux.conf `bind S` invokes ~/.tmux/tmux_send_to_all_except_nvim.sh
  if ! ensure_dir "$HOME/.tmux"; then
    print_error "Could not create $HOME/.tmux: the tmux helper link is skipped until it is fixed"
    record_failure "tmux helper link"
    return 0
  fi
  link_entry "$DOTFILES_DIR/scripts/tmux_send_to_all_except_nvim.sh" "$HOME/.tmux/tmux_send_to_all_except_nvim.sh"
  [ "$DRY_RUN" -eq 1 ] || chmod +x "$DOTFILES_DIR/scripts/tmux_send_to_all_except_nvim.sh" 2>/dev/null || true
}

# csb: the container sandbox CLI on PATH, as ~/.local/bin/csb. It is a fixed
# three-line /bin/sh wrapper, not a symlink:
#   unset DEVELOPER_DIR TOOLCHAINS SDKROOT __PYVENV_LAUNCHER__ ... PYTHONPATH
#   exec '<python>' -I -S '<HOME>/.claude/hooks/claude_sandbox.py' "$@"
# A symlink let `cp x ~/.local/bin/` write through it into claude_sandbox.py, and
# bash-review statically allows a canonical csb call only when this file matches
# the template (_sandbox_gate.csb_wrapper_text) byte for byte -- so the text is
# generated by that function, never spelled here. <python> is the interpreter the
# bash-review launchers start the hook on (_hook_python_pick: the root-owned
# system python3), since the hook trusts only a wrapper naming that very path; it
# is never PATH's python3, a pyenv shim or anything a .python-version picks. It
# goes through the ~/.claude/hooks link rather than straight into the checkout,
# so it follows that link wherever a later run points it. An older symlink or a
# file of someone else's in the way is backed up by backup_copy_if_real (copied,
# not moved: it stays until the rename replaces it). .zshrc puts ~/.local/bin on
# PATH.
#
# It runs from main() AFTER the package steps, not from create_symlinks: the
# system python3 of a new macOS is a stub until the Command Line Tools (which the
# Homebrew step installs) are in, and starting it opens the installer dialog.
# Without the Tools _link_sandbox_cli runs no python at all, dry-run included,
# and says to re-run once they are installed.

# Run "$@" in $HOME the way the bash-review launchers start the hook: sourcing a
# launcher (in this subshell only) unsets the launch variables
# (_sandbox_gate.LAUNCH_ENV_DROP) and defines its trusted-python functions; it does
# not run the launcher's main.
_as_hook_python() {
  (. "$DOTFILES_DIR/.claude/hooks/bash-review-launcher.sh" && cd "$HOME" && "$@")
}

# The interpreter the hooks run on: the launchers' _trusted_python over their fixed
# candidates (the first root-owned, unwritable system python3 >= 3.9). Not PATH's
# python3 and not pyenv's: bash-review trusts only a wrapper naming the very
# interpreter it runs on itself (the hook's csb_is_ours). Call inside _as_hook_python.
_hook_python_pick() {
  # The array is set right above, in the same (sub)shell this runs in.
  # shellcheck disable=SC2031
  _trusted_python_candidates && _trusted_python 0 / "${_TRUSTED_CANDIDATES[@]}"
}

# Does $2 hold the csb wrapper bash-review will trust for interpreter $1? The same
# test as the hook's csb_is_ours: a regular file (not a symlink), mode 0755, byte
# for byte _sandbox_gate.csb_wrapper_text for this interpreter and $HOME.
_csb_wrapper_verified() {
  _as_hook_python "$1" -I -S -B -c 'import os, stat, sys; sys.path.insert(0, sys.argv[1]); import _sandbox_gate as g; st = os.lstat(sys.argv[2]); ok = stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o755 and g.parse_csb_wrapper(open(sys.argv[2], "rb").read(), sys.argv[3]) == sys.argv[4]; sys.exit(0 if ok else 1)' \
    "$DOTFILES_DIR/.claude/hooks" "$2" "$HOME" "$1" 2>/dev/null
}

_link_sandbox_cli() {
  local dest="$HOME/.local/bin/csb" py tmp
  # A new macOS has no real /usr/bin/python3 until the Command Line Tools are
  # installed: it is a stub that, when started, opens the installer dialog. The
  # interpreter search below would start it (and a --dry-run would too), so
  # without the Tools nothing is run. Not a failure: the Homebrew step installs
  # them, and this runs after it, so on a real run this only trips when that did
  # not work -- a rerun then finishes csb. (Xcode.app also satisfies it.)
  if [[ "$OS" == "macos" ]] && ! xcode-select -p &>/dev/null; then
    print_warning "The Command Line Tools are not installed, so csb (which runs on the system python3 they provide) is not set up yet; install them with: xcode-select --install, then re-run ./install.sh"
    return 0
  fi
  if ! ensure_dir "$HOME/.local" || ! ensure_dir "$HOME/.local/bin"; then
    print_error "Could not create $HOME/.local/bin: csb is not installed until it is fixed"
    record_failure "csb wrapper"
    return 0
  fi
  py="$(_as_hook_python _hook_python_pick </dev/null 2>/dev/null || true)"
  case "$py" in
  /*) ;;
  *) py="" ;;
  esac
  if [ -z "$py" ]; then
    print_error "No trusted system python3 (root-owned, not group/other-writable, 3.9 or later; on macOS: xcode-select --install): csb is not installed"
    record_failure "csb wrapper"
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    print_info "[DRY-RUN] would write $dest (runs $HOME/.claude/hooks/claude_sandbox.py with $py)"
    return 0
  fi
  if ! tmp="$(mktemp "$HOME/.local/bin/.csb.XXXXXX")"; then
    print_error "Could not create a temporary file in $HOME/.local/bin: csb is not installed"
    record_failure "csb wrapper"
    return 0
  fi
  # -I: the template is read from the checkout, never from the cwd. -S: no site.
  # -B: importing it writes no __pycache__ into the checkout (-I ignores
  # PYTHONDONTWRITEBYTECODE).
  if ! _as_hook_python "$py" -I -S -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import _sandbox_gate as g; sys.stdout.write(g.csb_wrapper_text(sys.argv[2], sys.argv[3]))' \
    "$DOTFILES_DIR/.claude/hooks" "$py" "$HOME" >"$tmp" 2>/dev/null; then
    rm -f "$tmp"
    print_error "Could not write the csb wrapper for $py (an unusual interpreter or home path?): csb is not installed"
    record_failure "csb wrapper"
    return 0
  fi
  # Same bytes is not enough: a wrapper the hook rejects (a group-writable mode,
  # say) is backed up and rewritten rather than reported as installed.
  if [ -f "$dest" ] && [ ! -L "$dest" ] && cmp -s "$tmp" "$dest" &&
    _csb_wrapper_verified "$py" "$dest"; then
    rm -f "$tmp"
    print_info "Already installed csb"
    return 0
  fi
  # Copied aside, not moved: the old wrapper stays at the path until the rename
  # below replaces it, so a failed mv leaves csb as it was.
  if ! backup_copy_if_real "$dest"; then
    rm -f "$tmp"
    record_failure "csb wrapper"
    return 0
  fi
  # mv onto the path replaces whatever is there (a symlink is replaced, not
  # followed); chmod first so the file never appears without its mode.
  if ! { chmod 0755 "$tmp" && mv -f "$tmp" "$dest"; }; then
    rm -f "$tmp"
    print_error "Could not install $dest"
    record_failure "csb wrapper"
    return 0
  fi
  # Check what ended up at the path, not what was written: something rewriting the
  # temporary file before the move (or the path right after it) must not leave a
  # wrapper bash-review would then trust (_csb_wrapper_verified).
  if _csb_wrapper_verified "$py" "$dest"; then
    print_success "Installed csb (~/.local/bin/csb -> $HOME/.claude/hooks/claude_sandbox.py)"
  else
    rm -f "$dest"
    print_error "$dest changed while it was being installed: removed it, csb is not installed"
    record_failure "csb wrapper"
  fi
}

create_symlinks() {
  print_info "Creating symbolic links..."

  # Read whatever identity git resolves right now, BEFORE the .gitconfig link
  # below replaces it. An upgrade from a real ~/.gitconfig that carried [user]
  # keeps its name/email this way instead of silently losing it.
  #
  # --includes is not optional: git follows [include] by default only when no
  # file or scope is named, so a bare --global read the common
  # `[include] path = ~/.gitconfig.local` layout back as empty -- and the link
  # below then made that file unreachable, dropping the identity.
  #
  # `-C /` because --includes also resolves includeIf, and gitdir:/onbranch:
  # are matched against the CURRENT repository -- which is normally this
  # checkout. A `[includeIf "gitdir:~/work/"]` identity meant for that tree
  # only would otherwise be baked into user.gitconfig for every repository.
  # Outside any repository no conditional include matches, and a plain
  # [include] is still followed.
  local prior_git_name prior_git_email
  prior_git_name="$(git -C / config --global --includes user.name 2>/dev/null || true)"
  prior_git_email="$(git -C / config --global --includes user.email 2>/dev/null || true)"

  # Backup existing files. In dry-run nothing is moved, so the dir is never
  # created (and the empty-dir cleanup at the end is skipped to match).
  backup_dir="$HOME/.dotfiles_backup_$(date +%Y%m%d_%H%M%S)"
  [ "$DRY_RUN" -eq 1 ] || mkdir -p "$backup_dir"

  _link_top_level_dotfiles
  _seed_zsh_secrets
  _render_git_local_config "$prior_git_name" "$prior_git_email"
  _link_editor_configs
  _link_claude_config
  _link_codex_config
  _link_gemini_config

  # The Oh My Zsh theme is NOT linked here: see link_oh_my_zsh_theme, called
  # from main() only after install_oh_my_zsh.

  _link_tmux_helper

  # csb (_link_sandbox_cli) is NOT written here: it needs the system python3,
  # which a new macOS only has once the Command Line Tools are in -- so main()
  # runs it after the package steps.

  # Backup-dir bookkeeping. Guard the whole open+close as one unit: in dry-run
  # the dir was never created, so running `ls -A`/`rmdir` on it would fail under
  # `set -e -o pipefail`.
  if [ "$DRY_RUN" -eq 1 ]; then
    print_info "[DRY-RUN] no changes were made (create_symlinks)"
  elif [ -n "$(ls -A "$backup_dir" 2>/dev/null)" ]; then
    print_info "Backup created at: $backup_dir"
  else
    rmdir "$backup_dir"
  fi
}

# Install Vim plugins
install_vim_plugins() {
  print_info "Installing Vim plugins..."
  # Report the real outcome: vim being absent (exit 127) and a PlugInstall
  # failure are different, and neither may print success.
  if ! command_exists vim; then
    print_warning "vim not found; skipping Vim plugin installation"
    return
  fi
  if vim +PlugInstall +qall; then
    print_success "Vim plugins installed"
  else
    record_failure "vim +PlugInstall"
    print_warning "vim +PlugInstall failed (continuing)"
  fi
}

setup_neovim() {
  print_info "Setting up Neovim..."
  print_info "Please open Neovim manually to complete lazy.nvim setup: nvim"
  print_info "Lazy.nvim will automatically install plugins on first launch"
}

# Install AI tools (optional)
install_ai_tools() {
  # Skip cleanly when stdin is not a TTY (piped `curl | bash`, CI, </dev/null):
  # a bare `read` returns non-zero at EOF and, under `set -e`, would abort the
  # whole installer here -- skipping the later MCP registration and shell change
  # while still printing "Installation completed!", which looks like success.
  if [ ! -t 0 ]; then
    print_info "Non-interactive shell detected; skipping optional AI tools prompt."
    return 0
  fi
  read -p "Do you want to install AI development tools? (y/n): " -n 1 -r || REPLY=""
  echo
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    print_info "Installing AI development tools..."
    _install_ai_cli_tools
    print_success "AI tools installation attempted (check warnings above)"
  fi
}

# Install one global npm CLI unless its command is already on PATH. Updating
# an installed one is ./scripts/update_ai_tools.sh's job, not a rerun's: an
# unconditional `npm install -g` reinstalled all of them on every run.
_npm_global_tool() {
  local cmd="$1" label="$2" pkg="$3"
  if command_exists "$cmd"; then
    print_info "$label already installed (update with ./scripts/update_ai_tools.sh)"
    return 0
  fi
  try_install "$label" npm install -g "$pkg"
}

_install_ai_cli_tools() {
  # Claude Code (official npm distribution)
  _npm_global_tool claude "Claude Code" @anthropic-ai/claude-code
  _npm_global_tool codex Codex @openai/codex
  _npm_global_tool gemini "Gemini CLI" @google/gemini-cli
  # GitHub Copilot CLI (standalone `copilot` command; used by
  # update_ai_tools.sh and the `cop` alias in .zshrc).
  _npm_global_tool copilot "Copilot CLI" @github/copilot
}

# Prepend the user-scheme scripts dir of the interpreter behind pip command $1
# (pip3 -> python3, pip -> python) to PATH: ~/Library/Python/X.Y/bin on macOS,
# ~/.local/bin elsewhere. Silent no-op when it cannot be read.
_export_pip_user_bin() {
  local py="python3" base
  [ "$1" = "pip" ] && py="python"
  command_exists "$py" || return 0
  base="$("$py" -m site --user-base 2>/dev/null || true)"
  case "$base" in
  /*) export PATH="$base/bin:$PATH" ;;
  esac
  return 0
}

# rubocop (gem) and phpstan (composer) install the same way on macOS and
# Ubuntu; both arms of install_linters_formatters call this at the same point.
_install_ruby_php_linters() {
  # Ruby tools
  if command_exists gem; then
    if ! command_exists rubocop; then
      print_info "Installing rubocop via gem..."
      try_install rubocop gem install rubocop
    fi
  fi

  # PHP tools
  if command_exists php && ! command_exists phpstan; then
    if command_exists composer; then
      print_info "Installing phpstan via composer..."
      try_install phpstan composer global require phpstan/phpstan
    fi
  fi
}

# Install linters and formatters (used by .claude/hooks/lint.sh and auto-format.sh)
install_linters_formatters() {
  print_info "Installing linters and formatters..."

  # --- npm-based tools (cross-platform, requires Node.js) ---
  if command_exists npm; then
    local npm_tools=(
      prettier   # JS/TS/JSON/CSS/HTML/MD/YAML formatter
      eslint     # JS/TS linter
      typescript # tsc type checker
    )
    for tool in "${npm_tools[@]}"; do
      if ! command_exists "$tool" && ! npm list -g "$tool" &>/dev/null; then
        print_info "Installing $tool via npm..."
        try_install "$tool" npm install -g "$tool"
      else
        print_info "$tool already installed"
      fi
    done
  else
    print_warning "npm not found, skipping npm-based tools"
  fi

  # --- pip-based tools (cross-platform, requires Python) ---
  if command_exists pip3 || command_exists pip; then
    local pip_cmd="pip3"
    command_exists pip3 || pip_cmd="pip"

    local pip_tools=(
      ruff     # Python linter + formatter
      bandit   # Python security linter
      mypy     # Python type checker
      autopep8 # Python formatter (fallback)
      isort    # Python import sorter
    )
    # On macOS pip --user puts scripts in ~/Library/Python/X.Y/bin, which is on
    # no PATH while this script runs (.zshrc adds it for interactive shells
    # only), so command_exists alone missed every one of these and each rerun
    # reinstalled all five. Put that dir on PATH for the rest of the run, and
    # also take pip's own word that the package is installed.
    _export_pip_user_bin "$pip_cmd"
    for tool in "${pip_tools[@]}"; do
      if command_exists "$tool" || "$pip_cmd" show "$tool" &>/dev/null; then
        print_info "$tool already installed"
      else
        print_info "Installing $tool via pip..."
        pip_install_user "$pip_cmd" "$tool"
      fi
    done
  else
    print_warning "pip not found, skipping Python tools"
  fi

  # --- Platform-specific tools ---
  case "$OS" in
  macos)
    # Note: use the standalone clang-format formula, not llvm. llvm is
    # keg-only (macOS ships its own clang), so brew never links its
    # clang-format into PATH and the format hook would report it missing.
    # checkstyle has no brew formula; install manually from
    # https://checkstyle.sourceforge.io/ if needed.
    local brew_tools=(
      shellcheck   # Shell script linter
      shfmt        # Shell script formatter
      cppcheck     # C/C++ linter
      clang-format # C/C++ formatter
      staticcheck  # Go advanced linter
      php-cs-fixer # PHP formatter
    )
    for tool in "${brew_tools[@]}"; do
      if ! brew list "$tool" &>/dev/null; then
        print_info "Installing $tool via brew..."
        try_install "$tool" brew install "$tool"
      else
        print_info "$tool already installed"
      fi
    done

    # google-java-format (brew cask or manual)
    if ! command_exists google-java-format; then
      try_install google-java-format brew install google-java-format
    fi

    # Go tools (requires go)
    if command_exists go; then
      # ~/go/bin is not on PATH until exported (see the ubuntu branch below),
      # and install.sh runs under bash, so .zshrc's own export does not apply
      # here. staticcheck is not in this block because macOS gets it from brew
      # above.
      export PATH="$HOME/go/bin:$PATH"
      if ! command_exists goimports; then
        print_info "Installing goimports..."
        try_install goimports go install golang.org/x/tools/cmd/goimports@latest
      fi
    fi

    _install_ruby_php_linters
    ;;

  ubuntu)
    print_info "Installing APT-based linter/formatter packages..."
    try_install "APT linter packages" \
      sudo apt-get install -y shellcheck cppcheck clang-format

    # shfmt (snap or go install)
    if ! command_exists shfmt; then
      if command_exists snap; then
        print_info "Installing shfmt via snap..."
        try_install "shfmt (snap)" sudo snap install shfmt
      elif command_exists go; then
        print_info "Installing shfmt via go install..."
        try_install shfmt go install mvdan.cc/sh/v3/cmd/shfmt@latest
        # See the PATH export note below: go install's binaries aren't on
        # PATH until exported.
        export PATH="$HOME/go/bin:$PATH"
      fi
    fi

    # staticcheck (go install)
    if command_exists go; then
      # go install places binaries under ~/go/bin, which isn't on PATH
      # until exported -- without this, the command_exists checks for
      # staticcheck/goimports immediately below (and anything later in this
      # run) falsely report the tool missing right after installing it.
      export PATH="$HOME/go/bin:$PATH"
      if ! command_exists staticcheck; then
        print_info "Installing staticcheck..."
        try_install staticcheck go install honnef.co/go/tools/cmd/staticcheck@latest
      fi
      if ! command_exists goimports; then
        print_info "Installing goimports..."
        try_install goimports go install golang.org/x/tools/cmd/goimports@latest
      fi
    fi

    _install_ruby_php_linters

    # php-cs-fixer (composer; macOS gets it from brew above)
    if ! command_exists php-cs-fixer; then
      if command_exists composer; then
        print_info "Installing php-cs-fixer via composer..."
        try_install php-cs-fixer composer global require friendsofphp/php-cs-fixer
      fi
    fi

    # checkstyle / google-java-format (manual install if Java available)
    if command_exists java; then
      print_info "Java detected. checkstyle and google-java-format may need manual installation."
      print_info "  checkstyle: https://checkstyle.sourceforge.io/"
      print_info "  google-java-format: https://github.com/google/google-java-format"
    fi
    ;;

  windows)
    print_warning "Windows detected. Install the following tools manually or via package manager:"
    echo "  npm tools (cross-platform): prettier, eslint, typescript"
    echo "  pip tools (cross-platform): ruff, bandit, mypy, autopep8, isort"
    echo "  Shell: shellcheck (scoop install shellcheck), shfmt (scoop install shfmt)"
    echo "  C/C++: cppcheck (scoop install cppcheck), clang-format (via LLVM)"
    echo "  Go: staticcheck, goimports (go install)"
    echo "  Ruby: rubocop (gem install rubocop)"
    echo "  PHP: phpstan, php-cs-fixer (composer global require)"
    echo "  Java: checkstyle, google-java-format"

    # npm/pip tools are still installed above (cross-platform)
    # Try scoop if available
    if command_exists scoop; then
      print_info "Scoop detected, installing available tools..."
      local scoop_tools=(shellcheck shfmt cppcheck)
      for tool in "${scoop_tools[@]}"; do
        if ! command_exists "$tool"; then
          try_install "$tool (scoop)" scoop install "$tool"
        fi
      done
    fi
    ;;
  esac

  print_success "Linters and formatters installation completed"
}

# Change default shell to zsh
change_shell() {
  # Guard first: without zsh, `$(which zsh)` resolves to "" and the branch
  # below would run `chsh -s ""`.
  if ! command_exists zsh; then
    print_warning "zsh not found; skipping shell change. Install zsh, then run: chsh -s \$(which zsh)"
    return 0
  fi
  # "Is the login shell a zsh", not "is it THE zsh first on PATH". macOS logs
  # in with /bin/zsh while install_brew_packages puts a homebrew zsh first on
  # PATH, so comparing against `$(which zsh)` would retry chsh on every run (a
  # password prompt), have it refuse /opt/homebrew/bin/zsh as absent from
  # /etc/shells, and tell a user already on zsh to go edit that file.
  if [ "$(basename "${SHELL:-}")" != "zsh" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      print_info "[DRY-RUN] would change the default shell to zsh (chsh)"
      return 0
    fi
    print_info "Changing default shell to zsh..."
    if command_exists chsh; then
      # chsh fails if zsh isn't listed in /etc/shells; don't let that abort
      # the whole script under set -e this close to the finish line.
      if chsh -s "$(which zsh)"; then
        print_success "Default shell changed to zsh (restart terminal to apply)"
      else
        record_failure "chsh"
        print_warning "chsh failed; add $(command -v zsh) to /etc/shells and re-run chsh"
      fi
    else
      print_warning "chsh command not found. Please change shell manually: chsh -s $(which zsh)"
    fi
  else
    print_success "Default shell is already zsh"
  fi
}

# Install OS-specific packages. A separate function so the dispatch is
# unit-testable on its own. A bash `case` with no matching arm is a silent
# no-op, so every OS value gets an explicit arm (a non-Debian Linux is
# OS="linux", set by detect_os when /etc/debian_version is absent).
install_os_packages() {
  case "$OS" in
  macos)
    # `if` and not a bare pair: install_brew_packages needs `brew` on PATH, so
    # it must not run when the bootstrap failed. Calling install_homebrew as
    # the condition is also what stops `set -e` from aborting the whole
    # installer on its non-zero return.
    if install_homebrew; then
      install_brew_packages
    fi
    ;;
  ubuntu)
    install_apt_packages
    ;;
  linux)
    print_warning "Non-Debian Linux detected (OS=$OS, no /etc/debian_version). Automatic package installation is only implemented for Ubuntu/Debian; install packages manually (see install_apt_packages for the list)."
    ;;
  windows)
    print_warning "Windows detected. Please ensure Git Bash or WSL is properly configured."
    print_warning "Some features may require manual installation."
    ;;
  esac
}

main() {
  # A run reports only its own failures: start from an empty list rather than
  # whatever a previous call in the same shell (or a test) left behind.
  INSTALL_FAILURES=()

  # Parse options first, before the banner and the checkout guard, so `--help`
  # works from anywhere and `--dry-run` is set before the first side effect.
  # This script accepts no positional arguments.
  while [ "$#" -gt 0 ]; do
    case "$1" in
    -n | --dry-run)
      DRY_RUN=1
      ;;
    -h | --help)
      usage
      return 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      print_error "Unknown option: $1"
      usage >&2
      return 2
      ;;
    *)
      print_error "Unexpected argument: $1"
      usage >&2
      return 2
      ;;
    esac
    shift
  done
  if [ "$#" -gt 0 ]; then
    print_error "Unexpected argument: $1"
    usage >&2
    return 2
  fi

  echo "  Dotfiles Installation Script"
  echo
  if [ "$DRY_RUN" -eq 1 ]; then
    print_warning "DRY-RUN: previewing changes only; nothing will be written."
    echo
  fi

  # Guard against a bad DOTFILES_DIR (e.g. script piped into bash instead of
  # run from a checkout) — otherwise create_symlinks would silently skip
  # every entry.
  if [ ! -e "$DOTFILES_DIR/.zshrc" ] || [ ! -d "$DOTFILES_DIR/.claude" ]; then
    print_error "Dotfiles repository not found at: $DOTFILES_DIR"
    print_error "Clone the repo and run install.sh from the checkout: git clone <repo> && cd dotfiles && ./install.sh"
    exit 1
  fi

  detect_os

  # Symlinks first: this is the part that is actually ours, it needs no package
  # manager, and it is what a dotfiles install is FOR. Everything below can fail
  # on a flaky network or a renamed formula; run last, one such failure would
  # leave the machine with no dotfiles linked at all.
  create_symlinks

  # Package / tool installation is not simulated in dry-run: these steps are
  # network-bound and already externally idempotent (each guards on
  # command_exists / brew list / dir tests). Announce and skip them; the
  # dry-run value is in the symlink/backup preview above, not here.
  if [ "$DRY_RUN" -eq 1 ]; then
    print_info "[DRY-RUN] Skipping package and tool installation (not simulated)."
    print_info "[DRY-RUN]   Would install: OS packages (Homebrew / APT), WezTerm, fonts,"
    print_info "[DRY-RUN]   Node.js, gh, pyenv, uv, glow, Docker (+ docker info check),"
    print_info "[DRY-RUN]   hadolint, lazydocker, tree-sitter, MCP deps, linters/formatters,"
    print_info "[DRY-RUN]   Oh My Zsh, vim-plug, tmux plugins;"
    print_info "[DRY-RUN]   set up Neovim; install AI tools; register Claude MCP servers."
  else
    install_os_packages

    # Common installations
    install_wezterm
    install_fonts
    install_nodejs
    install_gh
    install_pyenv
    install_uv
    install_glow
    install_docker
    install_hadolint
    install_lazydocker
    install_tree_sitter_cli
    install_mcp_server_deps
    install_linters_formatters
    install_oh_my_zsh
    install_vim_plug
    install_tmux_plugins

    # Setup editors
    install_vim_plugins
    setup_neovim

    # Optional AI tools
    install_ai_tools

    # Register MCP servers with Claude Code (after symlinks + AI tools)
    register_claude_mcp_servers
  fi

  # csb, the container sandbox CLI. After the package steps, not with the other
  # links: its wrapper runs on the system python3, which on a new macOS is a
  # stub until the Command Line Tools (installed with Homebrew above) are in.
  # In dry-run it still runs, for the preview of what it would write; it starts
  # no python without the Tools (see _link_sandbox_cli).
  _link_sandbox_cli

  # Oh My Zsh theme symlink (dry-run aware; see link_oh_my_zsh_theme). Must
  # run after install_oh_my_zsh above (real run) so a true first install
  # doesn't pre-create $HOME/.oh-my-zsh and trip Oh My Zsh's own installer;
  # in dry-run install_oh_my_zsh never ran, so this is still the only place
  # that previews the theme symlink.
  link_oh_my_zsh_theme

  # Change shell (dry-run aware: previews the chsh, never runs it)
  change_shell

  echo
  if [ "$DRY_RUN" -eq 1 ]; then
    print_success "Dry-run complete. No changes were made."
    print_info "Re-run without --dry-run to apply."
    echo
    return 0
  fi
  # Failed steps only warned and the run carried on; say so here rather than
  # claiming success, and let the exit status carry it to a calling script.
  if [ "${#INSTALL_FAILURES[@]}" -gt 0 ]; then
    report_install_failures
  else
    print_success "Installation completed!"
  fi
  echo
  print_info "Next steps:"
  echo "  1. Restart your terminal"
  echo "  2. Open Neovim to complete lazy.nvim setup: nvim"
  echo "  3. In tmux, press prefix + I to install plugins (tpm)"
  echo "  4. Install AI tools if needed (see README.md)"
  echo "  5. Docker: check that the engine answers with: docker info"
  echo "     macOS: launch Docker Desktop once (open -a Docker); it puts the docker CLI on PATH."
  echo "     Linux: log out and back in (or run 'newgrp docker') so the docker group applies."
  echo "     csb (~/.local/bin/csb) then copies a repository snapshot into a disposable"
  echo "     container, runs commands there and brings the result back as a patch."
  echo "  6. Customize configurations as needed"
  echo
  print_info "Secrets / credentials still required (cannot be installed):"
  echo "  Put the exports in ~/.zsh_secrets (sourced by .zshrc, outside the checkout)."
  echo "  Never in ~/.zshrc itself -- it is a symlink into the repo, so git tracks it."
  echo "  - GEMINI_API_KEY : the gemini-api bash-review hook, the gemini-consultant MCP"
  echo "                     server, and the Vim/Neovim AI features' Gemini calls"
  echo "                     (scripts/gemini_api.py). Without it those editor keymaps"
  echo "                     fail with an explicit message rather than falling back."
  echo "  - GITHUB_ACCESS_TOKEN : the GitHub MCP server (.gemini/settings.json resolves \${GITHUB_ACCESS_TOKEN})"
  echo "  - gh auth login   : authenticate GitHub CLI (used by .gitconfig credential helper)"
  echo
  print_info "Utility scripts:"
  echo "  - ./scripts/update_ai_tools.sh : Update all AI tools"
  echo "  - ./scripts/new_project.sh : Scaffold a new project (docs/, assets/, README.md, git init; 'np' in zsh)"
  echo "  - ./scripts/tmux_send_to_all_except_nvim.sh : Send commands to tmux panes"
  echo
  if [ "${#INSTALL_FAILURES[@]}" -gt 0 ]; then
    return 1
  fi
  return 0
}

# Run main function only when executed directly (not when sourced by tests).
# BASH_SOURCE[0] is empty when the script is piped into bash; run main there
# too so the DOTFILES_DIR guard can print its clone-the-repo error.
if [[ -z "${BASH_SOURCE[0]:-}" ]] || [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
