#!/usr/bin/env bash

# Dotfiles Installation Script
# Supports: macOS, Ubuntu/Debian (WSL is detected and treated as Ubuntu),
# Windows (Git Bash)

# -e: exit on error. -o pipefail: a pipeline (e.g. `curl ... | sudo tee`)
# fails if ANY stage fails, not just the last one -- without it, a failed
# curl in front of `sudo tee`/`sudo dd` would go unnoticed.
# (-u is intentionally omitted: several env vars are read without a
# default elsewhere in this script -- e.g. $USER in install_docker,
# $SHELL in change_shell -- and auditing every use would be a much
# larger change than this pass covers.)
set -eo pipefail

# Color codes for output
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

# --- Pinned upstream bootstrap scripts ---------------------------------------
# These installers are fetched over the network and executed -- two of them by
# root. Pointing at HEAD/master means the bytes that run can change between two
# runs of this script with no signal to us, so each is pinned to an immutable
# commit. raw.githubusercontent.com serves any commit SHA, and a commit SHA
# already identifies its content cryptographically, so pinning the ref gives
# the same guarantee a separate checksum table would -- without a manifest to
# maintain, and without breaking every time upstream ships a release.
#
# What this does NOT cover: the pinned installer still downloads whatever is
# current when it runs (Homebrew's own formulae, the ohmyzsh clone, ...). The
# boundary is deliberate -- it is the bootstrap script itself that is verified,
# not the tree it goes on to install.
#
# The remaining fetches go through vendor redirectors (astral.sh/uv, pyenv.run,
# get.docker.com, deb.nodesource.com) that expose no immutable ref, so they
# stay unpinned; pinning them would require a content hash re-pinned on every
# upstream release.
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

# Function to print colored messages
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

# Usage / help text. Kept honest about dry-run's scope: it previews the parts
# that modify the user's own files (the reason to preview at all), and says
# outright that package installation is skipped rather than simulated.
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
real files it replaces), installs packages for the detected OS
(macOS / Ubuntu / WSL), and switches the default shell to zsh.
EOF
}

# Detect OS
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

# Check if command exists
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
#   fetch_and_run https://get.docker.com sudo sh
#   fetch_and_run https://.../ohmyzsh/install.sh sh -- --unattended
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
  # script positional args on the right. No `--` => everything is interpreter
  # side, so existing `fetch_and_run <url> sh` / `... sudo -E bash` calls are
  # unchanged.
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
  local tmp status
  tmp="$(mktemp)"
  if ! curl -fsSL "$url" -o "$tmp"; then
    print_warning "Failed to download $url"
    rm -f "$tmp"
    return 1
  fi
  # A blank body can slip past `curl -f` (e.g. an empty 200 response); never
  # hand an empty script to a (possibly root) shell.
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
    fetch_and_run "https://raw.githubusercontent.com/Homebrew/install/$HOMEBREW_INSTALL_REF/install.sh" /bin/bash

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
    liblzma-dev ||
    print_warning "Some APT packages failed to install (continuing)"

  # On Debian/Ubuntu the binaries are named `batcat` and `fdfind`, but the
  # configs (.zshrc vf(), nvim telescope) invoke `bat` and `fd`. Provide
  # PATH-visible aliases so those code paths resolve.
  #
  # Never clobber a real file here. These two were the only links in the script
  # that skipped create_symlinks' backup step, back when backup_if_real was
  # nested inside create_symlinks and so was not in scope at this point. A user
  # who keeps their own `fd` or `bat` wrapper in ~/.local/bin had it destroyed
  # with no backup and no warning. The rule below stays hand-rolled rather than
  # delegating to the now-top-level backup_if_real: these aliases are generated
  # artifacts that belong in ~/.local/bin, not dotfiles worth moving into
  # $backup_dir. A symlink is ours to replace (the same rule backup_if_real
  # applies); anything else belongs to the user and wins.
  link_debian_alias() {
    local source_cmd="$1" alias_path="$HOME/.local/bin/$2"
    command_exists "$source_cmd" || return 0
    if [ -e "$alias_path" ] && [ ! -L "$alias_path" ]; then
      print_warning "$alias_path already exists and is not a symlink; leaving it alone (no $2 alias created)"
      return 0
    fi
    ln -sf "$(command -v "$source_cmd")" "$alias_path"
  }

  mkdir -p "$HOME/.local/bin"
  link_debian_alias batcat bat
  link_debian_alias fdfind fd

  print_success "APT packages installed"
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
    fzf             # fuzzy finder (.zshrc `eval "$(fzf --zsh)"`, sshs/cf/vf)
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
      brew install "$package" || print_warning "Failed to install $package (continuing)"
    fi
  done

  print_success "Homebrew packages installed"
}

