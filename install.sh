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
    fetch_and_run https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh /bin/bash

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
  sudo apt-get update

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
    liblzma-dev

  # On Debian/Ubuntu the binaries are named `batcat` and `fdfind`, but the
  # configs (.zshrc vf(), nvim telescope) invoke `bat` and `fd`. Provide
  # PATH-visible aliases so those code paths resolve.
  mkdir -p "$HOME/.local/bin"
  command_exists batcat && ln -sf "$(command -v batcat)" "$HOME/.local/bin/bat"
  command_exists fdfind && ln -sf "$(command -v fdfind)" "$HOME/.local/bin/fd"

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
      brew install "$package"
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
    fetch_and_run https://raw.githubusercontent.com/jesseduffield/lazydocker/master/scripts/install_update_linux.sh bash ||
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
      brew install node
    elif [[ "$OS" == "ubuntu" ]]; then
      # Bare (unguarded) call: a NodeSource setup failure aborts the install
      # under set -e, matching the original `curl | sudo -E bash -`. The `-`
      # (read from stdin) is dropped because fetch_and_run passes a file path.
      fetch_and_run https://deb.nodesource.com/setup_lts.x sudo -E bash
      sudo apt-get install -y nodejs
    fi
    print_success "Node.js installed"
  else
    print_success "Node.js already installed ($(node --version))"
  fi

  # Setup npm global directory
  mkdir -p "$HOME/.npm-global"
  npm config set prefix "$HOME/.npm-global"
  # Ensure the npm-global bin is visible to the rest of this script so subsequent
  # `command_exists` checks and direct invocations of globally installed tools
  # (prettier, eslint, claude, etc.) resolve correctly. .zshrc already exports
  # this for interactive shells.
  export PATH="$HOME/.npm-global/bin:$PATH"
  print_success "npm global directory configured"
}

# Install Oh My Zsh
install_oh_my_zsh() {
  if [ ! -d "$HOME/.oh-my-zsh" ]; then
    print_info "Installing Oh My Zsh..."
    # Download-then-run for the same truncation safety as Homebrew. `--unattended`
    # is a positional arg to the installer (keeps it from starting a shell or
    # running chsh -- install.sh handles the shell change separately), so it goes
    # after `--`, yielding `sh <tmp> --unattended`.
    fetch_and_run https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh sh -- --unattended
    print_success "Oh My Zsh installed"
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

  # zsh-autosuggestions
  if [ ! -d "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" ]; then
    git clone https://github.com/zsh-users/zsh-autosuggestions "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions"
  fi

  # zsh-syntax-highlighting
  if [ ! -d "$HOME/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting" ]; then
    git clone https://github.com/zsh-users/zsh-syntax-highlighting "$HOME/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting"
  fi

  print_success "zsh plugins installed"
}

# Install vim-plug
# Neovim is managed by lazy.nvim (see .config/nvim/), so vim-plug is only
# needed for classic Vim (.vimrc).
install_vim_plug() {
  print_info "Installing vim-plug..."

  if [ ! -f "$HOME/.vim/autoload/plug.vim" ]; then
    curl -fLo "$HOME/.vim/autoload/plug.vim" --create-dirs \
      https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
  fi

  print_success "vim-plug installed"
}

