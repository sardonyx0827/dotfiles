#!/usr/bin/env bash

# Dotfiles Installation Script
# Supports: macOS, Ubuntu/Debian, Windows (WSL/Git Bash)

set -e

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
  elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ -n "$WSL_DISTRO_NAME" ]]; then
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

# Install Homebrew (macOS)
install_homebrew() {
  if ! command_exists brew; then
    print_info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

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
  if [[ "$OS" == "macos" ]]; then
    if ! brew list --cask wezterm &>/dev/null; then
      print_info "Installing WezTerm..."
      brew install --cask wezterm
      print_success "WezTerm installed"
    else
      print_success "WezTerm already installed"
    fi
  elif [[ "$OS" == "ubuntu" ]]; then
    if ! command_exists wezterm; then
      print_info "Installing WezTerm..."
      curl -fsSL https://apt.fury.io/wez/gpg.key | sudo gpg --yes --dearmor -o /usr/share/keyrings/wezterm-fury.gpg
      echo 'deb [signed-by=/usr/share/keyrings/wezterm-fury.gpg] https://apt.fury.io/wez/ * *' | sudo tee /etc/apt/sources.list.d/wezterm.list
      sudo apt-get update
      sudo apt-get install -y wezterm
      print_success "WezTerm installed"
    else
      print_success "WezTerm already installed"
    fi
  fi
}

# Install fonts
install_fonts() {
  if [[ "$OS" == "macos" ]]; then
    print_info "Installing fonts..."
    # homebrew/cask-fonts was deprecated in 2024 and folded into homebrew/cask.
    brew install --cask font-ubuntu-mono
    brew install --cask font-hack-nerd-font
    print_success "Fonts installed"
  elif [[ "$OS" == "ubuntu" ]]; then
    print_info "Installing fonts..."
    sudo apt-get install -y fonts-ubuntu fonts-hack-ttf
    print_success "Fonts installed"
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
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg |
      sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
    sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" |
      sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
    sudo apt-get update
    sudo apt-get install -y gh || print_warning "Failed to install gh"
  fi
  command_exists gh && print_success "gh installed"
}

# Install uv / uvx (Astral) — required to launch the serena MCP server.
install_uv() {
  if command_exists uv; then
    print_success "uv already installed"
    return
  fi
  print_info "Installing uv (Astral)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null ||
    print_warning "Failed to install uv"
  # uv installs to ~/.local/bin; make it visible for the rest of this script.
  export PATH="$HOME/.local/bin:$PATH"
  command_exists uv && print_success "uv installed"
}

# Install pyenv (macOS handled by brew packages; this covers Ubuntu).
install_pyenv() {
  if command_exists pyenv || [ -d "$HOME/.pyenv" ]; then
    print_success "pyenv already installed"
    return
  fi
  if [[ "$OS" == "ubuntu" ]]; then
    print_info "Installing pyenv..."
    curl -fsSL https://pyenv.run | bash 2>/dev/null ||
      print_warning "Failed to install pyenv"
    command_exists pyenv || [ -d "$HOME/.pyenv" ] && print_success "pyenv installed"
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
  command_exists glow && print_success "glow installed"
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
    curl -fsSL https://raw.githubusercontent.com/jesseduffield/lazydocker/master/scripts/install_update_linux.sh |
      bash 2>/dev/null || print_warning "Failed to install lazydocker"
  fi
  command_exists lazydocker && print_success "lazydocker installed"
}

# Install Docker — MCP_DOCKER gateway and the dsollama/deollama aliases.
install_docker() {
  if command_exists docker; then
    print_success "Docker already installed"
    return
  fi
  print_info "Installing Docker..."
  if [[ "$OS" == "macos" ]]; then
    brew install --cask docker || print_warning "Failed to install Docker Desktop"
    print_info "Launch Docker Desktop once and enable the MCP Toolkit for the MCP_DOCKER gateway."
  elif [[ "$OS" == "ubuntu" ]]; then
    curl -fsSL https://get.docker.com | sudo sh 2>/dev/null ||
      print_warning "Failed to install Docker engine"
    sudo usermod -aG docker "$USER" 2>/dev/null || true
    print_info "Log out/in (or 'newgrp docker') for group membership to take effect."
  fi
  command_exists docker && print_success "Docker installed"
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

  add_mcp github --transport http https://api.githubcopilot.com/mcp/
  add_mcp context7 -- npx -y @upstash/context7-mcp
  add_mcp codex -- codex mcp-server
  add_mcp serena -- uvx --from git+https://github.com/oraios/serena serena start-mcp-server --context ide-assistant
  add_mcp MCP_DOCKER -- docker mcp gateway run
  add_mcp drawio -- npx -y @drawio/mcp
  add_mcp gemini-consultant -- python "$HOME/.claude/mcp-servers/gemini-consultant/server.py"
}