# Install WezTerm
install_wezterm() {
  # Every brew/network/apt step below can fail transiently. WezTerm is
  # optional, so failures must warn and continue -- under `set -e` an
  # unguarded failure here aborts the whole installer (symlinks, MCP, ...).
  if [[ "$OS" == "macos" ]]; then
    if ! brew list --cask wezterm &>/dev/null; then
      print_info "Installing WezTerm..."
      brew install --cask wezterm || print_warning "Failed to install WezTerm"
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
          sudo tee /etc/apt/sources.list.d/wezterm.list >/dev/null ||
          print_warning "Failed to add WezTerm apt repository"
        sudo apt-get update || print_warning "apt-get update failed"
        sudo apt-get install -y wezterm || print_warning "Failed to install WezTerm"
      else
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

# Install fonts
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
        print_warning "Failed to install font-ubuntu-mono"
      }
    brew install --cask font-hack-nerd-font ||
      {
        fonts_ok=false
        print_warning "Failed to install font-hack-nerd-font"
      }
    if [[ "$fonts_ok" == true ]]; then
      print_success "Fonts installed"
    fi
  elif [[ "$OS" == "ubuntu" ]]; then
    print_info "Installing fonts..."
    if sudo apt-get install -y fonts-ubuntu fonts-hack-ttf; then
      print_success "Fonts installed"
    else
      print_warning "Failed to install fonts"
    fi
  fi
}

# Install GitHub CLI (gh)
# .gitconfig uses `gh auth git-credential` as the HTTPS credential helper.
install_gh() {
  if command_exists gh; then
    print_success "gh already installed"
    return
  fi
  print_info "Installing GitHub CLI (gh)..."
  if [[ "$OS" == "macos" ]]; then
    brew install gh || print_warning "Failed to install gh"
  elif [[ "$OS" == "ubuntu" ]]; then
    # Same guard rationale as install_wezterm: gh is optional, so a failed
    # keyring/apt step must warn and continue, not abort the installer.
    if curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg |
      sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg; then
      sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg ||
        print_warning "Failed to chmod gh keyring"
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" |
        sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null ||
        print_warning "Failed to add gh apt repository"
      sudo apt-get update || print_warning "apt-get update failed"
      sudo apt-get install -y gh || print_warning "Failed to install gh"
    else
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
  fetch_and_run https://astral.sh/uv/install.sh sh ||
    print_warning "Failed to install uv"
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
    fetch_and_run https://pyenv.run bash ||
      print_warning "Failed to install pyenv"
    if command_exists pyenv || [ -d "$HOME/.pyenv" ]; then
      print_success "pyenv installed"
    fi
  fi
}

# Install glow — vim :PreviewMarkdown renderer.
install_glow() {
  if command_exists glow; then
    print_success "glow already installed"
    return
  fi
  print_info "Installing glow..."
  if [[ "$OS" == "macos" ]]; then
    brew install glow || print_warning "Failed to install glow"
  elif [[ "$OS" == "ubuntu" ]]; then
    # Prefer go install (go is installed via apt) to avoid another apt repo.
    if command_exists go; then
      go install github.com/charmbracelet/glow@latest 2>/dev/null ||
        print_warning "Failed to install glow via go"
      # go install places the binary under ~/go/bin, which isn't on PATH
      # until exported -- without this the command_exists check just below
      # falsely reports glow as missing right after installing it (same
      # fix as install_nodejs/install_uv already apply after their own
      # installs).
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
    brew install lazydocker || print_warning "Failed to install lazydocker"
  elif [[ "$OS" == "ubuntu" ]]; then
    fetch_and_run "https://raw.githubusercontent.com/jesseduffield/lazydocker/$LAZYDOCKER_INSTALL_REF/scripts/install_update_linux.sh" bash ||
      print_warning "Failed to install lazydocker"
  fi
  if command_exists lazydocker; then
    print_success "lazydocker installed"
  fi
}

# Install Docker — used by the dsollama/deollama aliases.
install_docker() {
  if command_exists docker; then
    print_success "Docker already installed"
    return
  fi
  print_info "Installing Docker..."
  if [[ "$OS" == "macos" ]]; then
    brew install --cask docker || print_warning "Failed to install Docker Desktop"
    print_info "Launch Docker Desktop once to put the docker CLI on PATH."
    # macOS `--cask docker` does not put the `docker` CLI on PATH until Docker
    # Desktop is launched once, so the trailing check below legitimately finds
    # nothing on the happy path — the `if` (no else) returns 0, so `set -e`
    # never aborts the installer.
  elif [[ "$OS" == "ubuntu" ]]; then
    fetch_and_run https://get.docker.com sudo sh ||
      print_warning "Failed to install Docker engine"
    sudo usermod -aG docker "$USER" 2>/dev/null || true
    print_info "Log out/in (or 'newgrp docker') for group membership to take effect."
  fi
  if command_exists docker; then
    print_success "Docker installed"
  fi
}

# Install the tree-sitter CLI — nvim treesitter `auto_install` needs it to
# build grammars that ship only a grammar.js.
install_tree_sitter_cli() {
  if command_exists tree-sitter; then
    print_success "tree-sitter CLI already installed"
    return
  fi
  if command_exists npm; then
    print_info "Installing tree-sitter CLI via npm..."
    npm install -g tree-sitter-cli 2>/dev/null ||
      print_warning "Failed to install tree-sitter CLI"
  else
    print_warning "npm not found; skipping tree-sitter CLI"
  fi
}