# Create symbolic links
create_symlinks() {
  print_info "Creating symbolic links..."

  # Backup existing files
  backup_dir="$HOME/.dotfiles_backup_$(date +%Y%m%d_%H%M%S)"
  mkdir -p "$backup_dir"

  # Helper: backup a path if it exists as a real file/dir (not a symlink)
  backup_if_real() {
    local target="$1"
    # A symlink (even a broken one) is ours to replace, never worth backing up.
    if [ -L "$target" ]; then
      rm -f "$target"
      return 0
    fi
    # Nothing there: return success (a bare `return` would propagate the failed
    # test's exit status and abort the caller under `set -e`).
    [ -e "$target" ] || return 0
    print_warning "Backing up existing $(basename "$target")"
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
    ln -sf "$src" "$dest"
    print_success "Linked $(basename "$dest")"
  }

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

  # OS 別の資格情報ヘルパーを ~/.config/git/os.gitconfig に生成する。
  # .gitconfig は [include] でこれを読み込む。macOS は osxkeychain、それ以外は
  # cache を使い、credential-osxkeychain が存在しない Linux/WSL で
  # 「is not a git command」警告が出るのを防ぐ。生成ファイルなのでバックアップ
  # 不要 (毎回上書きで冪等)。git は未生成でも include を黙って無視する。
  mkdir -p "$HOME/.config/git"
  local git_cred_helper="cache --timeout=3600"
  [[ "$OS" == "macos" ]] && git_cred_helper="osxkeychain"
  printf '[credential]\n\thelper = %s\n' "$git_cred_helper" \
    >"$HOME/.config/git/os.gitconfig"
  print_success "Rendered os.gitconfig (credential helper: $git_cred_helper)"

  # Directories to symlink
  mkdir -p "$HOME/.config"

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
  mkdir -p "$vscode_user_dir"

  local editor_config_files=(
    "settings.json"
    "keybindings.json"
  )
  for entry in "${editor_config_files[@]}"; do
    link_entry "$DOTFILES_DIR/.config/Code/User/$entry" "$vscode_user_dir/$entry"
  done

  # Claude Code config: symlink individual entries so CLI runtime data
  # (projects/, sessions/, history.jsonl, backups/, etc.) stays out of the repo.
  mkdir -p "$HOME/.claude"
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
  mkdir -p "$HOME/.codex"

  local codex_link_entries=(
    "AGENTS.md"
    "config.toml"
    "hooks"
    "agents"
    "skills"
  )
  for entry in "${codex_link_entries[@]}"; do
    [ -e "$DOTFILES_DIR/.codex/$entry" ] &&
      link_entry "$DOTFILES_DIR/.codex/$entry" "$HOME/.codex/$entry"
  done

  # hooks.json: render from the template, substituting the placeholder for
  # this machine's real $HOME (Codex does not expand ~ or $HOME itself).
  if [ -f "$DOTFILES_DIR/.codex/hooks.json.template" ]; then
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

  # Gemini config: symlink individual entries
  mkdir -p "$HOME/.gemini"
  local gemini_entries=(
    "GEMINI.md"
    "settings.json"
  )
  for entry in "${gemini_entries[@]}"; do
    [ -e "$DOTFILES_DIR/.gemini/$entry" ] &&
      link_entry "$DOTFILES_DIR/.gemini/$entry" "$HOME/.gemini/$entry"
  done

  # Oh My Zsh custom: keep a REAL directory (install_oh_my_zsh clones plugins
  # into custom/plugins/) and symlink only the theme file(s) tracked in the
  # repo. Symlinking the whole custom/ dir would destroy cloned plugins on
  # the first run and, on a rerun, clone new plugins straight into the
  # dotfiles git checkout (see the self-heal in install_oh_my_zsh).
  if [ -L "$HOME/.oh-my-zsh/custom" ]; then
    rm -f "$HOME/.oh-my-zsh/custom"
  fi
  mkdir -p "$HOME/.oh-my-zsh/custom/themes"
  for theme in "$DOTFILES_DIR"/.oh-my-zsh/custom/themes/*; do
    [ -e "$theme" ] || continue
    link_entry "$theme" "$HOME/.oh-my-zsh/custom/themes/$(basename "$theme")"
  done

  # tmux helper script: .tmux.conf `bind S` invokes ~/.tmux/tmux_send_to_all_except_nvim.sh
  mkdir -p "$HOME/.tmux"
  link_entry "$DOTFILES_DIR/scripts/tmux_send_to_all_except_nvim.sh" "$HOME/.tmux/tmux_send_to_all_except_nvim.sh"
  chmod +x "$DOTFILES_DIR/scripts/tmux_send_to_all_except_nvim.sh" 2>/dev/null || true

  if [ -n "$(ls -A "$backup_dir" 2>/dev/null)" ]; then
    print_info "Backup created at: $backup_dir"
  else
    rmdir "$backup_dir"
  fi
}

# Install Vim plugins
install_vim_plugins() {
  print_info "Installing Vim plugins..."
  vim +PlugInstall +qall || true
  print_success "Vim plugins installed"
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
      fi
    fi

    # staticcheck (go install)
    if command_exists go; then
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
  if [ "$SHELL" != "$(which zsh)" ]; then
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

# Main installation flow
main() {
  echo "  Dotfiles Installation Script"
  echo

  # Guard against a bad DOTFILES_DIR (e.g. script piped into bash instead of
  # run from a checkout) — otherwise create_symlinks would silently skip
  # every entry.
  if [ ! -e "$DOTFILES_DIR/.zshrc" ] || [ ! -d "$DOTFILES_DIR/.claude" ]; then
    print_error "Dotfiles repository not found at: $DOTFILES_DIR"
    print_error "Clone the repo and run install.sh from the checkout: git clone <repo> && cd dotfiles && ./install.sh"
    exit 1
  fi

  detect_os

  # Platform-specific package installation
  case "$OS" in
  macos)
    install_homebrew
    install_brew_packages
    ;;
  ubuntu)
    install_apt_packages
    ;;
  windows)
    print_warning "Windows detected. Please ensure Git Bash or WSL is properly configured."
    print_warning "Some features may require manual installation."
    ;;
  esac

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

  # Create symlinks
  create_symlinks

  # Setup editors
  install_vim_plugins
  setup_neovim

  # Optional AI tools
  install_ai_tools

  # Register MCP servers with Claude Code (after symlinks + AI tools)
  register_claude_mcp_servers

  # Change shell
  change_shell

  echo
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