# Install Node.js and npm
install_nodejs() {
  if ! command_exists node; then
    print_info "Installing Node.js..."
    if [[ "$OS" == "macos" ]]; then
      brew install node
    elif [[ "$OS" == "ubuntu" ]]; then
      curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
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
    sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
    print_success "Oh My Zsh installed"
  else
    print_success "Oh My Zsh already installed"
  fi

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
    if [ -e "$target" ] && [ ! -L "$target" ]; then
      print_warning "Backing up existing $(basename "$target")"
      mv "$target" "$backup_dir/"
    elif [ -L "$target" ]; then
      rm -f "$target"
    fi
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

  # Directories to symlink
  mkdir -p "$HOME/.config"

  # Neovim config (repo stores it at .config/nvim)
  link_entry "$DOTFILES_DIR/.config/nvim" "$HOME/.config/nvim"

  # Claude Code config: symlink individual entries so CLI runtime data
  # (projects/, sessions/, history.jsonl, backups/, etc.) stays out of the repo.
  mkdir -p "$HOME/.claude"
  local claude_entries=(
    "CLAUDE.md"
    "settings.json"
    "statusline-command.sh"
    "agents"
    "archive"
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

  # Codex config.
  # IMPORTANT: Codex IGNORES symlinks for its directory-scanned config
  # (skills/, agents/) — both symlinked dirs and symlinked files are
  # skipped (openai/codex#3637, #4383, #5040, #16452). Those MUST be real files,
  # so they are COPIED. Single files opened by an exact path (AGENTS.md,
  # config.toml) and the hooks dir (scripts run by absolute path) follow
  # symlinks fine, so they stay symlinked. hooks.json is neither: Codex does
  # NOT expand ~ or $HOME inside it, so it is install-time RENDERED from
  # hooks.json.template instead of symlinked (see below).
  mkdir -p "$HOME/.codex"

  # Helper: copy a repo entry into a destination, backing up reals/symlinks.
  copy_entry() {
    local src="$1"
    local dest="$2"
    [ -e "$src" ] || return
    backup_if_real "$dest" # moves a real path to backup, or removes a symlink
    rm -rf "$dest"         # clear anything backup_if_real left (defensive)
    cp -R "$src" "$dest"
    print_success "Copied $(basename "$dest")"
  }

  local codex_link_entries=(
    "AGENTS.md"
    "config.toml"
    "hooks"
  )
  for entry in "${codex_link_entries[@]}"; do
    [ -e "$DOTFILES_DIR/.codex/$entry" ] &&
      link_entry "$DOTFILES_DIR/.codex/$entry" "$HOME/.codex/$entry"
  done

  # hooks.json: render from the template, substituting the placeholder for
  # this machine's real $HOME (Codex does not expand ~ or $HOME itself).
  if [ -f "$DOTFILES_DIR/.codex/hooks.json.template" ]; then
    backup_if_real "$HOME/.codex/hooks.json"
    sed "s|__HOME__|$HOME|g" "$DOTFILES_DIR/.codex/hooks.json.template" \
      >"$HOME/.codex/hooks.json"
    print_success "Rendered hooks.json"
  fi

  # Scanned by Codex -> must be real files (copied, not symlinked)
  local codex_copy_entries=(
    "agents"
  )
  for entry in "${codex_copy_entries[@]}"; do
    copy_entry "$DOTFILES_DIR/.codex/$entry" "$HOME/.codex/$entry"
  done

  # Codex skills: share curated, runtime-agnostic skills from .claude/skills.
  # Copied (not symlinked) so Codex's skill scan picks them up; Codex's managed
  # .system skills are left untouched.
  mkdir -p "$HOME/.codex/skills"
  local codex_skills=(
    "coding-standards"
    "backend-patterns"
    "frontend-patterns"
    "golang-patterns"
    "golang-testing"
    "typescript-testing"
    "tdd-workflow"
    "security-review"
    "docker-patterns"
    "github-actions-ci"
    "postgres-patterns"
    "clickhouse-io"
    "release-workflow"
    "migration-playbook"
    "python-scripting-patterns"
    "shell-scripting-patterns"
    "verification-loop"
    "eval-harness"
    "request-harness"
  )
  for skill in "${codex_skills[@]}"; do
    copy_entry "$DOTFILES_DIR/.claude/skills/$skill" "$HOME/.codex/skills/$skill"
  done

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

  # Oh My Zsh custom: remove existing (real dir or stale symlink) before linking
  # to prevent `ln -sf` from creating a symlink *inside* the existing directory.
  if [ -e "$HOME/.oh-my-zsh/custom" ] || [ -L "$HOME/.oh-my-zsh/custom" ]; then
    backup_if_real "$HOME/.oh-my-zsh/custom"
  fi
  ln -sf "$DOTFILES_DIR/.oh-my-zsh/custom" "$HOME/.oh-my-zsh/custom"
  print_success "Linked Oh My Zsh custom"

  # tmux helper script: .tmux.conf `bind S` invokes ~/.tmux/tmux_send_to_all_except_nvim.sh
  mkdir -p "$HOME/.tmux"
  link_entry "$DOTFILES_DIR/tmux_send_to_all_except_nvim.sh" "$HOME/.tmux/tmux_send_to_all_except_nvim.sh"
  chmod +x "$DOTFILES_DIR/tmux_send_to_all_except_nvim.sh" 2>/dev/null || true

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
  read -p "Do you want to install AI development tools? (y/n): " -n 1 -r
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

    # GitHub Copilot CLI is distributed as a `gh` extension, not an npm package.
    if command_exists gh; then
      gh extension install github/gh-copilot 2>/dev/null ||
        print_info "gh-copilot already installed or install skipped"
    else
      print_warning "gh CLI not found; install it to use GitHub Copilot CLI (gh extension install github/gh-copilot)"
    fi

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
      chsh -s "$(which zsh)"
      print_success "Default shell changed to zsh (restart terminal to apply)"
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
  echo "  - GitHub MCP token: replace the <placeholder> token in .gemini/.claude MCP config"
  echo "  - gh auth login   : authenticate GitHub CLI (used by .gitconfig credential helper)"
  echo
  print_info "Utility scripts:"
  echo "  - ./update_ai_tools.sh : Update all AI tools"
  echo "  - ./tmux_send_to_all_except_nvim.sh : Send commands to tmux panes"
  echo
}

# Run main function only when executed directly (not when sourced by tests).
# BASH_SOURCE[0] is empty when the script is piped into bash; run main there
# too so the DOTFILES_DIR guard can print its clone-the-repo error.
if [[ -z "${BASH_SOURCE[0]:-}" ]] || [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