# Install tpm (Tmux Plugin Manager) — .tmux.conf declares plugins via @plugin
# and runs ~/.tmux/plugins/tpm/tpm, but tpm does not bootstrap itself.
install_tmux_plugins() {
  if [ -d "$HOME/.tmux/plugins/tpm" ]; then
    print_success "tpm already installed"
    return
  fi
  print_info "Installing tpm..."
  git clone https://github.com/tmux-plugins/tpm "$HOME/.tmux/plugins/tpm" 2>/dev/null ||
    print_warning "Failed to install tpm"
  print_info "Launch tmux and press prefix + I to install the declared plugins."
}

# Install Python deps for the bundled MCP server (.claude/mcp-servers/gemini-consultant).
# server.py imports `mcp.server.fastmcp`, so the `mcp` package must be present.
install_mcp_server_deps() {
  if command_exists pip3 || command_exists pip; then
    local pip_cmd="pip3"
    command_exists pip3 || pip_cmd="pip"
    if ! $pip_cmd show mcp &>/dev/null; then
      print_info "Installing Python 'mcp' package for the gemini-consultant MCP server..."
      $pip_cmd install --user mcp 2>/dev/null || print_warning "Failed to install mcp"
    else
      print_info "Python 'mcp' package already installed"
    fi
  else
    print_warning "pip not found; skipping MCP server deps"
  fi
}

# Register MCP servers with Claude Code (user scope, idempotent).
# Server connections live in ~/.claude.json, which is NOT under dotfiles
# management, so re-register them here so a fresh machine matches the
# servers assumed by .claude/skills/codex-consultation and CLAUDE.md.
register_claude_mcp_servers() {
  if ! command_exists claude; then
    print_warning "claude CLI not found; skipping MCP server registration"
    return
  fi

  add_mcp() {
    local name="$1"
    shift
    if claude mcp get "$name" &>/dev/null; then
      print_info "MCP server '$name' already registered"
    else
      print_info "Registering MCP server '$name'..."
      claude mcp add --scope user "$name" "$@" ||
        print_warning "Failed to register MCP server '$name'"
    fi
  }

  add_mcp context7 -- npx -y @upstash/context7-mcp
  add_mcp codex -- codex mcp-server
  add_mcp serena -- uvx --from git+https://github.com/oraios/serena serena start-mcp-server --context ide-assistant

  # Bare `python` does not exist on stock Ubuntu or Homebrew installs
  # (only `python3`); resolve a concrete interpreter instead.
  local python_cmd
  python_cmd="$(command -v python3 || command -v python || true)"
  if [ -n "$python_cmd" ]; then
    add_mcp gemini-consultant -- "$python_cmd" "$HOME/.claude/mcp-servers/gemini-consultant/server.py"
  else
    print_warning "python3/python not found; skipping gemini-consultant MCP server registration"
  fi
}

# Install Node.js and npm
install_nodejs() {
  if ! command_exists node; then
    print_info "Installing Node.js..."
    if [[ "$OS" == "macos" ]]; then
      brew install node || print_warning "Failed to install Node.js (continuing)"
    elif [[ "$OS" == "ubuntu" ]]; then
      # Guarded: NodeSource being down must not abort the run. The `-` (read
      # from stdin) of the canonical `curl | sudo -E bash -` is dropped because
      # fetch_and_run passes a file path instead.
      if fetch_and_run https://deb.nodesource.com/setup_lts.x sudo -E bash; then
        sudo apt-get install -y nodejs ||
          print_warning "Failed to install Node.js (continuing)"
      else
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
  # after this one (gh, pyenv, docker, linters, oh-my-zsh, the symlinks).
  if command_exists npm; then
    mkdir -p "$HOME/.npm-global"
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
  # Test for the entry point, not the directory: create_symlinks runs first and
  # makes ~/.oh-my-zsh/custom/themes/ to land the theme, so a `-d` test on the
  # directory would report Oh My Zsh as already installed and skip it forever.
  if [ ! -f "$HOME/.oh-my-zsh/oh-my-zsh.sh" ]; then
    print_info "Installing Oh My Zsh..."
    # Download-then-run for the same truncation safety as Homebrew. `--unattended`
    # is a positional arg to the installer (keeps it from starting a shell or
    # running chsh -- install.sh handles the shell change separately), so it goes
    # after `--`, yielding `sh <tmp> --unattended`.
    #
    # Guarded like every other optional installer (install_uv / install_pyenv /
    # install_lazydocker / install_docker / install_nodejs). Unguarded, a
    # transient network failure here returned non-zero under `set -eo pipefail`
    # and killed main() outright -- taking vim-plug, tmux plugins, the Neovim
    # setup, the AI tools, the MCP registration, the theme symlink and the
    # shell change down with it, for a component none of them depend on.
    if fetch_and_run "https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/$OHMYZSH_INSTALL_REF/tools/install.sh" sh -- --unattended; then
      print_success "Oh My Zsh installed"
    else
      print_warning "Failed to install Oh My Zsh (continuing; the zsh theme and plugins below may be incomplete)"
    fi
  else
    print_success "Oh My Zsh already installed"
  fi

  # Self-heal: older installs symlinked $HOME/.oh-my-zsh/custom straight to
  # $DOTFILES_DIR/.oh-my-zsh/custom, which only tracks themes/ (no plugins/).
  # That destroyed cloned plugins on the first run and, on a second run,
  # cloned new plugins THROUGH the symlink into the dotfiles git checkout.
  # Ensure custom/ is a real directory before cloning anything into it.
  if [ -L "$HOME/.oh-my-zsh/custom" ]; then
    print_warning "Migrating $HOME/.oh-my-zsh/custom from a symlink to a real directory"
    rm -f "$HOME/.oh-my-zsh/custom"
  fi
  mkdir -p "$HOME/.oh-my-zsh/custom"

  # Install zsh plugins
  print_info "Installing zsh plugins..."

  # Guarded like the tpm clone: a failed plugin clone must not abort the run.
  # zsh-autosuggestions
  if [ ! -d "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" ]; then
    git clone https://github.com/zsh-users/zsh-autosuggestions "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" ||
      print_warning "Failed to clone zsh-autosuggestions (continuing)"
  fi

  # zsh-syntax-highlighting
  if [ ! -d "$HOME/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting" ]; then
    git clone https://github.com/zsh-users/zsh-syntax-highlighting "$HOME/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting" ||
      print_warning "Failed to clone zsh-syntax-highlighting (continuing)"
  fi

  print_success "zsh plugins installed"
}

