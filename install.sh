#!/usr/bin/env bash

#############################################
# Dotfiles Installation Script
# Supports: macOS, Ubuntu/Debian, Windows (WSL/Git Bash)
#############################################

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Dotfiles directory
DOTFILES_DIR="$HOME/dotfiles"

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
    if [ -f /etc/debian_version ]; then
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
    xsel

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

  # Codex config: symlink individual entries
  mkdir -p "$HOME/.codex"
  local codex_entries=(
    "AGENTS.md"
    "config.json"
    "config.toml"
    "agents"
  )
  for entry in "${codex_entries[@]}"; do
    [ -e "$DOTFILES_DIR/.codex/$entry" ] &&
      link_entry "$DOTFILES_DIR/.codex/$entry" "$HOME/.codex/$entry"
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
  echo "================================================"
  echo "  Dotfiles Installation Script"
  echo "================================================"
  echo

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
  install_linters_formatters
  install_oh_my_zsh
  install_vim_plug

  # Create symlinks
  create_symlinks

  # Setup editors
  install_vim_plugins
  setup_neovim

  # Optional AI tools
  install_ai_tools

  # Change shell
  change_shell

  echo
  echo "================================================"
  print_success "Installation completed!"
  echo "================================================"
  echo
  print_info "Next steps:"
  echo "  1. Restart your terminal"
  echo "  2. Open Neovim to complete lazy.nvim setup: nvim"
  echo "  3. Install AI tools if needed (see README.md)"
  echo "  4. Customize configurations as needed"
  echo
  print_info "Utility scripts:"
  echo "  - ./update_ai_tools.sh : Update all AI tools"
  echo "  - ./tmux_send_to_all_except_nvim.sh : Send commands to tmux panes"
  echo
}

# Run main function
main "$@"