# Link the Oh My Zsh custom theme(s) tracked in the repo. Split out of
# create_symlinks and called from main() only AFTER install_oh_my_zsh: this
# used to run inside create_symlinks and `mkdir -p` the themes directory,
# which on a genuinely fresh machine created $HOME/.oh-my-zsh before Oh My
# Zsh's own installer ran -- and that installer refuses to run when $ZSH
# already exists. Running this afterward means $HOME/.oh-my-zsh does not
# exist yet when the official installer runs on a true first install.
#
# Depends on link_entry, which is now a top-level function (it used to be
# nested inside create_symlinks, making this call order load-bearing).
link_oh_my_zsh_theme() {
  # Oh My Zsh custom: keep a REAL directory (install_oh_my_zsh clones plugins
  # into custom/plugins/) and symlink only the theme file(s) tracked in the
  # repo. Symlinking the whole custom/ dir would destroy cloned plugins on
  # the first run and, on a rerun, clone new plugins straight into the
  # dotfiles git checkout (see the self-heal in install_oh_my_zsh).
  if [ -L "$HOME/.oh-my-zsh/custom" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      print_info "[DRY-RUN] would replace the ~/.oh-my-zsh/custom symlink with a real directory"
    else
      rm -f "$HOME/.oh-my-zsh/custom"
    fi
  fi
  [ "$DRY_RUN" -eq 1 ] || mkdir -p "$HOME/.oh-my-zsh/custom/themes"
  for theme in "$DOTFILES_DIR"/.oh-my-zsh/custom/themes/*; do
    [ -e "$theme" ] || continue
    link_entry "$theme" "$HOME/.oh-my-zsh/custom/themes/$(basename "$theme")"
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
    curl -fLo "$HOME/.vim/autoload/plug.vim" --create-dirs \
      "https://raw.githubusercontent.com/junegunn/vim-plug/$VIM_PLUG_REF/plug.vim" ||
      print_warning "Failed to download vim-plug (continuing)"
  fi

  if [ -f "$HOME/.vim/autoload/plug.vim" ]; then
    print_success "vim-plug installed"
  fi
}

# Create symbolic links
# --- create_symlinks and its steps -------------------------------------------
# create_symlinks was a single ~320-line function mixing dotfile linking, git
# credential/identity rendering, and per-tool (VS Code / Claude / Codex /
# Gemini / tmux) wiring. It is now an orchestrator over the steps below; each
# step keeps the comments that explain its own decisions.
#
# backup_if_real and link_entry used to be nested INSIDE create_symlinks. Bash
# promotes a nested definition to global once the outer function first runs, so
# the steps would still have resolved them -- but only as an invisible side
# effect of call order. Hoisting makes the dependency explicit.
#
# Both read `backup_dir`, which create_symlinks assigns before invoking any
# step. It is deliberately global (not `local`) for exactly that reason.

# Helper: backup a path if it exists as a real file/dir (not a symlink)
backup_if_real() {
  local target="$1"
  # A symlink (even a broken one) is ours to replace, never worth backing up.
  if [ -L "$target" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      print_info "[DRY-RUN] would replace existing symlink $target"
      return 0
    fi
    rm -f "$target"
    return 0
  fi
  # Nothing there: return success (a bare `return` would propagate the failed
  # test's exit status and abort the caller under `set -e`).
  [ -e "$target" ] || return 0
  # Preserve the path structure under $backup_dir. A flat, basename-only
  # backup silently overwrites files that share a basename across different
  # destinations (settings.json / keybindings.json live under Code/ and
  # .claude/), so all but the last one moved would be lost.
  local rel dest_parent
  case "$target" in
  "$HOME"/*) rel="${target#"$HOME"/}" ;;
  /*) rel="${target#/}" ;;
  *) rel="$target" ;;
  esac
  dest_parent="$backup_dir/$(dirname "$rel")"
  if [ "$DRY_RUN" -eq 1 ]; then
    print_info "[DRY-RUN] would back up $target -> $dest_parent/"
    return 0
  fi
  print_warning "Backing up existing $(basename "$target")"
  mkdir -p "$dest_parent"
  mv "$target" "$dest_parent/"
}

# Helper: symlink a repo entry into a destination directory, backing up reals
link_entry() {
  local src="$1"
  local dest="$2"
  if [ ! -e "$src" ]; then
    print_warning "Skipping missing source: $src"
    return
  fi
  backup_if_real "$dest"
  if [ "$DRY_RUN" -eq 1 ]; then
    print_info "[DRY-RUN] would link $dest -> $src"
    return
  fi
  ln -sf "$src" "$dest"
  print_success "Linked $(basename "$dest")"
}

_link_top_level_dotfiles() {
  # Top-level dotfiles
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
  if [ "$DRY_RUN" -eq 1 ]; then
    # Read-only preview of the same decisions the real branch makes below --
    # never prompt (dry-run must not block on input) and never write.
    print_info "[DRY-RUN] would render $HOME/.config/git/os.gitconfig (credential helper: $git_cred_helper)"
    if [ -e "$git_user_config" ]; then
      print_info "[DRY-RUN] would keep existing git identity ($git_user_config)"
    elif [ -n "$prior_git_name" ] && [ -n "$prior_git_email" ]; then
      print_info "[DRY-RUN] would render $git_user_config inheriting $prior_git_name <$prior_git_email>"
    else
      print_info "[DRY-RUN] would prompt for a git identity (interactive) or write a commented-out placeholder at $git_user_config"
    fi
  else
    mkdir -p "$HOME/.config/git"
    printf '[credential]\n\thelper = %s\n' "$git_cred_helper" \
      >"$HOME/.config/git/os.gitconfig"
    print_success "Rendered os.gitconfig (credential helper: $git_cred_helper)"

    if [ -e "$git_user_config" ]; then
      print_info "Keeping existing git identity ($git_user_config)"
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
        printf '[user]\n\tname = %s\n\temail = %s\n' "$git_name" "$git_email" \
          >"$git_user_config"
        print_success "Rendered user.gitconfig ($git_name <$git_email>)"
      else
        # Commented-out keys, not empty ones: an empty `name =` makes git report
        # a configured-but-blank identity instead of prompting the user to set it.
        printf '# Fill in before committing:\n#[user]\n#\tname = Your Name\n#\temail = you@example.com\n' \
          >"$git_user_config"
        print_warning "No git identity configured. Edit $git_user_config before committing."
      fi
    fi
  fi
}

_link_editor_configs() {
  # Directories to symlink
  [ "$DRY_RUN" -eq 1 ] || mkdir -p "$HOME/.config"

  # Neovim config (repo stores it at .config/nvim)
  link_entry "$DOTFILES_DIR/.config/nvim" "$HOME/.config/nvim"

  # VS Code: reads user settings from an OS-specific location (not
  # $HOME/.config on macOS). Symlink individual files (not the whole User/
  # dir) so editor runtime state (globalStorage, workspaceStorage, etc.)
  # never ends up in the repo.
  local vscode_user_dir
  if [[ "$OS" == "macos" ]]; then
    vscode_user_dir="$HOME/Library/Application Support/Code/User"
  else
    vscode_user_dir="$HOME/.config/Code/User"
  fi
  [ "$DRY_RUN" -eq 1 ] || mkdir -p "$vscode_user_dir"

  local editor_config_files=(
    "settings.json"
    "keybindings.json"
  )
  for entry in "${editor_config_files[@]}"; do
    link_entry "$DOTFILES_DIR/.config/Code/User/$entry" "$vscode_user_dir/$entry"
  done
}

_link_claude_config() {
  # Claude Code config: symlink individual entries so CLI runtime data
  # (projects/, sessions/, history.jsonl, backups/, etc.) stays out of the repo.
  [ "$DRY_RUN" -eq 1 ] || mkdir -p "$HOME/.claude"
  local claude_entries=(
    "CLAUDE.md"
    "settings.json"
    "statusline-command.sh"
    "agents"
    "commands"
    "hooks"
    "mcp-servers"
    "rules"
    "skills"
  )
  for entry in "${claude_entries[@]}"; do
    [ -e "$DOTFILES_DIR/.claude/$entry" ] &&
      link_entry "$DOTFILES_DIR/.claude/$entry" "$HOME/.claude/$entry"
  done
}

_link_codex_config() {
  # Codex config. Every entry is symlinked, including the directories Codex
  # scans for itself (skills/, agents/). hooks.json is the sole exception:
  # Codex does NOT expand ~ or $HOME inside it, so it is install-time RENDERED
  # from hooks.json.template (see below).
  #
  # The shared skill set is defined in the repo tree, not here: .codex/skills
  # holds relative symlinks into .claude/skills (same convention as the
  # .codex/hooks/_*.sh helpers). Share or drop a skill by adding or removing a
  # link there.
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
  [ "$DRY_RUN" -eq 1 ] || mkdir -p "$HOME/.codex"

  local codex_link_entries=(
    "AGENTS.md"
    "hooks"
    "agents"
    "skills"
  )
  for entry in "${codex_link_entries[@]}"; do
    [ -e "$DOTFILES_DIR/.codex/$entry" ] &&
      link_entry "$DOTFILES_DIR/.codex/$entry" "$HOME/.codex/$entry"
  done

  # config.toml is deliberately NOT symlinked. Codex owns this file at runtime:
  # `codex mcp add` writes mcp_servers into it -- Authorization headers and all
  # -- and Codex accumulates projects/, plugins/ and desktop state there too. A
  # symlink would land every bit of that in the checkout, one `git add` away
  # from committing a token. This is the same trap that once cloned zsh plugins
  # through ~/.oh-my-zsh/custom into the repo (see install_oh_my_zsh).
  #
  # Older installs did link it, so replace such a link with a real file.
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
    if [ ! -e "$HOME/.codex/config.toml" ]; then
      if [ "$DRY_RUN" -eq 1 ]; then
        print_info "[DRY-RUN] would seed $HOME/.codex/config.toml from template"
      else
        cp "$DOTFILES_DIR/.codex/config.toml.template" "$HOME/.codex/config.toml"
        print_success "Seeded config.toml from template"
      fi
    else
      print_info "Keeping existing config.toml (Codex owns it; baseline lives in .codex/config.toml.template)"
    fi
  fi

  # hooks.json: render from the template, substituting the placeholder for
  # this machine's real $HOME (Codex does not expand ~ or $HOME itself).
  if [ -f "$DOTFILES_DIR/.codex/hooks.json.template" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      # Read-only preview of the same diff check the real branch below
      # performs -- this used to unconditionally claim "would render" even
      # when the rendered output is byte-identical to what's already there.
      local dry_rendered_tmp
      dry_rendered_tmp="$(mktemp)"
      sed "s|__HOME__|$HOME|g" "$DOTFILES_DIR/.codex/hooks.json.template" \
        >"$dry_rendered_tmp"
      if [ ! -f "$HOME/.codex/hooks.json" ] ||
        ! cmp -s "$dry_rendered_tmp" "$HOME/.codex/hooks.json"; then
        print_info "[DRY-RUN] would render $HOME/.codex/hooks.json from template (resolving \$HOME)"
      else
        print_info "[DRY-RUN] $HOME/.codex/hooks.json already up to date; skipping render"
      fi
      rm -f "$dry_rendered_tmp"
    else
      local rendered_tmp
      rendered_tmp="$(mktemp)"
      sed "s|__HOME__|$HOME|g" "$DOTFILES_DIR/.codex/hooks.json.template" \
        >"$rendered_tmp"
      # Only replace (and back up) when the rendered result actually changed, so
      # re-runs don't move an identical hooks.json into a fresh backup dir.
      if [ ! -f "$HOME/.codex/hooks.json" ] ||
        ! cmp -s "$rendered_tmp" "$HOME/.codex/hooks.json"; then
        backup_if_real "$HOME/.codex/hooks.json"
        mv "$rendered_tmp" "$HOME/.codex/hooks.json"
        print_success "Rendered hooks.json"
      else
        rm -f "$rendered_tmp"
      fi
    fi
  fi
}

_link_gemini_config() {
  # Gemini config: symlink individual entries
  [ "$DRY_RUN" -eq 1 ] || mkdir -p "$HOME/.gemini"
  local gemini_entries=(
    "GEMINI.md"
    "settings.json"
  )
  for entry in "${gemini_entries[@]}"; do
    [ -e "$DOTFILES_DIR/.gemini/$entry" ] &&
      link_entry "$DOTFILES_DIR/.gemini/$entry" "$HOME/.gemini/$entry"
  done
}

_link_tmux_helper() {
  # tmux helper script: .tmux.conf `bind S` invokes ~/.tmux/tmux_send_to_all_except_nvim.sh
  [ "$DRY_RUN" -eq 1 ] || mkdir -p "$HOME/.tmux"
  link_entry "$DOTFILES_DIR/scripts/tmux_send_to_all_except_nvim.sh" "$HOME/.tmux/tmux_send_to_all_except_nvim.sh"
  [ "$DRY_RUN" -eq 1 ] || chmod +x "$DOTFILES_DIR/scripts/tmux_send_to_all_except_nvim.sh" 2>/dev/null || true
}

create_symlinks() {
  print_info "Creating symbolic links..."

  # Read whatever identity git resolves right now, BEFORE the .gitconfig link
  # below replaces it. An upgrade from a real ~/.gitconfig that carried [user]
  # keeps its name/email this way instead of silently losing it.
  local prior_git_name prior_git_email
  prior_git_name="$(git config --global user.name 2>/dev/null || true)"
  prior_git_email="$(git config --global user.email 2>/dev/null || true)"

  # Backup existing files. In dry-run nothing is moved, so the dir is never
  # created (and the empty-dir cleanup at the end is skipped to match).
  backup_dir="$HOME/.dotfiles_backup_$(date +%Y%m%d_%H%M%S)"
  [ "$DRY_RUN" -eq 1 ] || mkdir -p "$backup_dir"

  _link_top_level_dotfiles
  _render_git_local_config "$prior_git_name" "$prior_git_email"
  _link_editor_configs
  _link_claude_config
  _link_codex_config
  _link_gemini_config

  # Oh My Zsh custom theme: NOT handled here. This used to
  # `mkdir -p "$HOME/.oh-my-zsh/custom/themes"` at this point, which on a
  # genuinely fresh machine created $HOME/.oh-my-zsh as a real directory
  # before Oh My Zsh's own installer ever ran -- and Oh My Zsh's official
  # installer refuses to run when $ZSH already exists, so a true first
  # install aborted at install_oh_my_zsh's unguarded fetch_and_run under
  # set -eo pipefail. See link_oh_my_zsh_theme, called from main() only
  # after install_oh_my_zsh.

  _link_tmux_helper

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
  # Guarded like every other installer here: `|| true` used to swallow vim
  # being absent (exit 127) the same as a real PlugInstall failure, then
  # print_success ran unconditionally either way.
  if ! command_exists vim; then
    print_warning "vim not found; skipping Vim plugin installation"
    return
  fi
  if vim +PlugInstall +qall; then
    print_success "Vim plugins installed"
  else
    print_warning "vim +PlugInstall failed (continuing)"
  fi
}

# Setup Neovim
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

    # Claude Code (official npm distribution)
    if ! command_exists claude; then
      npm install -g @anthropic-ai/claude-code 2>/dev/null ||
        print_warning "Failed to install Claude Code"
    else
      print_info "Claude Code already installed"
    fi

    # Install npm-based tools
    npm install -g @openai/codex 2>/dev/null || print_warning "Failed to install Codex"
    npm install -g @google/gemini-cli 2>/dev/null || print_warning "Failed to install Gemini CLI"

    # GitHub Copilot CLI (standalone `copilot` command; used by
    # update_ai_tools.sh and the `cop` alias in .zshrc).
    npm install -g @github/copilot 2>/dev/null || print_warning "Failed to install Copilot CLI"

    print_success "AI tools installation attempted (check warnings above)"
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
        npm install -g "$tool" 2>/dev/null || print_warning "Failed to install $tool"
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
    for tool in "${pip_tools[@]}"; do
      if ! command_exists "$tool"; then
        print_info "Installing $tool via pip..."
        $pip_cmd install --user "$tool" 2>/dev/null || print_warning "Failed to install $tool"
      else
        print_info "$tool already installed"
      fi
    done
  else
    print_warning "pip not found, skipping Python tools"
  fi

  # --- Platform-specific tools ---
  case "$OS" in
  macos)
    # Note: clang-format ships with llvm (brew has no standalone formula).
    # checkstyle has no brew formula; install manually from
    # https://checkstyle.sourceforge.io/ if needed.
    local brew_tools=(
      shellcheck   # Shell script linter
      shfmt        # Shell script formatter
      cppcheck     # C/C++ linter
      llvm         # Provides clang-format for C/C++
      staticcheck  # Go advanced linter
      php-cs-fixer # PHP formatter
    )
    for tool in "${brew_tools[@]}"; do
      if ! brew list "$tool" &>/dev/null; then
        print_info "Installing $tool via brew..."
        brew install "$tool" 2>/dev/null || print_warning "Failed to install $tool"
      else
        print_info "$tool already installed"
      fi
    done

    # google-java-format (brew cask or manual)
    if ! command_exists google-java-format; then
      brew install google-java-format 2>/dev/null || print_warning "Failed to install google-java-format"
    fi

    # Go tools (requires go)
    if command_exists go; then
      if ! command_exists goimports; then
        print_info "Installing goimports..."
        go install golang.org/x/tools/cmd/goimports@latest 2>/dev/null || print_warning "Failed to install goimports"
      fi
    fi

    # Ruby tools
    if command_exists gem; then
      if ! command_exists rubocop; then
        print_info "Installing rubocop via gem..."
        gem install rubocop 2>/dev/null || print_warning "Failed to install rubocop"
      fi
    fi

    # PHP tools
    if command_exists php && ! command_exists phpstan; then
      if command_exists composer; then
        print_info "Installing phpstan via composer..."
        composer global require phpstan/phpstan 2>/dev/null || print_warning "Failed to install phpstan"
      fi
    fi
    ;;

  ubuntu)
    print_info "Installing APT-based linter/formatter packages..."
    sudo apt-get install -y \
      shellcheck \
      cppcheck \
      clang-format \
      2>/dev/null || print_warning "Some APT packages failed to install"

    # shfmt (snap or go install)
    if ! command_exists shfmt; then
      if command_exists snap; then
        print_info "Installing shfmt via snap..."
        sudo snap install shfmt 2>/dev/null || print_warning "Failed to install shfmt via snap"
      elif command_exists go; then
        print_info "Installing shfmt via go install..."
        go install mvdan.cc/sh/v3/cmd/shfmt@latest 2>/dev/null || print_warning "Failed to install shfmt"
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
        go install honnef.co/go/tools/cmd/staticcheck@latest 2>/dev/null || print_warning "Failed to install staticcheck"
      fi
      if ! command_exists goimports; then
        print_info "Installing goimports..."
        go install golang.org/x/tools/cmd/goimports@latest 2>/dev/null || print_warning "Failed to install goimports"
      fi
    fi

    # Ruby tools
    if command_exists gem; then
      if ! command_exists rubocop; then
        print_info "Installing rubocop via gem..."
        gem install rubocop 2>/dev/null || print_warning "Failed to install rubocop"
      fi
    fi

    # PHP tools
    if command_exists php && ! command_exists phpstan; then
      if command_exists composer; then
        print_info "Installing phpstan via composer..."
        composer global require phpstan/phpstan 2>/dev/null || print_warning "Failed to install phpstan"
      fi
    fi
    if ! command_exists php-cs-fixer; then
      if command_exists composer; then
        print_info "Installing php-cs-fixer via composer..."
        composer global require friendsofphp/php-cs-fixer 2>/dev/null || print_warning "Failed to install php-cs-fixer"
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
          scoop install "$tool" 2>/dev/null || print_warning "Failed to install $tool via scoop"
        fi
      done
    fi
    ;;
  esac

  print_success "Linters and formatters installation completed"
}

# Change default shell to zsh
change_shell() {
  # Guard first: when zsh isn't installed, `$(which zsh)` resolves to "",
  # `[ "$SHELL" != "" ]` is true, and the branch below used to run
  # `chsh -s ""` unconditionally.
  if ! command_exists zsh; then
    print_warning "zsh not found; skipping shell change. Install zsh, then run: chsh -s \$(which zsh)"
    return 0
  fi
  if [ "$SHELL" != "$(which zsh)" ]; then
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
        print_warning "chsh failed; add $(command -v zsh) to /etc/shells and re-run chsh"
      fi
    else
      print_warning "chsh command not found. Please change shell manually: chsh -s $(which zsh)"
    fi
  else
    print_success "Default shell is already zsh"
  fi
}

# Install OS-specific packages. Extracted from main()'s inline case so the
# dispatch is unit-testable on its own. A bash `case` with no matching arm
# is a silent no-op: a non-Debian Linux (OS="linux", set by detect_os when
# /etc/debian_version is absent) used to fall through main()'s case with no
# warning and no packages installed at all.
install_os_packages() {
  case "$OS" in
  macos)
    install_homebrew
    install_brew_packages
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

# Main installation flow
main() {
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
  # on a flaky network or a renamed formula; when it ran last, one such failure
  # left the machine with no dotfiles linked at all.
  create_symlinks

  # Package / tool installation is not simulated in dry-run: these steps are
  # network-bound and already externally idempotent (each guards on
  # command_exists / brew list / dir tests). Announce and skip them; the
  # dry-run value is in the symlink/backup preview above, not here.
  if [ "$DRY_RUN" -eq 1 ]; then
    print_info "[DRY-RUN] Skipping package and tool installation (not simulated)."
    print_info "[DRY-RUN]   Would install: OS packages (Homebrew / APT), WezTerm, fonts,"
    print_info "[DRY-RUN]   Node.js, gh, pyenv, uv, glow, Docker, lazydocker, tree-sitter,"
    print_info "[DRY-RUN]   MCP deps, linters/formatters, Oh My Zsh, vim-plug, tmux plugins;"
    print_info "[DRY-RUN]   set up Neovim; install AI tools; register Claude MCP servers."
  else
    # Platform-specific package installation
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
  print_success "Installation completed!"
  echo
  print_info "Next steps:"
  echo "  1. Restart your terminal"
  echo "  2. Open Neovim to complete lazy.nvim setup: nvim"
  echo "  3. In tmux, press prefix + I to install plugins (tpm)"
  echo "  4. Install AI tools if needed (see README.md)"
  echo "  5. Customize configurations as needed"
  echo
  print_info "Secrets / credentials still required (cannot be installed):"
  echo "  - GEMINI_API_KEY : export in your shell for the gemini-api bash-review hook"
  echo "                     and the gemini-consultant MCP server"
  echo "  - GITHUB_ACCESS_TOKEN : export in your shell for the GitHub MCP server (.gemini/settings.json resolves \${GITHUB_ACCESS_TOKEN})"
  echo "  - gh auth login   : authenticate GitHub CLI (used by .gitconfig credential helper)"
  echo
  print_info "Utility scripts:"
  echo "  - ./scripts/update_ai_tools.sh : Update all AI tools"
  echo "  - ./scripts/tmux_send_to_all_except_nvim.sh : Send commands to tmux panes"
  echo
}

# Run main function only when executed directly (not when sourced by tests).
# BASH_SOURCE[0] is empty when the script is piped into bash; run main there
# too so the DOTFILES_DIR guard can print its clone-the-repo error.
if [[ -z "${BASH_SOURCE[0]:-}" ]] || [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
